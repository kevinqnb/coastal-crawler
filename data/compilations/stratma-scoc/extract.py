#!/usr/bin/env python3
"""SCOC database v1 -> compilation-contract tables.

Stratmann, T., Soetaert, K., Wei, C.-L. et al. (2022). The SCOC database - a large,
open and global database with sediment community oxygen consumption rates.
Dryad, doi:10.5061/dryad.25nd083. Raw files (this directory):
``SCOC_database_version_1.csv`` (measurements) and
``List_of_studies_version_1.csv`` (screening record of every candidate study).

Grain: ONE FACT PER DATA ROW = one sediment community oxygen consumption rate at one
station. Values are carried exactly as reported, in the source's units
(``mmol O2 m-2 d-1``, read from the column header and asserted). Suspicious values
are FLAGGED in ``fact.flags``, never changed or dropped.

Attribute (from the TOU/DOU/AOU column; different quantities, never mixed), all
measurement type B5 (benthic metabolism); ``fact.subfield`` in brackets:
  TOU -> scoc_tou (total oxygen uptake) [total_o2_uptake]
  DOU -> scoc_dou (diffusive oxygen uptake) [diffusive_o2_uptake]
  AOU -> scoc_aou (advective oxygen uptake) [advective_o2_uptake; a declared extension
         of the standard B5 subfields]
  NA  -> scoc_unspecified (20 rows, all one study; flux type not stated) [NULL subfield]
         + flag ``flux_type_missing``.
Plus ONE G1 fact per data row with a median grain size: scoc_median_grain_size
[median_grain_size], units ``um`` (the source header's "?m" is a mangled micro sign), its
own method row per study (no method information), flags = the row's location/duplicate/
study flags (+ ``zero_value`` if 0).
Standard labels (habitat, setting) come from ``compilation_label_maps.csv``: habitat = subtidal soft
sediment for every ``Depth range`` label; setting shelf / open ocean for the shelf and
abyssal labels, blank for near-shore and slope.

Fail loud. The only exclusion is a row whose every field is blank (reason
``empty_row``, listed in ``skip_report.jsonl``); a row that is only partly blank
raises. Row accounting is asserted: source rows = facts + excluded rows.

Standardisation / derived-field rules (the raw value is recoverable through
``fact.source_row_ref``):
  * Strings are whitespace-stripped; the literals ``NA`` / ``Na`` and blank become NULL.
    ``not investigated`` is kept as a label (it is information, not missing).
  * longitude: if > 180 (source mixes 0-360 and +-180) subtract 360 using exact decimal
    arithmetic; flag ``lon_wrapped``. latitude and longitude are otherwise unchanged.
  * water_depth_m = Depth (m), unchanged (positive down); elevation is NULL.  ``habitat_type`` = the source's ``Depth range``
    label (a depth class, kept raw); ``water_body`` = ``Ocean``; ``site_name`` = ``Region``.
    ``position_method`` = the source's ``Location`` label (exact / approximated location).
  * No country / admin_division is parsed out of ``Region``.
  * sample: one row per data row (the flux is for the whole community at the sediment
    surface, so there is no sediment depth interval); sediment type and the
    photosynthetic-sediment label are sample columns.
  * method: technique = measuring device; in_situ_ex_situ and light_dark raw labels.
    ``method_status`` is ``matched`` when device and in/ex-situ are present and
    ``method_id_null`` when both are NA (the source has no method id at all).
  * reference_key = the sorted ``+``-joined keys of the list-of-studies entries a data
    row's reference string resolves to. A data reference string matches a study-list
    entry when their case- and punctuation-insensitive forms are equal; the residual
    mismatches are resolved by ``CROSSWALK`` below (asserted to cover exactly the
    mismatches, so a new mismatch raises).  Study keys are ``study_<0-based list row>``.
  * study_screening: all list-of-studies rows, raw labels; ``reference_key`` is NULL for
    studies with no facts.

Flags: zero_value, lon_wrapped, lat_lon_zero, coords_missing, coords_approximate,
depth_missing, method_missing, flux_type_missing, study_marked_excluded (the cited study is
not `included` in the study list; 1 known row), duplicate_source_row (every member of
a group of fully identical source rows).

Usage:
    python extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from coastal_crawler.compilations import ContractError, write_extracted

SOURCE_KEY = "stratma-scoc"
SOURCE_NAME = "SCOC database"
SOURCE_VERSION = "1"
SOURCE_DOI = "10.5061/dryad.25nd083"
DATA_FILE = "SCOC_database_version_1.csv"
LIST_FILE = "List_of_studies_version_1.csv"

EXPECTED_UNITS = "mmol O2 m-2 d-1"
DATA_COLUMNS = [
    "Region", "Ocean", "Latitude (N)", "Longitude", "Location", "Depth (m)",
    "Depth range", f"SCOC ({EXPECTED_UNITS})", "ex situ/ in situ",
    "Measurement/ sampling device", "TOU/ DOU/ AOU",
    "median sediment grain size (?m)", "sediment type", "photosynthetic sediment",
    "dark/ light incubation/ measurements", "Reference",
]
SHORT = [
    "region", "ocean", "lat", "lon", "locq", "depth", "drange", "scoc", "insitu",
    "device", "flux", "grain", "sed", "photo", "dl", "ref",
]
LIST_COLUMNS = ["Study reference", "Screening phase", "Eligibility phase", "SCOC database"]

ATTRIBUTE_BY_FLUX = {
    "TOU": "scoc_tou",
    "DOU": "scoc_dou",
    "AOU": "scoc_aou",
    None: "scoc_unspecified",
}
ALLOWED_SKIP_REASONS = frozenset({"empty_row"})
# Studies the data cites although the list marks them as not in the SCOC database
# (found 2026-11-15: one row of Smith & Teal 1973, list row 251, "excluded" at the
# eligibility phase). Kept and flagged ``study_marked_excluded``; any other mismatch raises.
KNOWN_USED_BUT_NOT_INCLUDED = frozenset({"study_251"})
NULL_LITERALS = frozenset({"", "NA", "Na"})
GRAIN_ATTR = "scoc_median_grain_size"
GRAIN_UNITS = "um"
SUBFIELD_BY_ATTR = {
    "scoc_tou": "total_o2_uptake",
    "scoc_dou": "diffusive_o2_uptake",
    "scoc_aou": "advective_o2_uptake",
    "scoc_unspecified": None,
}
NO_WATER_CONTEXT = {  # in-situ water context columns on sample_dim this compilation does not have
    "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan,
    "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
}
DEPTH_NOTES = "sediment-water interface flux; no sediment depth interval"

# data-reference prefix -> list-of-studies prefixes (normalised strings, see _norm).
# Only strings whose canonical form matches no list entry are resolved here; each
# data prefix must hit >=1 data string and each study prefix exactly 1 list entry.
CROSSWALK: list[tuple[str, list[str]]] = [
    ("Baldwin, R. . & Smith", ["Baldwin, R. J. & Smith"]),
    ("Christensen, J. P. HLY-04-03", ["Christensen, J. P. HLY-04-03"]),  # 2 data strings
    ("Devol, A. H. & Christensen, J. P. HLY-02-03", ["Devol, A. H. & Christensen, J. P. HLY-02-03"]),
    ("Devol, A. H. & Christensen, J. P. HLY-04-02", ["Devol, A. H. & Christensen, J. P. HLY-04-02"]),
    ("Devol, A. H. Sediment oxygen flux", ["Devol, A. H. Sediment oxygen flux"]),
    ("Grebmeier, J. M. & Cooper, L. W. HLY-04-02", ["Grebmeier, J. M. & Cooper, L. W. HLY-04-02"]),
    ("Grebmeier, J. M. & Cooper, L. W. HLY-04-03", ["Grebmeier, J. M. & Cooper, L. W. HLY-04-03"]),
    ("Grebmeier, J. M. & Cooper, L. W. HLY-08-01", ["Grebmeier, J. M. & Cooper, L. W. HLY-08-01"]),
    ("Grebmeier, J. M. & Cooper, L. W. HLY-09-01", ["Grebmeier, J. M. & Cooper, L. W. HLY-09-01"]),
    ("Grebmeier, J. M. & Cooper, L. W. HLY0601", ["Grebmeier, J. M. & Cooper, L. W. HLY0601"]),
    ("Grebmeier, J. M. & Cooper, L. W. HLY0702", ["Grebmeier, J. M. & Cooper, L. W. HLY0702"]),
    ("Grebmeier, J. M. & Cooper, L. W. PacMARS", ["Grebmeier, J. M. & Cooper, L. W. PacMARS"]),
    ("Lampitt, R. S., Raine", ["Lampitt, R. S., Raine"]),
    # one data cell holds two citations
    ("Seiler, D. Biogeochemical", ["Seiler, D. Biogeochemical", "Seiler, D. Macrobenthic"]),
    ("Wenzhöfer, F., Riess", ["Wenzhöfer, F., Riess", "Weber, A., Riess"]),
]


# ---------------------------------------------------------------- helpers


def _norm(s: str) -> str:
    """Collapse whitespace, drop one trailing period."""
    return re.sub(r"\s+", " ", s).strip().rstrip(".").strip()


def _canon(s: str) -> str:
    """Case- and punctuation-insensitive form used to match reference strings."""
    return re.sub(r"[^a-z0-9]", "", _norm(s).casefold())


def _clean(v: str) -> str | None:
    v = v.strip()
    return None if v in NULL_LITERALS else v


def _float(v: str | None, what: str) -> float:
    """Python's correctly-rounded float(); NaN for NULL; raise on non-numeric."""
    if v is None:
        return float("nan")
    try:
        return float(v)
    except ValueError as e:
        raise ContractError(f"{what}: non-numeric value {v!r}") from e


def _none(v: Any) -> Any:
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


def _read_csv(path: Path) -> pd.DataFrame:
    # every field as string; nothing is auto-NA'd; the file is Windows-1252
    return pd.read_csv(path, dtype=str, encoding="cp1252", keep_default_na=False, na_values=[])


# ---------------------------------------------------------------- study list / references


def build_studies(
    lst: pd.DataFrame, data_refs: set[str]
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Returns (study table, data reference string (normalised) -> sorted study keys)."""
    if list(lst.columns) != LIST_COLUMNS:
        raise ContractError(f"{LIST_FILE}: unexpected columns {list(lst.columns)}")
    studies = pd.DataFrame(
        {
            "study_key": [f"study_{i:03d}" for i in range(len(lst))],
            "study_citation": [re.sub(r"\s+", " ", x).strip() for x in lst["Study reference"]],
            "screening_phase": [_clean(x) for x in lst["Screening phase"]],
            "eligibility_phase": [_clean(x) for x in lst["Eligibility phase"]],
            "database_status": [_clean(x) for x in lst["SCOC database"]],
        }
    )
    if (studies.study_citation == "").any():
        raise ContractError("list of studies: empty citation")
    studies["norm"] = studies.study_citation.map(_norm)
    studies["canon"] = studies.study_citation.map(_canon)
    if studies.canon.duplicated().any():
        raise ContractError("list of studies: two entries share a canonical form")
    by_canon = dict(zip(studies.canon, studies.study_key))

    resolved: dict[str, list[str]] = {}
    unmatched = []
    for r in sorted(data_refs):
        k = by_canon.get(_canon(r))
        if k is None:
            unmatched.append(r)
        else:
            resolved[r] = [k]

    covered: set[str] = set()
    for dprefix, sprefixes in CROSSWALK:
        hits = [r for r in unmatched if r.startswith(dprefix)]
        if not hits:
            raise ContractError(f"CROSSWALK data prefix matches nothing: {dprefix!r}")
        keys = []
        for sp in sprefixes:
            m = studies[studies.norm.str.startswith(sp)]
            if len(m) != 1:
                raise ContractError(f"CROSSWALK study prefix {sp!r} matches {len(m)} entries")
            keys.append(m.study_key.iloc[0])
        for r in hits:
            if r in covered:
                raise ContractError(f"CROSSWALK covers {r[:60]!r} twice")
            covered.add(r)
            resolved[r] = sorted(keys)
    left = set(unmatched) - covered
    if left:
        raise ContractError(
            f"{len(left)} data reference strings match no list entry and are not in "
            f"CROSSWALK, e.g. {sorted(left)[0]!r}"
        )
    if set(resolved) != data_refs:
        raise ContractError("reference resolution lost or invented data references")

    used = {k for ks in resolved.values() for k in ks}
    included = set(studies.study_key[studies.database_status == "included"])
    if (used - included) != KNOWN_USED_BUT_NOT_INCLUDED or (included - used):
        raise ContractError(
            f"studies used by data vs marked included in the list: used-not-included "
            f"{sorted(used - included)} (allowed {sorted(KNOWN_USED_BUT_NOT_INCLUDED)}), "
            f"included-unused {sorted(included - used)}"
        )
    for k in KNOWN_USED_BUT_NOT_INCLUDED:
        assert studies.set_index("study_key").loc[k, "database_status"] != "included"
    return studies, resolved


# ---------------------------------------------------------------- main build


_YEAR = re.compile(r"\((\d{4})\)")
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


def build_tables(
    raw: pd.DataFrame, lst: pd.DataFrame, access_date: str
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    cols = list(raw.columns)
    if len(cols) != len(DATA_COLUMNS):
        raise ContractError(f"{DATA_FILE}: {len(cols)} columns, expected {len(DATA_COLUMNS)}")
    for got, want in zip(cols, DATA_COLUMNS):
        if want == "Longitude":
            ok = got.startswith("Longitude")  # trailing byte is a mangled degree sign
        else:
            ok = got == want
        if not ok:
            raise ContractError(f"{DATA_FILE}: column {got!r}, expected {want!r}")
    units = re.fullmatch(r"SCOC \((.*)\)", cols[7])
    assert units is not None and units.group(1) == EXPECTED_UNITS
    n_source = len(raw)
    df = raw.copy()
    df.columns = SHORT
    df["row_idx"] = np.arange(len(df))  # 0-based data row, as read by pandas

    # ---- exclusions: fully blank rows only
    stripped = df[SHORT].apply(lambda c: c.str.strip())
    blank_all = (stripped == "").all(axis=1)
    skips = [
        {"data_row": int(i), "reason": "empty_row", "n_measurements": 0}
        for i in df.row_idx[blank_all]
    ]
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")
    d = df[~blank_all].copy()
    for c in SHORT:
        d[c] = pd.Series([_clean(v) for v in d[c]], index=d.index, dtype=object)
    for c in ("ref", "scoc", "region", "ocean", "drange"):
        if d[c].isna().any():
            raise ContractError(f"non-blank rows with NULL {c!r}: rows {d.row_idx[d[c].isna()].tolist()[:5]}")

    # duplicates: all members of a group of fully identical (stripped) source rows
    dup = d.duplicated(SHORT, keep=False)

    # ---- references / studies
    data_refs = {_norm(r) for r in d.ref}
    studies, resolved = build_studies(lst, data_refs)
    d["ref_norm"] = d.ref.map(_norm)
    d["reference_key"] = d.ref_norm.map(lambda r: "+".join(resolved[r]))

    # ---- numerics
    d["scoc_v"] = [_float(v, "SCOC") for v in d.scoc]
    lat = np.array([_float(v, "latitude") for v in d.lat])
    lon_raw = np.array([_float(v, "longitude") for v in d.lon])
    if (np.isnan(lat) != np.isnan(lon_raw)).any():
        raise ContractError("latitude and longitude are not missing together")
    lon = lon_raw.copy()
    wrapped = lon_raw > 180
    lon_strs = list(d.lon)
    for j in np.flatnonzero(wrapped):
        lon[j] = float(Decimal(lon_strs[j]) - 360)  # exact decimal arithmetic
    if not (np.nanmin(lat) >= -90 and np.nanmax(lat) <= 90):
        raise ContractError("latitude outside [-90, 90]")
    if not (np.nanmin(lon) >= -180 and np.nanmax(lon) <= 180):
        raise ContractError("longitude outside [-180, 180] after wrapping")
    depth = np.array([_float(v, "depth") for v in d.depth])
    if (depth[~np.isnan(depth)] <= 0).any():
        raise ContractError("non-positive depth (m)")
    grain = np.array([_float(v, "grain size") for v in d.grain])
    d["lat_v"], d["lon_v"], d["depth_v"], d["grain_v"] = lat, lon, depth, grain
    d["wrapped"] = wrapped

    # ---- attribute
    unknown_flux = set(d.flux.dropna()) - {"TOU", "DOU", "AOU"}
    if unknown_flux:
        raise ContractError(f"unknown TOU/DOU/AOU values {unknown_flux}")
    d["attribute"] = [ATTRIBUTE_BY_FLUX[f] for f in d.flux]

    # ---- method labels (raw); device and in/ex-situ must be missing together
    if (d.device.isna() != d.insitu.isna()).any():
        raise ContractError("device and in situ/ex situ are not NULL together")

    # ---- location
    d["location_key"] = (
        d.reference_key + "|" + d.region + "|" + d.lat.fillna("") + "|" + d.lon.fillna("")
        + "|" + d.depth.fillna("") + "|" + d.locq.fillna("")
    )  # locq in the key: one station has both exact and approximated rows
    loc_attrs = ["ocean", "locq", "drange", "lat_v", "lon_v", "depth_v"]
    nun = d.groupby("location_key")[loc_attrs].nunique(dropna=False)
    if (nun > 1).any().any():
        raise ContractError("a location_key has inconsistent attributes; widen the key")
    first = d.drop_duplicates("location_key").sort_values("location_key")
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "location_key": first.location_key,
            "site_name": first.region,
            "source_site_id": None,
            "source_core_id": first.location_key,  # the source has no core/station id
            "latitude": first.lat_v,
            "longitude": first.lon_v,
            "position_accuracy": np.nan,
            "position_accuracy_units": None,
            "position_method": first.locq,
            "country": None,
            "admin_division": None,
            "water_body": first.ocean,
            "habitat_type": first.drange,
            "geomorphic_setting": None,
            "bioregion": None,
            "marine_ecoregion": None,
            "salinity_class": None,
            "salinity_class_level": None,
            "vegetation_class": None,
            "vegetation_class_level": None,
            "tidal_position": None,
            "tidal_position_level": None,
            "modification_status": [[] for _ in range(len(first))],
            "dominant_species": [[] for _ in range(len(first))],
            "elevation": np.nan,  # water depth is water_depth_m, not an elevation
            "elevation_units": None,
            "elevation_datum": None,
            "elevation_method": None,
            "water_depth_m": first.depth_v,
            "distance_to_shore_km": np.nan,
        }
    )

    # ---- sample (one per data row)
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
            "sampling_year": None,
            "sampling_month": None,
            "taxon": None,
            "taxon_rank": None,
            "vegetated_or_bare": None,
            "marsh_zone": None,
            "sediment_type": d.sed,
            "photosynthetic_sediment": d.photo,
            **NO_WATER_CONTEXT,
        }
    )

    # ---- method
    d["method_key"] = (
        d.reference_key + "|" + d.attribute + "|" + d.device.fillna("") + "|"
        + d.insitu.fillna("") + "|" + d.dl.fillna("") + "|" + d.flux.fillna("")
    )
    mfirst = d.drop_duplicates("method_key").sort_values("method_key")
    method = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "method_key": mfirst.method_key,
            "source_method_id": None,
            "attribute": mfirst.attribute,
            "method_status": np.where(mfirst.device.isna(), "method_id_null", "matched"),
            "technique": mfirst.device,
            "coring_method": None,
            "carbon_type": None,
            "carbon_measured_or_modeled": None,
            "fraction_carbon_method": None,
            "carbonates_removed": None,
            "carbonate_removal_method": None,
            "conversion_applied": None,
            "in_situ_ex_situ": mfirst.insitu,
            "light_dark": mfirst.dl,
            "incubation_type": None,
            "experimental_status": None,
            "params": [json.dumps({"flux_type": f}, sort_keys=True) for f in mfirst.flux],
        }
    )
    mstatus = dict(zip(method.method_key, method.method_status))

    # ---- fact
    flags: list[list[str]] = []
    loc_flags: list[list[str]] = []  # flags about the station / source row, shared by the G1 fact
    not_incl = {k for k in studies.study_key[studies.database_status != "included"]}
    for r, w, dp in zip(d.itertuples(index=False), d.wrapped, dup):
        f: list[str] = []
        if r.scoc_v == 0:
            f.append("zero_value")
        if w:
            f.append("lon_wrapped")
        if np.isnan(r.lat_v):
            f.append("coords_missing")
        elif r.lat_v == 0 and r.lon_v == 0:
            f.append("lat_lon_zero")
        if r.locq == "approximated location":
            f.append("coords_approximate")
        if np.isnan(r.depth_v):
            f.append("depth_missing")
        if dp:
            f.append("duplicate_source_row")
        if not_incl & set(r.reference_key.split("+")):
            f.append("study_marked_excluded")
        loc_flags.append(sorted(x for x in f if x != "zero_value"))
        if mstatus[r.method_key] != "matched":
            f.append("method_missing")
        if r.flux is None:
            f.append("flux_type_missing")
        flags.append(sorted(f))
    if set(d.locq.dropna()) - {"exact location", "approximated location"}:
        raise ContractError(f"unknown Location labels {set(d.locq.dropna())}")
    fact = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "fact_key": d.sample_key + ":" + d.attribute,
            "reference_key": d.reference_key,
            "location_key": d.location_key,
            "sample_key": d.sample_key,
            "method_key": d.method_key,
            "attribute": d.attribute,
            "measurement_type": d.attribute.map(lambda a: ATTRIBUTE_DECL[a].measurement_type),
            "subfield": d.attribute.map(SUBFIELD_BY_ATTR),
            "value": d.scoc_v,
            "units_raw": EXPECTED_UNITS,
            "sd": np.nan,
            "se": np.nan,
            "n": None,
            "flags": flags,
            "source_row_ref": DATA_FILE + "#data_row=" + d.row_idx.astype(str) + ":" + d.attribute,
        }
    )
    fact["subfield"] = fact.subfield.astype(object).where(fact.subfield.notna(), None)

    # ---- G1 facts: one per data row with a median grain size (no method info: one method
    # row per study)
    g = d[d.grain_v.notna()]
    g_flags = [
        sorted(lf + (["zero_value"] if gv == 0 else []))
        for lf, gv, has in zip(loc_flags, d.grain_v, d.grain_v.notna()) if has
    ]
    g_method_key = g.reference_key + "|" + GRAIN_ATTR
    gm = pd.DataFrame({"reference_key": g.reference_key, "method_key": g_method_key}).drop_duplicates(
        "method_key"
    ).sort_values("method_key")
    grain_method = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "method_key": gm.method_key,
            "source_method_id": None,
            "attribute": GRAIN_ATTR,
            "method_status": "method_id_null",
            "technique": None, "coring_method": None, "carbon_type": None,
            "carbon_measured_or_modeled": None, "fraction_carbon_method": None,
            "carbonates_removed": None, "carbonate_removal_method": None,
            "conversion_applied": None, "in_situ_ex_situ": None, "light_dark": None,
            "incubation_type": None, "experimental_status": None,
            "params": json.dumps({"source_column": "median sediment grain size (?m)"}, sort_keys=True),
        }
    )
    grain_fact = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "fact_key": g.sample_key + ":" + GRAIN_ATTR,
            "reference_key": g.reference_key,
            "location_key": g.location_key,
            "sample_key": g.sample_key,
            "method_key": g_method_key,
            "attribute": GRAIN_ATTR,
            "measurement_type": ATTRIBUTE_DECL[GRAIN_ATTR].measurement_type,
            "subfield": "median_grain_size",
            "value": g.grain_v,
            "units_raw": GRAIN_UNITS,
            "sd": np.nan,
            "se": np.nan,
            "n": None,
            "flags": g_flags,
            "source_row_ref": DATA_FILE + "#data_row=" + g.row_idx.astype(str) + ":" + GRAIN_ATTR,
        }
    )
    n_o2_facts = len(fact)
    fact = pd.concat([fact, grain_fact], ignore_index=True)
    method = pd.concat([method, grain_method], ignore_index=True)
    flags = [list(f) for f in fact["flags"]]

    # ---- reference / citation / study_screening
    ref_keys = sorted(set(d.reference_key))
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_keys})
    study_ref = {k: rk for rk in ref_keys for k in rk.split("+")}
    cit = studies[studies.study_key.isin(study_ref)].copy()
    cit["reference_key"] = cit.study_key.map(study_ref)

    def year(s: str) -> int | None:
        m = _YEAR.findall(s)
        return int(m[-1]) if m else None

    def doi(s: str) -> str | None:
        m = _DOI.search(s)
        return m.group(0).rstrip(".;,") if m else None

    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "citation_key": cit.study_key,
            "reference_key": cit.reference_key,
            "bibliography_id": cit.study_key,
            "publication_type": None,
            "doi": cit.study_citation.map(doi),
            "title": cit.study_citation,
            "authors": None,
            "year": pd.Series([year(s) for s in cit.study_citation], index=cit.index, dtype="Int64"),
            "url": None,
            "openalex_id": None,
            "doi_resolved": None,
            "doi_resolution_source": None,
        }
    )
    screening = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "study_key": studies.study_key,
            "study_citation": studies.study_citation,
            "screening_phase": studies.screening_phase,
            "eligibility_phase": studies.eligibility_phase,
            "database_status": studies.database_status,
            "reference_key": studies.study_key.map(study_ref),
        }
    )
    screening["reference_key"] = screening.reference_key.where(screening.reference_key.notna(), None)

    source = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY,
                "name": SOURCE_NAME,
                "version": SOURCE_VERSION,
                "doi": SOURCE_DOI,
                "access_date": access_date,
                "license": None,
                "notes": "Stratmann, Soetaert, Wei et al. (2022), Dryad. Sediment community oxygen "
                "consumption (TOU/DOU/AOU) in marine sediments, all depths; mostly not coastal wetlands.",
            }
        ]
    )

    tables = {
        "source": source, "reference": reference, "citation": citation,
        "study_screening": screening, "location": loc.reset_index(drop=True),
        "sample": sample, "method": method, "fact": fact,
    }
    flag_counts: dict[str, int] = {}
    for f in flags:
        for x in f:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    report = {
        "source_rows": n_source,
        "facts": len(fact),
        "facts_excluded": len(skips),
        "grain_size_facts": len(grain_fact),
        "facts_by_attribute": fact.attribute.value_counts().to_dict(),
        "samples": len(sample),
        "locations": len(loc),
        "methods": len(method),
        "method_status": method.method_status.value_counts().to_dict(),
        "references": len(reference),
        "citations": len(citation),
        "study_screening": len(screening),
        "study_screening_with_reference": int(screening.reference_key.notna().sum()),
        "flag_counts": flag_counts,
    }
    # accounting: every source row is a fact or a listed exclusion
    if n_o2_facts + report["facts_excluded"] != n_source:
        raise ContractError("O2 facts + excluded rows != source rows")
    if n_o2_facts != len(d):
        raise ContractError("O2 fact count != non-blank data rows")
    if len(fact) != n_o2_facts + int(d.grain_v.notna().sum()):
        raise ContractError("fact count != O2 facts + non-blank grain-size cells")
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the files were downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    raw = _read_csv(args.raw_dir / DATA_FILE)
    lst = _read_csv(args.raw_dir / LIST_FILE)
    tables, skips, report = build_tables(raw, lst, args.access_date)
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
