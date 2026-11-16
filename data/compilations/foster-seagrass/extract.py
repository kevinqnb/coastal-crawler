#!/usr/bin/env python3
"""Foster et al. seagrass biomass / production / structure dataset -> compilation-contract tables.

Foster, N., Gomis, E., Montemayor, D. et al. (2025). Global patterns and drivers of
seagrass biomass, net primary production and meadow structure [Dataset]. Dryad,
doi:10.5061/dryad.kprr4xhh1. Raw file (this directory):
``Dataset_ecological_biomass_complete.csv`` (a meta-analysis table: one row per
study x site x genus x year).

Grain: ONE FACT PER NON-BLANK SEAGRASS MEASUREMENT. A row holding k of the 11 measurement
columns becomes k facts. Values are carried exactly as reported, in the units of the
column header / the dataset README (asserted via the header text). Nothing is converted
or capped; no threshold is applied to any attribute.

Attributes (source column -> attribute, units_raw):
  Above/Below/Total.biomass..g.DW.m.2.        -> seagrass_biomass_above/_below/_total   g DW m-2
  Shoot.density..no..shoots.m.2.              -> seagrass_shoot_density                 shoots m-2
  Leaf.density..no..leaves.m.2.               -> seagrass_leaf_density                  leaves m-2
  Percent.cover....                           -> seagrass_percent_cover                 %  (kept as a percentage)
  Above/Below/Total/Shoot/Leaf.production.. . -> seagrass_production_above/_below/_total/_shoot/_leaf   g DW m-2 d-1

Measurement types (``compilation_vocab.ATTRIBUTES``; ``fact.measurement_type`` /
``subfield``): biomass above/below/total -> B2 above_ground_biomass / below_ground_biomass /
total_biomass; shoot density -> B3 shoot_or_stem_density; leaf density -> B3 leaf_density
(declared extension); percent cover -> B3 percent_cover; production above/below ->
B6 above_ground_npp / below_ground_npp; production total/shoot/leaf -> B6 total_npp /
shoot_production / leaf_production (declared extensions). Production is per DAY here
(``g DW m-2 d-1``) where the standard B6 lists per year: carried as reported. Standard labels:
habitat = Seagrass meadow (from the constant ``seagrass``), setting = estuary for
``Geotype`` Estuarine (Coastal -> blank), via ``compilation_label_maps.csv``.

NOT LOADED (deliberate): the 19 ``BO22_*`` columns are Bio-ORACLE gridded
model/satellite climatology looked up at each coordinate (2000-2014), not seagrass
measurements and not study results. They are asserted to be present and are listed in
``extract_report.json``; they create no facts. They can be added later as a
modelled-data attribute family.

Fail loud. The only exclusion is a row with none of the 11 measurement columns filled
(reason ``no_seagrass_measurement``, listed in ``skip_report.jsonl``). Row accounting is
asserted: source rows = rows with facts + excluded rows, and fact count = non-blank
measurement cells.

Standardisation / derived-field rules (the raw value is recoverable via
``fact.source_row_ref`` = ``<file>#data_row=<0-based>:<attribute>``):
  * Strings are whitespace-stripped (the file starts with a UTF-8 BOM, removed); the
    literal ``NA`` and blank become NULL (``NA`` only occurs in Year.of.sampling).
  * latitude/longitude are the source decimal degrees, unchanged (asserted to lie in
    [-90, 90] / [-180, 180]).
  * habitat_type = constant ``seagrass`` (every row is a seagrass record by the dataset's
    scope; not a per-row source label). geomorphic_setting = the source ``Geotype``
    label (``Coastal`` / ``Estuarine``), raw. bioregion = ``Bioregion`` (Short et al.
    2007 seagrass bioregions, per the dataset README); marine_ecoregion = ``ECOREGION``
    (undocumented in the dataset README; the names look like the Marine Ecoregions of
    the World, which is presumed, not stated by the source). site_name, country,
    water_body, salinity_class, position_* and elevation are NULL (not in the source).
  * location_key = lat | lon | Geotype | Bioregion | ECOREGION (floats by repr), so
    coordinates carrying conflicting labels stay separate locations.
  * sample: one per ``Unique.ID`` (the grain of a source row). taxon = ``Genera`` (raw;
    ``Mixed`` is kept as a label), taxon_rank = ``genus`` for the 12 genera and
    ``mixed`` for ``Mixed``; an unknown genus raises. sampling_year = Year.of.sampling
    (``NA`` -> NULL, flag ``year_missing``); month and depth are NULL (sediment depth
    is not defined for these quantities; depth_notes says so).
  * method: the source gives no method information, so there is one method row per
    attribute with ``method_status = method_id_null`` and ``params`` = the source
    column. For the three ``*_total`` attributes params also says the source README
    states totals are the sum of above+below when not reported, so reported and
    summed totals are not distinguishable.
  * reference_key: ``doi:<doi>`` when the row's DOI cell is a DOI (bare, or a
    https://(dx.)doi.org/ URL; all whitespace inside the cell removed, lower-cased),
    otherwise ``ref:<Reference string>``. A DOI cell that is free text (a full
    citation) or blank falls back to the Reference string. No manual crosswalk:
    one paper with two DOI forms is two references, one DOI under two Reference
    strings is one reference (one citation row per distinct Reference string).
    citation: bibliography_id = the Reference string, doi = the normalised DOI,
    year = last 4-digit 19xx/20xx in the Reference string; title/authors NULL.
    The counts of references that mix DOI and non-DOI rows are in extract_report.json.

Flags (informational; nothing is changed or dropped): year_missing,
duplicate_source_row (every member of a group of rows identical in all columns except
Unique.ID, BO22 columns included), total_ne_sum_of_parts (on every fact of a
biomass or production triple whose above, below and total are all present and where
total differs from above+below by more than ``--sum-rel-tol`` relative AND
``--sum-abs-tol`` absolute).

Usage:
    python extract.py --access-date YYYY-MM-DD --sum-rel-tol 1e-3 --sum-abs-tol 1e-3
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted

SOURCE_KEY = "foster-seagrass"
SOURCE_NAME = "Foster et al. seagrass biomass, production and structure"
SOURCE_VERSION = "2025"  # Dryad publication year; the source states no version number
SOURCE_DOI = "10.5061/dryad.kprr4xhh1"
DATA_FILE = "Dataset_ecological_biomass_complete.csv"
CITATION = (
    "Foster, Nicole; Gomis, Enric; Montemayor, Diana et al. (2025). Global patterns and "
    "drivers of seagrass biomass, net primary production and meadow structure [Dataset]. "
    "Dryad. https://doi.org/10.5061/dryad.kprr4xhh1"
)

ID_COL = "Unique.ID"
LAT_COL, LON_COL = "Latitude", "Longitude"
BIOREGION_COL, GEOTYPE_COL, GENUS_COL = "Bioregion", "Geotype", "Genera"
YEAR_COL, REF_COL, DOI_COL, ECOREGION_COL = "Year.of.sampling", "Reference", "DOI", "ECOREGION"

# source column -> (attribute, units_raw)
MEASUREMENTS: dict[str, tuple[str, str]] = {
    "Above.ground.biomass..g.DW.m.2.": ("seagrass_biomass_above", "g DW m-2"),
    "Below.ground.biomass..g.DW.m.2.": ("seagrass_biomass_below", "g DW m-2"),
    "Total.biomass..g.DW.m.2.": ("seagrass_biomass_total", "g DW m-2"),
    "Shoot.density..no..shoots.m.2.": ("seagrass_shoot_density", "shoots m-2"),
    "Percent.cover....": ("seagrass_percent_cover", "%"),
    "Leaf.density..no..leaves.m.2.": ("seagrass_leaf_density", "leaves m-2"),
    "Above.ground.production..g.DW.m.2.day..1.": ("seagrass_production_above", "g DW m-2 d-1"),
    "Below.ground.production..g.DW.m.2.day..1.": ("seagrass_production_below", "g DW m-2 d-1"),
    "Total.production..g.DW.m.2.day..1.": ("seagrass_production_total", "g DW m-2 d-1"),
    "Shoot.production..g.DW.m.2.day..1.": ("seagrass_production_shoot", "g DW m-2 d-1"),
    "Leaf.production..g.DW.m.2.day..1.": ("seagrass_production_leaf", "g DW m-2 d-1"),
}
TOTAL_ATTRS = {"seagrass_biomass_total", "seagrass_production_total"}
# (above, below, total) attributes whose total should be the sum of its parts
SUM_TRIPLES = [
    ("seagrass_biomass_above", "seagrass_biomass_below", "seagrass_biomass_total"),
    ("seagrass_production_above", "seagrass_production_below", "seagrass_production_total"),
]
BO22_COLUMNS = [
    "BO22_tempmean_ss", "BO22_tempmax_ss", "BO22_tempmin_ss", "BO22_ph", "BO22_parmax",
    "BO22_parmean", "BO22_carbonphytomax_ss", "BO22_carbonphytomean_ss", "BO22_ironmean_ss",
    "BO22_nitratemax_ss", "BO22_phosphatemax_ss", "BO22_ppmax_ss", "BO22_curvelmean_ss",
    "BO22_ppmean_ss", "BO22_salinitymean_ss", "BO22_silicatemean_ss", "BO22_dissoxmin_ss",
    "BO22_dissoxmean_ss", "BO22_chlomean_ss",
]
META_COLUMNS = [
    ID_COL, LAT_COL, LON_COL, BIOREGION_COL, GEOTYPE_COL, GENUS_COL, YEAR_COL,
]
# the source column order, asserted at read time
EXPECTED_COLUMNS = (
    META_COLUMNS + list(MEASUREMENTS) + [REF_COL, DOI_COL] + BO22_COLUMNS + [ECOREGION_COL]
)
GEOTYPES = frozenset({"Coastal", "Estuarine"})
GENERA = frozenset({
    "Amphibolis", "Cymodocea", "Enhalus", "Halodule", "Halophila", "Phyllospadix",
    "Posidonia", "Ruppia", "Syringodium", "Thalassia", "Thalassodendron", "Zostera",
})
MIXED = "Mixed"
ALLOWED_SKIP_REASONS = frozenset({"no_seagrass_measurement"})
check_skip_reasons(ALLOWED_SKIP_REASONS)  # the local list must be a subset of the registry
NULL_LITERALS = frozenset({"", "NA"})
DEPTH_NOTES = "seagrass meadow quantity; no sediment depth interval"

_DOI = re.compile(r"(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


# ---------------------------------------------------------------- helpers


def _clean(v: str) -> str | None:
    v = v.strip()
    return None if v in NULL_LITERALS else v


def _float(v: str | None, what: str) -> float:
    """Python's correctly-rounded float(); NaN for NULL; raise on non-numeric/non-finite."""
    if v is None:
        return float("nan")
    try:
        x = float(v)
    except ValueError as e:
        raise ContractError(f"{what}: non-numeric value {v!r}") from e
    if not math.isfinite(x):
        raise ContractError(f"{what}: non-finite value {v!r}")
    return x


def normalise_doi(cell: str | None) -> str | None:
    """Bare lower-case DOI, or None if the cell is blank or not a DOI (free text)."""
    if cell is None:
        return None
    squeezed = re.sub(r"\s+", "", cell)  # source has e.g. "10.1371/ journal.pone.0198348"
    m = _DOI.fullmatch(squeezed)
    return m.group(1).lower() if m else None


def _norm_ref(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def read_raw(path: Path) -> pd.DataFrame:
    # every field as string; nothing is auto-NA'd; UTF-8 with BOM
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False, na_values=[])


# ---------------------------------------------------------------- main build


def build_tables(
    raw: pd.DataFrame, access_date: str, sum_rel_tol: float, sum_abs_tol: float
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if list(raw.columns) != EXPECTED_COLUMNS:
        raise ContractError(f"{DATA_FILE}: unexpected columns {list(raw.columns)}")
    n_source = len(raw)
    df = raw.copy()
    df["row_idx"] = np.arange(n_source)  # 0-based data row, as read by pandas
    # duplicates: rows identical in every column except the id (BO22 columns included)
    dup = df.duplicated([c for c in raw.columns if c != ID_COL], keep=False)

    for c in raw.columns:
        df[c] = pd.Series([_clean(v) for v in df[c]], index=df.index, dtype=object)
    for c in [ID_COL, LAT_COL, LON_COL, BIOREGION_COL, GEOTYPE_COL, GENUS_COL, REF_COL, ECOREGION_COL]:
        if df[c].isna().any():
            raise ContractError(f"{c}: NULL in rows {df.row_idx[df[c].isna()].tolist()[:5]}")
    if df[ID_COL].duplicated().any():
        raise ContractError("Unique.ID is not unique")
    if set(df[GEOTYPE_COL]) - GEOTYPES:
        raise ContractError(f"unknown Geotype values {set(df[GEOTYPE_COL]) - GEOTYPES}")
    unknown_genera = set(df[GENUS_COL]) - GENERA - {MIXED}
    if unknown_genera:
        raise ContractError(f"unknown Genera values {unknown_genera}")

    # ---- numerics
    num = {col: np.array([_float(v, col) for v in df[col]]) for col in MEASUREMENTS}
    lat = np.array([_float(v, LAT_COL) for v in df[LAT_COL]])
    lon = np.array([_float(v, LON_COL) for v in df[LON_COL]])
    if np.isnan(lat).any() or np.isnan(lon).any():
        raise ContractError("missing coordinates")
    if not (lat.min() >= -90 and lat.max() <= 90):
        raise ContractError("latitude outside [-90, 90]")
    if not (lon.min() >= -180 and lon.max() <= 180):
        raise ContractError("longitude outside [-180, 180]")
    df["lat_v"], df["lon_v"] = lat, lon
    year_strs = df[YEAR_COL]
    for v in year_strs.dropna():
        if not re.fullmatch(r"\d{4}", v):
            raise ContractError(f"{YEAR_COL}: non-year value {v!r}")
    df["year_v"] = pd.array([None if v is None else int(v) for v in year_strs], dtype="Int64")

    # ---- exclusions: rows with no seagrass measurement
    has_meas = np.column_stack([~np.isnan(num[c]) for c in MEASUREMENTS])
    n_cells = int(has_meas.sum())
    keep = has_meas.any(axis=1)
    skips = [
        {"data_row": int(i), "reason": "no_seagrass_measurement", "n_measurements": 0}
        for i in df.row_idx[~keep]
    ]
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")
    d = df[keep].copy()
    idx = np.flatnonzero(keep)  # positions into the numpy arrays
    num_k = {c: a[idx] for c, a in num.items()}
    dup_k = dup.to_numpy()[idx]

    # ---- references: DOI when the cell is a DOI, else the Reference string
    d["ref_norm"] = d[REF_COL].map(_norm_ref)
    d["doi_n"] = d[DOI_COL].map(normalise_doi)
    d["reference_key"] = [
        f"doi:{x}" if isinstance(x, str) else f"ref:{r}" for x, r in zip(d.doi_n, d.ref_norm)
    ]
    mix = d.groupby("ref_norm").doi_n.agg(lambda s: bool(s.notna().any() and s.isna().any()))
    refs_mixed = sorted(mix.index[mix])

    # ---- location (one per distinct source coordinate+label tuple)
    d["location_key"] = (
        d.lat_v.map(repr) + "|" + d.lon_v.map(repr) + "|" + d[GEOTYPE_COL] + "|"
        + d[BIOREGION_COL] + "|" + d[ECOREGION_COL]
    )
    first = d.drop_duplicates("location_key").sort_values("location_key")
    n = len(first)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "location_key": first.location_key,
            "site_name": None,
            "source_site_id": None,
            "source_core_id": first.location_key,  # the source has no site/station id
            "latitude": first.lat_v,
            "longitude": first.lon_v,
            "position_accuracy": np.nan,
            "position_accuracy_units": None,
            "position_method": None,
            "country": None,
            "admin_division": None,
            "water_body": None,
            "habitat_type": "seagrass",
            "geomorphic_setting": first[GEOTYPE_COL],
            "bioregion": first[BIOREGION_COL],
            "marine_ecoregion": first[ECOREGION_COL],
            "salinity_class": None,
            "salinity_class_level": None,
            "vegetation_class": None,
            "vegetation_class_level": None,
            "tidal_position": None,
            "tidal_position_level": None,
            "modification_status": [[] for _ in range(n)],
            "dominant_species": [[] for _ in range(n)],
            "elevation": np.nan,
            "elevation_units": None,
            "elevation_datum": None,
            "elevation_method": None,
            "water_depth_m": np.nan,
            "distance_to_shore_km": np.nan,
        }
    ).reset_index(drop=True)

    # ---- sample (one per Unique.ID)
    d["sample_key"] = "id" + d[ID_COL]
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": d.sample_key,
            "source_sample_id": d[ID_COL],
            "depth_min_cm": np.nan,
            "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": DEPTH_NOTES,
            "sampling_year": d.year_v,
            "sampling_month": pd.array([None] * len(d), dtype="Int64"),
            "taxon": d[GENUS_COL],
            "taxon_rank": np.where(d[GENUS_COL] == MIXED, "mixed", "genus"),
            "vegetated_or_bare": None,
            "marsh_zone": None,
            "sediment_type": None,
            "photosynthetic_sediment": None,
            "sample_depth_m": np.nan,
            "water_temperature_c": np.nan,
            "water_salinity": np.nan,
            "water_salinity_units": None,
            "bottom_water_o2": np.nan,
            "bottom_water_o2_units": None,
        }
    ).reset_index(drop=True)

    # ---- facts (long form), with the sum-of-parts check per row
    attr_col = {a: c for c, (a, _) in MEASUREMENTS.items()}
    val = {a: num_k[c] for a, c in attr_col.items()}
    mismatch = np.zeros(len(d), dtype=bool)
    mismatch_by_triple: list[np.ndarray] = []
    for a, b, t in SUM_TRIPLES:
        allp = ~np.isnan(val[a]) & ~np.isnan(val[b]) & ~np.isnan(val[t])
        m = np.zeros(len(d), dtype=bool)
        for i in np.flatnonzero(allp):
            m[i] = not math.isclose(
                val[t][i], val[a][i] + val[b][i], rel_tol=sum_rel_tol, abs_tol=sum_abs_tol
            )
        mismatch_by_triple.append(m)
        mismatch |= m
    triple_of = {x: k for k, tr in enumerate(SUM_TRIPLES) for x in tr}

    rows = []
    d_rows = d.reset_index(drop=True)
    for col, (attr, units) in MEASUREMENTS.items():
        for i in np.flatnonzero(~np.isnan(val[attr])):
            flags = []
            if pd.isna(d_rows.year_v.iloc[i]):
                flags.append("year_missing")
            if dup_k[i]:
                flags.append("duplicate_source_row")
            if attr in triple_of and mismatch_by_triple[triple_of[attr]][i]:
                flags.append("total_ne_sum_of_parts")
            r = d_rows.iloc[i]
            rows.append(
                {
                    "source_key": SOURCE_KEY,
                    "fact_key": f"{r.sample_key}:{attr}",
                    "reference_key": r.reference_key,
                    "location_key": r.location_key,
                    "sample_key": r.sample_key,
                    "method_key": attr,
                    "attribute": attr,
                    "measurement_type": ATTRIBUTE_DECL[attr].measurement_type,
                    "subfield": next(iter(ATTRIBUTE_DECL[attr].subfields)),
                    "value": float(val[attr][i]),
                    "units_raw": units,
                    "sd": np.nan,
                    "se": np.nan,
                    "n": None,
                    "flags": sorted(flags),
                    "source_row_ref": f"{DATA_FILE}#data_row={int(r.row_idx)}:{attr}",
                }
            )
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")

    # ---- method: one row per attribute that has facts
    used_attrs = sorted(set(fact.attribute))
    col_of = {a: c for c, (a, _) in MEASUREMENTS.items()}
    params = []
    for a in used_attrs:
        p = {"source_column": col_of[a]}
        if a in TOTAL_ATTRS:
            p["note"] = "source README: total = above + below when not reported; reported and summed totals are not distinguishable"
        params.append(json.dumps(p, sort_keys=True))
    method = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "method_key": used_attrs,
            "source_method_id": None,
            "attribute": used_attrs,
            "method_status": "method_id_null",
            "technique": None,
            "coring_method": None,
            "carbon_type": None,
            "carbon_measured_or_modeled": None,
            "fraction_carbon_method": None,
            "carbonates_removed": None,
            "carbonate_removal_method": None,
            "conversion_applied": None,
            "in_situ_ex_situ": None,
            "light_dark": None,
            "incubation_type": None,
            "experimental_status": None,
            "params": params,
        }
    )

    # ---- reference / citation
    ref_keys = sorted(set(d.reference_key))
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_keys})
    cits = d.drop_duplicates(["reference_key", "ref_norm"]).sort_values(["reference_key", "ref_norm"])

    def year(s: str) -> int | None:
        m = _YEAR.findall(s)
        return int(m[-1]) if m else None

    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "citation_key": cits.reference_key + "|" + cits.ref_norm,
            "reference_key": cits.reference_key,
            "bibliography_id": cits.ref_norm,
            "publication_type": None,
            "doi": cits.doi_n,
            "title": None,
            "authors": None,
            "year": pd.array([year(s) for s in cits.ref_norm], dtype="Int64"),
            "url": None,
            "openalex_id": None,
            "doi_resolved": None,
            "doi_resolution_source": None,
        }
    ).reset_index(drop=True)

    source = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY,
                "name": SOURCE_NAME,
                "version": SOURCE_VERSION,
                "doi": SOURCE_DOI,
                "access_date": access_date,
                "license": None,
                "notes": CITATION + " Seagrass biomass, production and structure meta-analysis "
                "table. The 19 Bio-ORACLE BO22_* environmental columns were deliberately not loaded.",
            }
        ]
    )

    tables = {
        "source": source,
        "reference": reference,
        "citation": citation,
        "study_screening": empty_table("study_screening"),
        "location": loc,
        "sample": sample,
        "method": method,
        "fact": fact,
    }
    flag_counts: dict[str, int] = {}
    for f in fact["flags"]:
        for x in f:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    report = {
        "source_rows": n_source,
        "rows_with_facts": len(d),
        "rows_excluded": len(skips),
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "nonblank_measurement_cells": n_cells,
        "samples": len(sample),
        "locations": len(loc),
        "methods": len(method),
        "references": len(reference),
        "references_doi_keyed": int(sum(k.startswith("doi:") for k in ref_keys)),
        "references_text_keyed": int(sum(k.startswith("ref:") for k in ref_keys)),
        "citations": len(citation),
        "reference_strings_mixing_doi_and_non_doi_rows": refs_mixed,
        "doi_cells_not_doi_nonblank": int(
            (d[DOI_COL].notna() & d.doi_n.isna()).sum()
        ),
        "sum_rel_tol": sum_rel_tol,
        "sum_abs_tol": sum_abs_tol,
        "flag_counts": flag_counts,
        "bo22_columns_not_loaded": BO22_COLUMNS,
        "bo22_rows_populated": int(df[BO22_COLUMNS[0]].notna().sum()),
    }
    # accounting: every source row is a row with facts or a listed exclusion; every
    # non-blank measurement cell is exactly one fact
    if len(d) + len(skips) != n_source:
        raise ContractError("rows with facts + excluded rows != source rows")
    if len(fact) != n_cells:
        raise ContractError(f"facts {len(fact)} != non-blank measurement cells {n_cells}")
    if set(fact.sample_key) != set(sample.sample_key):
        raise ContractError("sample rows and fact samples differ")
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    ap.add_argument("--sum-rel-tol", type=float, required=True,
                    help="relative tolerance for flagging total != above+below")
    ap.add_argument("--sum-abs-tol", type=float, required=True,
                    help="absolute tolerance for flagging total != above+below")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    raw = read_raw(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(raw, args.access_date, args.sum_rel_tol, args.sum_abs_tol)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str)
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
