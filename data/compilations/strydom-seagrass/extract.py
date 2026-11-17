#!/usr/bin/env python3
"""Strydom et al. global seagrass structure / biomass / production database -> compilation-contract tables.

Strydom, S. et al. (2021): Global database of key seagrass structure, biomass and production
variables [dataset]. PANGAEA, doi:10.1594/PANGAEA.929968. Supplement to Strydom et al. (2023),
Earth System Science Data 15, 511-519, doi:10.5194/essd-15-511-2023. CC-BY-4.0. Raw file (this
directory): ``Seagrass_db.tab`` (PANGAEA tab format: a ``/* ... */`` header, then a tab-separated
table with its own header line).

The table is the same design as Foster et al. (``foster-seagrass``): one row per study x site x
genus x year with the same 11 seagrass measures, so this extract reuses Foster's attribute names,
types and sum-of-parts rule (imported read-only from ``foster-seagrass/extract.py``). What differs:
PANGAEA's unit spellings (declared verbatim), no Unique.ID, no DOI column, no ECOREGION, no Bio-ORACLE
columns, and a full citation list in the file header.

Grain: ONE FACT PER NON-BLANK SEAGRASS MEASUREMENT. A row holding k of the 11 measurement columns
becomes k facts. Values are carried exactly as reported, in the units of the column header
(asserted). Nothing is converted or capped. PANGAEA parameter names say dry mass ("dm").

Attributes (source column -> attribute, units_raw verbatim from the header bracket):
  Seagr biom above/below/tot dm [g/m**2]        -> seagrass_biomass_above/_below/_total      g/m**2
  Seagr shoot dens [#/m**2]                     -> seagrass_shoot_density                    #/m**2
  Seagr leaf dens [#/m**2]                      -> seagrass_leaf_density                     #/m**2
  Seagr cov [%]                                 -> seagrass_percent_cover                    %
  Seagr prod above/below/total dm, shoot prod dm, leaf prod dm [g/m**2/day]
                                                -> seagrass_production_above/_below/_total/_shoot/_leaf   g/m**2/day
The attributes and their measurement types are Foster's (``compilation_vocab.ATTRIBUTES``); the
unit strings are this source's spelling and are declared beside Foster's. Equivalent spellings
(``g DW m-2`` vs ``g/m**2``) stay separate; conversion is downstream.

Fail loud. The only exclusion is a row with none of the 11 measurement columns filled (reason
``no_seagrass_measurement``, in ``skip_report.jsonl``). Row accounting is asserted: source rows =
rows with facts + excluded rows, and fact count = non-blank measurement cells.

Standardisation / derived-field rules (the raw value is recoverable via ``fact.source_row_ref`` =
``Seagrass_db.tab#data_row=<0-based data row, as read by pandas after the header>:<attribute>``):
  * Strings are whitespace-stripped; blank becomes NULL (the file has no ``NA`` literal; a
    non-numeric measurement raises).
  * latitude/longitude are the source decimal degrees, unchanged (asserted in range). The source
    corrected some longitudes from east to west on 2025-05-06 (header "Change history"); the file
    read here is that corrected version.
  * habitat_type = constant ``seagrass`` (the dataset's scope, not a per-row label).
    geomorphic_setting = the source ``Habitat`` label (``Coastal`` / ``Estuarine``), raw.
    bioregion = ``Seagr bioregion`` (the six Short et al. 2007 bioregions). No marine_ecoregion,
    site_name, country, water_body, salinity, position_* or elevation (not in the source).
  * location_key = lat | lon | Habitat | Seagr bioregion (floats by repr), so coordinates carrying
    conflicting labels stay separate locations (as Foster).
  * sample: one per source data row (the file has no row id). taxon = ``Genus`` (12 genera, an
    unknown genus raises; there is no ``Mixed`` here), taxon_rank = ``genus``. sampling_year =
    ``Year obs [a AD]`` (blank -> NULL, flag ``year_missing``); month and depth are NULL.
  * method: the source gives no method information, so one method row per attribute,
    ``method_status = method_id_null``, params = the source column (and, for the totals, Foster's note that
    a total may be the sum of its parts).
  * reference_key = ``ref:<Reference string>`` (whitespace-normalised). The file has no DOI per row.
    Full citations and DOIs come from the header's "Related to" list by an author-year match
    (``match_reference``): single author -> one-author entry; "A and B" / "A & B" -> two-author entry
    with both surnames; "A et al." -> entry with >= 2 authors and that first surname; the year must
    be equal. A string with a year letter (``2010a``), or matching more than one entry, is
    AMBIGUOUS and not matched; no entry -> unmatched. Nothing is matched fuzzily; matched / ambiguous /
    unmatched strings are all listed in ``extract_report.json``. A matched string gets the entry's
    DOI, authors and year; the others keep the string and a year parsed from it. The DOI of a matched
    entry is the ``https://doi.org/...`` link ending the entry (entries with no link: no DOI).

Flags (informational; nothing is changed or dropped): habitat_from_scope (every fact),
year_missing, duplicate_source_row (every member of a group of rows identical in ALL columns --
identical measurement values included, so some may be genuine replicates), total_ne_sum_of_parts
(same rule and tolerances as Foster: above, below and total all present, total differs from
above+below by more than ``--sum-rel-tol`` relative AND ``--sum-abs-tol`` absolute).

Usage:
    python -I extract.py --access-date YYYY-MM-DD --sum-rel-tol 1e-3 --sum-abs-tol 1e-3
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons, load_label_maps  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

# Foster's helpers and tables (the same measurement design); imported read-only
_FOSTER = Path(__file__).resolve().parents[1] / "foster-seagrass" / "extract.py"
_spec = importlib.util.spec_from_file_location("foster_extract_for_strydom", _FOSTER)
assert _spec and _spec.loader
_foster = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_foster)
_float = _foster._float
SUM_TRIPLES = _foster.SUM_TRIPLES
TOTAL_ATTRS = _foster.TOTAL_ATTRS
GENERA = _foster.GENERA
_YEAR = _foster._YEAR
_norm_ref = _foster._norm_ref

SOURCE_KEY = "strydom-seagrass"
SOURCE_NAME = "Strydom et al. global seagrass structure, biomass and production database"
SOURCE_DOI = "10.1594/PANGAEA.929968"
SOURCE_LICENSE = "CC-BY-4.0"
DATA_FILE = "Seagrass_db.tab"
HABITAT_TYPE = "seagrass"  # constant: the dataset's scope
CITATION = (
    "Strydom, Simone; Webster, Chanelle L; McCallum, Roisin; Lafratta, Anna; O'Dea, Caitlyn M; "
    "Said, Nicole E; Inostroza, Karina; Salinas, Cristian; Billinghurst, Samuel; Phelps, Charlie M; "
    "Campbell, Connor; Gorham, Connor; Dunham, Natasha; Bernasconi, Rachele; Frouws, Anna M; "
    "Werner, Axel; Vitelli, Frederico; Puigcorbé, Viena; D'Cruz, Alexandra; McMahon, Kathryn M; "
    "Robinson, Jack; Huggett, Megan J; McNamara, Sian; Hyndes, Glenn A; Serrano, Óscar (2021): "
    "Global database of key seagrass structure, biomass and production variables [dataset]. "
    "PANGAEA, https://doi.org/10.1594/PANGAEA.929968"
)

LAT_COL, LON_COL = "Latitude", "Longitude"
BIOREGION_COL, HABITAT_COL, GENUS_COL = "Seagr bioregion", "Habitat", "Genus"
YEAR_COL, REF_COL = "Year obs [a AD]", "Reference"

# source column -> (attribute, units_raw); units_raw is the header bracket, asserted at read time
MEASUREMENTS: dict[str, tuple[str, str]] = {
    "Seagr biom above dm [g/m**2]": ("seagrass_biomass_above", "g/m**2"),
    "Seagr biom below dm [g/m**2]": ("seagrass_biomass_below", "g/m**2"),
    "Seagr biom tot dm [g/m**2]": ("seagrass_biomass_total", "g/m**2"),
    "Seagr shoot dens [#/m**2]": ("seagrass_shoot_density", "#/m**2"),
    "Seagr cov [%]": ("seagrass_percent_cover", "%"),
    "Seagr leaf dens [#/m**2]": ("seagrass_leaf_density", "#/m**2"),
    "Seagr prod above dm [g/m**2/day]": ("seagrass_production_above", "g/m**2/day"),
    "Seagr prod below dm [g/m**2/day]": ("seagrass_production_below", "g/m**2/day"),
    "Seagr prod total dm [g/m**2/day]": ("seagrass_production_total", "g/m**2/day"),
    "Seagr shoot prod dm [g/m**2/day]": ("seagrass_production_shoot", "g/m**2/day"),
    "Seagr leaf prod dm [g/m**2/day]": ("seagrass_production_leaf", "g/m**2/day"),
}
EXPECTED_COLUMNS = (
    [LAT_COL, LON_COL, BIOREGION_COL, HABITAT_COL, GENUS_COL, YEAR_COL]
    + list(MEASUREMENTS) + [REF_COL]
)
HABITATS = frozenset({"Coastal", "Estuarine"})
BIOREGIONS = frozenset({
    "Mediterranean", "Temperate North Atlantic", "Temperate North Pacific",
    "Temperate Southern", "Tropical Atlantic", "Tropical Indo Pacific",
})
ALLOWED_SKIP_REASONS = frozenset({"no_seagrass_measurement"})
check_skip_reasons(ALLOWED_SKIP_REASONS)  # the local list must be a subset of the registry
DEPTH_NOTES = "seagrass meadow quantity; no sediment depth interval"

_ENTRY = re.compile(r"(?P<authors>.+?) \((?P<year>(?:19|20)\d{2})\): (?P<rest>.*)", re.DOTALL)
_ENTRY_DOI = re.compile(r"https?://doi\.org/(10\.\d{4,9}/\S+)\s*$", re.IGNORECASE)
_REF = re.compile(r"(?P<names>.+?) (?P<year>(?:19|20)\d{2})(?P<suffix>[a-z]?)")
_ETAL = re.compile(r"\s+(?:et|ey)\.?\s+al[.,]?$")


def _assert_habitat_from_scope(labels: set[str]) -> None:
    """``habitat_from_scope`` goes on every fact, so every ``habitat_type`` label must map to a
    habitat in the label map (otherwise the flag would claim a scope-derived habitat that is not there)."""
    m = load_label_maps().get((SOURCE_KEY, "habitat_type", "habitat"), {})
    bad = sorted(x for x in labels if x not in m or m[x] is None)
    if bad:
        raise ContractError(f"habitat_type labels without a habitat in the label map: {bad}")


# ---------------------------------------------------------------- header and table reading


def read_raw(path: Path) -> tuple[list[str], pd.DataFrame]:
    """Return (header lines incl. the closing ``*/``, table as strings). Nothing is auto-NA'd."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or lines[0] != "/* DATA DESCRIPTION:":
        raise ContractError(f"{path.name}: not a PANGAEA /* DATA DESCRIPTION header")
    close = [i for i, l in enumerate(lines) if l == "*/"]
    if len(close) != 1:
        raise ContractError(f"{path.name}: expected exactly one '*/' line, found {len(close)}")
    n_header = close[0] + 1
    from io import StringIO

    df = pd.read_csv(
        StringIO("\n".join(lines[n_header:])), sep="\t", dtype=str,
        keep_default_na=False, na_values=[], encoding="utf-8",
    )
    return lines[:n_header], df


def header_field(header: list[str], name: str) -> str:
    """The single-line value of ``<name>:\\t<value>``; raises unless exactly one line has it."""
    hits = [l[len(name) + 2:] for l in header if l.startswith(name + ":\t")]
    if len(hits) != 1:
        raise ContractError(f"header field {name!r}: expected one line, found {len(hits)}")
    return hits[0]


def header_citations(header: list[str]) -> list[str]:
    """The ``Related to:`` list: its first line plus the tab-indented continuation lines."""
    start = [i for i, l in enumerate(header) if l.startswith("Related to:\t")]
    if len(start) != 1:
        raise ContractError("header: expected one 'Related to:' field")
    out = [header[start[0]][len("Related to:\t"):]]
    j = start[0] + 1
    while header[j].startswith("\t"):
        out.append(header[j][1:])
        j += 1
    return out


def parse_entry(entry: str) -> dict:
    """One header citation -> authors (verbatim), surnames, year, doi (bare lower-case or None)."""
    m = _ENTRY.fullmatch(entry)
    if not m:
        raise ContractError(f"header citation not in 'Authors (YYYY): ...' form: {entry[:80]!r}")
    authors = m["authors"]
    surnames = [_norm_name(a.split(",")[0]) for a in authors.split("; ")]
    d = _ENTRY_DOI.search(entry)
    return {
        "authors": authors,
        "surnames": surnames,
        "year": int(m["year"]),
        "doi": d.group(1).lower() if d else None,
    }


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s\-]+", " ", s).strip().casefold()


def parse_reference(ref: str) -> dict | None:
    """'Smith 2010' / 'Smith and Jones 2010' / 'Smith & Jones 2010' / 'Smith et al. 2010a' ->
    {'form': 'one'|'two'|'etal', 'surnames': [...], 'year': int, 'suffix': ''|'a'|...}; None if
    it fits none of these."""
    m = _REF.fullmatch(ref)
    if not m:
        return None
    names = m["names"].rstrip(".,")  # 'Bryars. 2009', 'Fourquean et al, 2010'
    form = "one"
    if _ETAL.search(names):
        names = _ETAL.sub("", names)
        form = "etal"
        parts = [names]
    elif " and " in names or " & " in names:
        parts = re.split(r" and | & ", names)
        if len(parts) != 2:
            return None
        form = "two"
    else:
        parts = [names]
    return {
        "form": form,
        "surnames": [_norm_name(p) for p in parts],
        "year": int(m["year"]),
        "suffix": m["suffix"],
    }


def match_reference(ref: str, entries: list[dict]) -> tuple[str, int | None]:
    """('matched', entry index) | ('ambiguous', None) | ('no_entry', None) | ('unparsed', None)."""
    p = parse_reference(ref)
    if p is None:
        return "unparsed", None
    cands = []
    for k, e in enumerate(entries):
        if e["year"] != p["year"] or e["surnames"][0] != p["surnames"][0]:
            continue
        if p["form"] == "one" and len(e["surnames"]) != 1:
            continue
        if p["form"] == "two" and e["surnames"] != p["surnames"]:
            continue
        if p["form"] == "etal" and len(e["surnames"]) < 2:
            continue
        cands.append(k)
    if not cands:
        return "no_entry", None
    if len(cands) > 1 or p["suffix"]:
        return "ambiguous", None
    return "matched", cands[0]


# ---------------------------------------------------------------- main build


def build_tables(
    header: list[str], raw: pd.DataFrame, access_date: str, sum_rel_tol: float, sum_abs_tol: float
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if list(raw.columns) != EXPECTED_COLUMNS:
        raise ContractError(f"{DATA_FILE}: unexpected columns {list(raw.columns)}")
    for col, (_, units) in MEASUREMENTS.items():
        m = re.search(r"\[(.+)\]$", col)
        if not m or m.group(1) != units:
            raise ContractError(f"{col}: header unit is not {units!r}")
    if SOURCE_LICENSE not in header_field(header, "License"):
        raise ContractError("header License does not state " + SOURCE_LICENSE)
    change = header_field(header, "Change history")
    m = re.match(r"(\d{4}-\d{2}-\d{2})T", change)
    if not m:
        raise ContractError(f"cannot read the change date from {change!r}")
    version = m.group(1)

    n_source = len(raw)
    df = raw.copy()
    df["row_idx"] = np.arange(n_source)  # 0-based data row, as read by pandas
    dup = df.duplicated(list(raw.columns), keep=False)  # raw strings, before cleaning
    for c in raw.columns:
        df[c] = pd.Series([v.strip() or None for v in df[c]], index=df.index, dtype=object)
    for c in [LAT_COL, LON_COL, BIOREGION_COL, HABITAT_COL, GENUS_COL, REF_COL]:
        if df[c].isna().any():
            raise ContractError(f"{c}: NULL in rows {df.row_idx[df[c].isna()].tolist()[:5]}")
    if set(df[HABITAT_COL]) - HABITATS:
        raise ContractError(f"unknown Habitat values {set(df[HABITAT_COL]) - HABITATS}")
    if set(df[BIOREGION_COL]) - BIOREGIONS:
        raise ContractError(f"unknown Seagr bioregion values {set(df[BIOREGION_COL]) - BIOREGIONS}")
    if set(df[GENUS_COL]) - GENERA:
        raise ContractError(f"unknown Genus values {set(df[GENUS_COL]) - GENERA}")

    num = {col: np.array([_float(v, col) for v in df[col]]) for col in MEASUREMENTS}
    lat = np.array([_float(v, LAT_COL) for v in df[LAT_COL]])
    lon = np.array([_float(v, LON_COL) for v in df[LON_COL]])
    if not (lat.min() >= -90 and lat.max() <= 90):
        raise ContractError("latitude outside [-90, 90]")
    if not (lon.min() >= -180 and lon.max() <= 180):
        raise ContractError("longitude outside [-180, 180]")
    df["lat_v"], df["lon_v"] = lat, lon
    for v in df[YEAR_COL].dropna():
        if not re.fullmatch(r"\d{4}", v):
            raise ContractError(f"{YEAR_COL}: non-year value {v!r}")
    df["year_v"] = pd.array([None if v is None else int(v) for v in df[YEAR_COL]], dtype="Int64")

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
    d = df[keep].reset_index(drop=True)
    idx = np.flatnonzero(keep)
    num_k = {c: a[idx] for c, a in num.items()}
    dup_k = dup.to_numpy()[idx]

    # ---- references and citations (author-year match against the header list)
    d["ref_norm"] = d[REF_COL].map(_norm_ref)
    d["reference_key"] = "ref:" + d.ref_norm
    entries = [parse_entry(e) for e in header_citations(header)]
    ref_strings = sorted(set(d.ref_norm))
    match: dict[str, tuple[str, int | None]] = {r: match_reference(r, entries) for r in ref_strings}
    by_status: dict[str, list[str]] = {}
    for r, (st, _) in match.items():
        by_status.setdefault(st, []).append(r)
    near_misses = {
        r: [f"{entries[k]['authors'][:50]} ({entries[k]['year']})" for k in range(len(entries))
            if (pr := parse_reference(r)) is not None and entries[k]["surnames"][0] == pr["surnames"][0]]
        for r in by_status.get("no_entry", []) + by_status.get("ambiguous", [])
    }
    matched_entries = [k for (st, k) in match.values() if st == "matched"]
    entries_hit_twice = sorted({k for k in matched_entries if matched_entries.count(k) > 1})

    # ---- location (one per distinct source coordinate+label tuple)
    d["location_key"] = (
        d.lat_v.map(repr) + "|" + d.lon_v.map(repr) + "|" + d[HABITAT_COL] + "|" + d[BIOREGION_COL]
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
            "habitat_type": HABITAT_TYPE,
            "geomorphic_setting": first[HABITAT_COL],
            "bioregion": first[BIOREGION_COL],
            "marine_ecoregion": None,
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

    # ---- sample (one per source data row)
    d["sample_key"] = "row" + d.row_idx.astype(str)
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": d.sample_key,
            "source_sample_id": None,
            "depth_min_cm": np.nan,
            "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": DEPTH_NOTES,
            "sampling_year": d.year_v,
            "sampling_month": pd.array([None] * len(d), dtype="Int64"),
            "taxon": d[GENUS_COL],
            "taxon_rank": "genus",
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
    mismatch_by_triple: list[np.ndarray] = []
    for a, b, t in SUM_TRIPLES:
        allp = ~np.isnan(val[a]) & ~np.isnan(val[b]) & ~np.isnan(val[t])
        m_ = np.zeros(len(d), dtype=bool)
        for i in np.flatnonzero(allp):
            m_[i] = not math.isclose(
                val[t][i], val[a][i] + val[b][i], rel_tol=sum_rel_tol, abs_tol=sum_abs_tol
            )
        mismatch_by_triple.append(m_)
    triple_of = {x: k for k, tr in enumerate(SUM_TRIPLES) for x in tr}

    _assert_habitat_from_scope({HABITAT_TYPE})
    rows = []
    for col, (attr, units) in MEASUREMENTS.items():
        for i in np.flatnonzero(~np.isnan(val[attr])):
            flags = ["habitat_from_scope"]  # habitat_type is the constant "seagrass" (dataset scope)
            if pd.isna(d.year_v.iloc[i]):
                flags.append("year_missing")
            if dup_k[i]:
                flags.append("duplicate_source_row")
            if attr in triple_of and mismatch_by_triple[triple_of[attr]][i]:
                flags.append("total_ne_sum_of_parts")
            r = d.iloc[i]
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
            p["note"] = "a total may be the sum of its parts; reported and summed totals are not distinguishable in this file"
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

    # ---- reference / citation (one citation row per distinct Reference string)
    ref_keys = ["ref:" + r for r in ref_strings]
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_keys})

    def year_of(s: str) -> int | None:
        found = _YEAR.findall(s)
        return int(found[-1]) if found else None

    cit_doi, cit_authors, cit_year = [], [], []
    for r in ref_strings:
        st, k = match[r]
        if st == "matched":
            assert k is not None
            e = entries[k]
            cit_doi.append(e["doi"])
            cit_authors.append(e["authors"])
            cit_year.append(e["year"])
        else:
            cit_doi.append(None)
            cit_authors.append(None)
            cit_year.append(year_of(r))
    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "citation_key": ["ref:" + r + "|" + r for r in ref_strings],
            "reference_key": ref_keys,
            "bibliography_id": ref_strings,
            "publication_type": None,
            "doi": cit_doi,
            "title": None,
            "authors": cit_authors,
            "year": pd.array(cit_year, dtype="Int64"),
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
                "version": version,
                "doi": SOURCE_DOI,
                "access_date": access_date,
                "license": SOURCE_LICENSE,
                "notes": CITATION + " Version = the date of the last change in the file header "
                "(corrected some longitudes from east to west); PANGAEA states no version number.",
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
    n_dup_rows = int(dup_k.sum())
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
        "citations": len(citation),
        "source_version": version,
        "header_citations": len(entries),
        "header_citations_with_doi": sum(e["doi"] is not None for e in entries),
        "reference_match": {
            "matched": len(by_status.get("matched", [])),
            "ambiguous": by_status.get("ambiguous", []),
            "no_entry": by_status.get("no_entry", []),
            "unparsed": by_status.get("unparsed", []),
            "matched_with_doi": sum(
                entries[k]["doi"] is not None for (st, k) in match.values() if st == "matched" and k is not None
            ),
            "near_misses_same_first_author_any_year_not_matched": near_misses,
            "header_entries_matched_by_more_than_one_string": [
                entries[k]["authors"][:60] + f" ({entries[k]['year']})" for k in entries_hit_twice
            ],
        },
        "header_entries_matched_by_no_string": len(entries) - len(set(matched_entries)),
        "reference_strings_with_replacement_char_u_fffd": [r for r in ref_strings if "\ufffd" in r],
        "reference_strings_all_in_rows_without_measurement": sorted(
            set(_norm_ref(v) for v in df.loc[~keep, REF_COL]) - set(ref_strings)
        ),
        "duplicate_source_rows_flagged": n_dup_rows,
        "flag_counts": flag_counts,
        "sum_rel_tol": sum_rel_tol,
        "sum_abs_tol": sum_abs_tol,
    }
    # accounting: every source row is a row with facts or a listed exclusion; every
    # non-blank measurement cell is exactly one fact
    if len(d) + len(skips) != n_source:
        raise ContractError("rows with facts + excluded rows != source rows")
    if len(fact) != n_cells:
        raise ContractError(f"facts {len(fact)} != non-blank measurement cells {n_cells}")
    if set(fact.sample_key) != set(sample.sample_key):
        raise ContractError("sample rows and fact samples differ")
    if set(fact.reference_key) != set(reference.reference_key):
        raise ContractError("reference rows and fact references differ")
    if sum(len(v) for v in by_status.values()) != len(ref_strings):
        raise ContractError("reference match statuses do not cover every Reference string")
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
    header, raw = read_raw(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(
        header, raw, args.access_date, args.sum_rel_tol, args.sum_abs_tol
    )
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
