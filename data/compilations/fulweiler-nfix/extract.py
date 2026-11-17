#!/usr/bin/env python3
"""Fulweiler et al. global dataset of aquatic nitrogen fixation rates (EDI edi.1759.2) -> contract tables.

Fulweiler, R.W., Berberich, M.E., Rinehart, S.A., Taylor, J.M., Kelly, M.C., Ray, N.E., Oczkowski, A., Benavides, M.,
Church, M.J., Loeks, B., Newell, S., Olofsson, M., Oppong, J., Roley, S.S., Vizza, C., Wilson, S.T., Scott, J.T.,
Marcarelli, A.M. (2025): Global dataset of nitrogen fixation rates across inland and coastal waters based on a
coordinated synthesis effort, ver 2. Environmental Data Initiative, doi:10.6073/pasta/333f651ca721da657d5fd0c393d26cf8
(package edi.1759.2; the EML ``edi.1759.2.xml`` gives CC BY 4.0). Raw files (this directory): ``aquatic_N2fix_rates.csv``
(4,793 rows, one rate each), ``aquatic_N2fix_ancillary.csv`` (3,238 rows keyed by ``nfix_rate_id``, a subset of the
rates), ``aquatic_N2fix_references.csv`` (267 references), ``data_dictionary.csv`` (authoritative units and meanings;
read, not parsed). ``..._with_abstracts.csv``, the EML/report xml and the R script are not loaded.

D1 (harmonised value). The fact carries the SOURCE'S CONVERTED molar rate ``nfix_rate_converted`` in the unit
``nfix_unit_converted`` (four declared spellings). Everything needed to recover the original sits in a per-source-row
``method_dim`` row (method_key = ``nfix_rate_id``): ``params`` holds the original rate, error and unit, the ARA
conversion factor, the incubation time and temperature, day, hour, season, the substrate and its details and the
reference's extraction location; ``conversion_applied`` states the original -> converted values when they differ
(flag ``converted_by_source``). ``technique`` = ``nfix_method`` (ARA, 15N2, N2/Ar). The 174 N2/Ar rows whose original
is negative are stored positive by the source (sign flipped); counted in the report.

Attribute by ``substrate``: ``wc`` -> ``fulweiler_n2fix_water`` (C5 n2_fixation); ``benthos`` and ``other`` (plants,
stems, epiphytes) -> ``fulweiler_n2fix_benthic`` (C7 n2_fixation). ``nfix_error_converted``: type ``stdev.`` -> ``sd``,
``std. err`` -> ``se``; ``C.I.`` and ``range`` are not an SD or SE: no sd/se, the value and type go to params and the
fact gets ``error_type_other``. A value with NO type is assumed to be an SD (the user's rule, 2026-11-17: default to SD,
flag it, note it): it goes in ``sd``, the fact gets ``error_type_missing`` and its method row carries
``notes`` = ``SD_ASSUMED_NOTE``; any other type string raises;
``nfix_n`` -> ``n`` (blank 68).

Habitat (D3). Raw ``habitat_type`` = the ``habitat`` column for ``benthos`` / ``other`` rows and ``<habitat> | wc``
for ``wc`` rows (both written into ``location_key``, rule 3.10). The label map sends salt marshes, mangroves, tidal
flats, seagrasses to their standard habitat, every ``... | wc`` label to ``Water column`` (these facts carry
``habitat_from_scope``: no source states it), ``estuaries`` / ``continental shelves`` (and their wc forms) to a
``setting`` only, and lakes, rivers and freshwater wetlands to nothing (the inland rows are loaded so the database
mirrors the compilation).

Samples. The plan's grain is reference + lat + lon + date + sample depth + substrate; ``sample_key`` here adds the
other fields stored on the sample row (substrate details, salinity and its raw unit), so the row is single-valued.
Rows with no ancillary row have no salinity. ``sample_depth`` is "depth of site, sample, or sediment" (ambiguous): it
goes to the numeric ``sample_depth_m`` only for ``wc`` rows and is otherwise kept as text in ``depth_notes``
together with the substrate and its details. Year and month blank -> NULL (flag ``year_missing`` for the year).
Day, hour and season are in the method params.

J1 (water concentrations from the ancillary file; rules of INTEGRATION_PLAN 1.2). One fact per (sample,
distinct value): ``NH4_wc`` water_ammonium, ``NO3_wc`` water_nitrate, ``NO2_wc`` water_nitrite, ``NOx_wc``
water_nitrate_nitrite, ``DIN_wc`` water_din, ``DIP_wc`` water_phosphate, ``DO_wc`` water_o2, ``chla_wc`` water_chl_a,
``pH`` water_ph (unit ``unitless``, from the data dictionary). Units are the ancillary file's unit cells verbatim
(whitespace normalised; 2 to 13 spellings per attribute, some areal or percent: the J1 attribute is not
unit-homogeneous). A value with no unit cell is skipped (``units_missing``; 4 ``chla_wc`` cells). Repeats of one value
in a sample collapse into the first row's fact and are counted; two different values in one sample are both kept with
``covariate_conflict``. Salinity (+ its unit cell) goes to the sample slot; a value with no unit keeps NULL units and the
facts of that row carry ``salinity_units_missing``. Not loaded (phase 2): conductivity, PAR, flow, residence time,
water table, primary production, DIN flux, POC_wc, PON_wc and every ``*_sed`` column (counts in the report).

References: ``reference_id`` (R1..R267) is the study id and ``reference_key``; the references file supplies DOI (lower
case), url, authors, title, year; ``bibliography_id`` = the shorthand ``reference``. Journal, volume and pages are not loaded.

Flags: zero_value, year_missing, duplicate_source_row (rate rows identical in every column except ``nfix_rate_id``,
both or all copies flagged, as Chang), habitat_from_scope, converted_by_source, error_type_other, error_type_missing,
salinity_units_missing, covariate_conflict.

Provenance: ``source_row_ref`` = ``<file>#data_row=<0-based data row>:<attribute>`` in the rates file (rate facts) or the
ancillary file (J1 facts; the first row of the sample carrying the value).

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from _g3_common import collapse_covariates, count_flags, method_row, norm_ws  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "fulweiler-nfix"
ERROR_TYPES_STATED = ("stdev.", "std. err", "C.I.", "range")  # the types the source names; no type = not in this tuple
NO_TYPE_CODES = ("NA",)  # the source's code for "no error type" (besides an empty cell)


def _no_error_type(v: object) -> bool:
    return v is None or v != v or v in NO_TYPE_CODES  # v != v: NaN
SD_ASSUMED_NOTE = ("error value has no error type in the source; assumed to be a standard deviation "
                   "(flag error_type_missing)")
SOURCE_NAME = "Global dataset of nitrogen fixation rates across inland and coastal waters (Fulweiler et al.)"
SOURCE_DOI = "10.6073/pasta/333f651ca721da657d5fd0c393d26cf8"
SOURCE_VERSION = "EDI edi.1759.2 (ver 2)"
SOURCE_LICENSE = "CC BY 4.0"  # EML intellectualRights of edi.1759.2.xml
RATES_FILE, ANC_FILE, REFS_FILE = "aquatic_N2fix_rates.csv", "aquatic_N2fix_ancillary.csv", "aquatic_N2fix_references.csv"
CITATION = (
    "Fulweiler, R.W., Berberich, M.E., Rinehart, S.A., Taylor, J.M., Kelly, M.C., Ray, N.E., Oczkowski, A., Benavides, "
    "M., Church, M.J., Loeks, B., Newell, S., Olofsson, M., Oppong, J., Roley, S.S., Vizza, C., Wilson, S.T., Scott, "
    "J.T., Marcarelli, A.M. (2025): Global dataset of nitrogen fixation rates across inland and coastal waters based on "
    "a coordinated synthesis effort, ver 2 [Dataset]. Environmental Data Initiative, "
    "https://doi.org/10.6073/pasta/333f651ca721da657d5fd0c393d26cf8 (package edi.1759.2)"
)
ALLOWED_SKIP_REASONS = frozenset({"units_missing"})
check_skip_reasons(ALLOWED_SKIP_REASONS)

RATE_COLUMNS = [
    "nfix_rate_id", "reference_id", "site_name", "latitude", "longitude", "habitat", "year", "month", "day",
    "hour_minute", "season", "substrate", "substrate_details", "sample_depth", "nfix_rate_converted",
    "nfix_error_converted", "nfix_unit_converted", "nfix_error_type", "nfix_n", "nfix_rate_original",
    "nfix_error_original", "nfix_unit_original", "nfix_method", "ARA_conversion_factor", "nfix_incubation_time",
    "nfix_incubation_temp",
]
REF_COLUMNS = ["reference_id", "reference", "doi", "doi_data", "url", "publication_year", "authors", "title",
               "publication", "volume", "pages", "extraction_location", "extraction_location_details"]
ANC_J1 = {  # column -> (attribute, unit column or None for a fixed unit)
    "NH4_wc": ("water_ammonium", "NH4_wc_units"), "NO3_wc": ("water_nitrate", "NO3_wc_units"),
    "NO2_wc": ("water_nitrite", "NO2_wc_units"), "NOx_wc": ("water_nitrate_nitrite", "NOx_wc_units"),
    "DIN_wc": ("water_din", "DIN_wc_units"), "DIP_wc": ("water_phosphate", "DIP_wc_units"),
    "DO_wc": ("water_o2", "DO_wc_units"), "chla_wc": ("water_chl_a", "chla_wc_units"),
    "pH": ("water_ph", None),
}
PH_UNIT = "unitless"  # data_dictionary.csv: pH, unit unitless
ANC_SLOT = ["salinity", "salinity_units"]
ANC_NOT_LOADED = [  # value columns of the ancillary file that are not loaded (phase 2)
    "conductivity", "PAR", "flow", "systemResidenceTime_d", "waterTableDepth", "PrimaryProduction", "DINFlux", "PON_wc",
    "POC_wc", "chla_sed", "NH4_sed", "NO3_sed", "NO2_sed", "NOx_sed", "DIP_sed", "percLOI_sed", "CN_sed", "Fe_sed",
    "Mo_sed", "sulfate_sed", "sulfide_sed",
]
ANC_NOT_LOADED_TEXT = ["Fe_sed_type"]
ANC_COLUMNS = (
    ["nfix_rate_id", "reference_id", "salinity", "salinity_units", "pH", "conductivity", "conductivity_units", "PAR",
     "PAR_units", "flow", "flow_units", "systemResidenceTime_d", "waterTableDepth", "waterTableDepth_units",
     "PrimaryProduction", "PrimaryProduction_units", "DINFlux", "DINFlux_units", "chla_wc", "chla_wc_units", "DO_wc",
     "DO_wc_units", "NH4_wc", "NH4_wc_units", "NO3_wc", "NO3_wc_units", "NO2_wc", "NO2_wc_units", "NOx_wc",
     "NOx_wc_units", "DIN_wc", "DIN_wc_units", "PON_wc", "PON_wc_units", "DIP_wc", "DIP_wc_units", "POC_wc",
     "POC_wc_units", "chla_sed", "chla_sed_units", "NH4_sed", "NH4_sed_units", "NO3_sed", "NO3_sed_units", "NO2_sed",
     "NO2_sed_units", "NOx_sed", "NOx_sed_units", "DIP_sed", "DIP_sed_units", "percLOI_sed", "CN_sed", "CN_sed_units",
     "Fe_sed", "Fe_sed_type", "Fe_sed_units", "Mo_sed", "Mo_sed_units", "sulfate_sed", "sulfate_sed_units",
     "sulfide_sed", "sulfide_sed_units"]
)
SUBSTRATES = {"wc", "benthos", "other"}
HABITATS = {"continental shelves", "estuaries", "lakes", "rivers", "salt marshes", "freshwater wetlands",
            "tidal flats", "seagrasses", "mangroves"}
ERROR_TYPES = {"stdev.", "std. err", "C.I.", "range", "NA"}
METHODS = {"ARA", "15N2", "N2/Ar"}
_NUM = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")


def na(v: str) -> bool:
    return v == "NA"


def num(v: str, what: str) -> float | None:
    """'NA' -> None; a number -> float; anything else (including blank) raises."""
    if na(v):
        return None
    if not _NUM.fullmatch(v):
        raise ContractError(f"{what}: unrecognised value {v!r}")
    return float(v)


def txt(v: str) -> str | None:
    return None if na(v) else norm_ws(v)


def read_raw(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    kw = dict(dtype=str, keep_default_na=False, na_values=[], encoding="utf-8-sig")
    rates = pd.read_csv(raw_dir / RATES_FILE, **kw)  # type: ignore[call-overload]
    anc = pd.read_csv(raw_dir / ANC_FILE, **kw)  # type: ignore[call-overload]
    refs = pd.read_csv(raw_dir / REFS_FILE, **kw)  # type: ignore[call-overload]
    return rates, anc, refs


# ---------------------------------------------------------------- main build


def build_tables(
    rates: pd.DataFrame, anc: pd.DataFrame, refs: pd.DataFrame, access_date: str
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if list(rates.columns) != RATE_COLUMNS:
        raise ContractError(f"{RATES_FILE}: unexpected columns {list(rates.columns)}")
    if list(anc.columns) != ANC_COLUMNS:
        raise ContractError(f"{ANC_FILE}: unexpected columns {list(anc.columns)}")
    if list(refs.columns) != REF_COLUMNS:
        raise ContractError(f"{REFS_FILE}: unexpected columns {list(refs.columns)}")
    n_rows = len(rates)
    if not rates.nfix_rate_id.is_unique or not anc.nfix_rate_id.is_unique or not refs.reference_id.is_unique:
        raise ContractError("an id column is not unique")
    rid = list(rates.nfix_rate_id)
    row_of = {k: i for i, k in enumerate(rid)}
    if not set(anc.nfix_rate_id) <= set(rid):
        raise ContractError("ancillary rows for unknown rate ids")
    ref_ids = set(refs.reference_id)
    if set(rates.reference_id) != ref_ids:
        raise ContractError("reference ids in the rates file and the references file differ")
    anc_row_of_rate = {k: j for j, k in enumerate(anc.nfix_rate_id)}
    for j, (k, rf) in enumerate(zip(anc.nfix_rate_id, anc.reference_id)):
        if rates.reference_id.iat[row_of[k]] != rf:
            raise ContractError(f"ancillary row {j}: reference differs from its rate row")

    ref = list(rates.reference_id)
    site = [txt(v) for v in rates.site_name]
    if any(s is None for s in site):
        raise ContractError("a rate row with no site_name")
    lat = [num(v, "latitude") for v in rates.latitude]
    lon = [num(v, "longitude") for v in rates.longitude]
    if any(a is None or b is None or not (-90 <= a <= 90) or not (-180 <= b <= 180) for a, b in zip(lat, lon)):
        raise ContractError("missing or out-of-range coordinates")
    if any(a == 0 and b == 0 for a, b in zip(lat, lon)):
        raise ContractError("a row at 0, 0")
    hab, sub = list(rates.habitat), list(rates.substrate)
    if set(hab) - HABITATS or set(sub) - SUBSTRATES:
        raise ContractError(f"unknown habitat or substrate: {set(hab) - HABITATS} {set(sub) - SUBSTRATES}")
    hab_raw = [f"{h} | wc" if s == "wc" else h for h, s in zip(hab, sub)]
    year = [None if (v := num(y, "year")) is None else int(v) for y in rates.year]
    month = [None if (v := num(y, "month")) is None else int(v) for y in rates.month]
    day = [txt(v) for v in rates.day]
    if any(m is not None and not 1 <= m <= 12 for m in month):
        raise ContractError("month outside 1..12")
    sdepth = [num(v, "sample_depth") for v in rates.sample_depth]
    if any(d is not None and d < 0 for d in sdepth):
        raise ContractError("negative sample_depth")
    details = [txt(v) for v in rates.substrate_details]
    rate = [num(v, "nfix_rate_converted") for v in rates.nfix_rate_converted]
    if any(x is None or x < 0 for x in rate):
        raise ContractError("a missing or negative converted rate")
    err = [num(v, "nfix_error_converted") for v in rates.nfix_error_converted]
    if any(e is not None and e < 0 for e in err):
        raise ContractError("a negative error")
    etype = list(rates.nfix_error_type)
    if set(etype) - ERROR_TYPES or set(rates.nfix_method) - METHODS:
        raise ContractError("unknown error type or method")
    nn = [num(v, "nfix_n") for v in rates.nfix_n]
    if any(x is not None and (x < 1 or x != int(x)) for x in nn):
        raise ContractError("nfix_n is not a positive integer")
    unit = [norm_ws(v) for v in rates.nfix_unit_converted]
    orig_rate = [num(v, "nfix_rate_original") for v in rates.nfix_rate_original]
    orig_unit = [norm_ws(v) for v in rates.nfix_unit_original]
    if any(x is None for x in orig_rate):
        raise ContractError("a missing original rate")
    dup = rates.drop(columns="nfix_rate_id").duplicated(keep=False).to_numpy()

    # ---- ancillary salinity slot (per rate row) and J1 cells
    sal: list[float | None] = [None] * n_rows
    sal_units: list[str | None] = [None] * n_rows
    sal_units_missing = [False] * n_rows
    n_unit_without_value = 0
    for j, k in enumerate(anc.nfix_rate_id):
        i = row_of[k]
        sal[i] = num(anc.salinity.iat[j], "salinity")
        u = txt(anc.salinity_units.iat[j])
        if sal[i] is None and u is not None:  # a unit cell with no value: nothing to attach it to (counted)
            n_unit_without_value += 1
            u = None
        sal_units[i] = u
        sal_units_missing[i] = sal[i] is not None and u is None

    j1_raw: list[tuple[int, str, float, str, int]] = []  # (rate row, attribute, value, unit, ancillary row)
    skips: list[dict] = []
    j1_cells_total = 0
    for col, (attr, ucol) in ANC_J1.items():
        for j, v in enumerate(anc[col]):
            x = num(v, f"{ANC_FILE} {col}")
            if x is None:
                continue
            j1_cells_total += 1
            u = PH_UNIT if ucol is None else txt(anc[ucol].iat[j])
            if u is None:
                skips.append({"data_row": j, "attribute": attr, "reason": "units_missing", "cell": v})
                continue
            j1_raw.append((row_of[anc.nfix_rate_id.iat[j]], attr, x, u, j))
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")
    not_loaded = {c: int(sum(1 for v in anc[c] if not na(v))) for c in ANC_NOT_LOADED}
    not_loaded.update({c: int(sum(1 for v in anc[c] if not na(v))) for c in ANC_NOT_LOADED_TEXT})
    # every unit column that is not the salinity / J1 / not-loaded set must be accounted for
    accounted = set(ANC_J1) | {c for _a, c in ANC_J1.values() if c} | set(ANC_SLOT) | set(ANC_NOT_LOADED) | set(
        ANC_NOT_LOADED_TEXT) | {"nfix_rate_id", "reference_id"}
    leftover = [c for c in ANC_COLUMNS if c not in accounted and not c.endswith("_units") and not c.endswith("_type")]
    if leftover:
        raise ContractError(f"ancillary columns not accounted for: {leftover}")

    # ---- samples
    def f(x: float | None) -> str:
        return "" if x is None else repr(float(x))

    plan_key = ["|".join([ref[i], rates.latitude.iat[i], rates.longitude.iat[i], rates.year.iat[i], rates.month.iat[i],
                          rates.day.iat[i], rates.sample_depth.iat[i], sub[i]]) for i in range(n_rows)]
    loc_key = [f"{hab_raw[i]}|{site[i]}|{f(lat[i])}|{f(lon[i])}" for i in range(n_rows)]
    sample_key = [plan_key[i] + "|" + "|".join([details[i] or "", f(sal[i]), sal_units[i] or ""]) + "||" + loc_key[i]
                  for i in range(n_rows)]
    first_of_sample: dict[str, int] = {}
    for i, k in enumerate(sample_key):
        first_of_sample.setdefault(k, i)
    skeys = sorted(first_of_sample)
    idx = [first_of_sample[k] for k in skeys]
    for i in range(n_rows):  # single-valued sample rows
        g = first_of_sample[sample_key[i]]
        if (year[i], month[i], sdepth[i], sub[i], sal[i], sal_units[i]) != (year[g], month[g], sdepth[g], sub[g], sal[g], sal_units[g]):
            raise ContractError(f"row {i}: sample fields differ inside one sample key")

    def depth_notes(i: int) -> str:
        parts = [f"substrate: {sub[i]}"]
        if details[i]:
            parts.append(f"details: {details[i]}")
        if sub[i] != "wc" and sdepth[i] is not None:
            parts.append(f"sample_depth (depth of site, sample or sediment, m): {f(sdepth[i])}")
        return "; ".join(parts)

    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "sample_key": skeys, "source_sample_id": None,
            "depth_min_cm": np.nan, "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan, "representative_depth_max_cm": np.nan,
            "depth_notes": [depth_notes(i) for i in idx],
            "sampling_year": pd.array([year[i] for i in idx], dtype="Int64"),
            "sampling_month": pd.array([month[i] for i in idx], dtype="Int64"),
            "taxon": None, "taxon_rank": None, "vegetated_or_bare": None, "marsh_zone": None,
            "sediment_type": None, "photosynthetic_sediment": None,
            "sample_depth_m": [np.nan if sub[i] != "wc" or sdepth[i] is None else sdepth[i] for i in idx],
            "water_temperature_c": np.nan,
            "water_salinity": [np.nan if sal[i] is None else sal[i] for i in idx],
            "water_salinity_units": [sal_units[i] for i in idx],
            "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
        }
    )

    # ---- locations
    first_loc: dict[str, int] = {}
    for i, k in enumerate(loc_key):
        first_loc.setdefault(k, i)
    lkeys = sorted(first_loc)
    li = [first_loc[k] for k in lkeys]
    n = len(lkeys)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": [site[i] for i in li],
            "source_site_id": None, "source_core_id": lkeys, "latitude": [lat[i] for i in li],
            "longitude": [lon[i] for i in li], "position_accuracy": np.nan, "position_accuracy_units": None,
            "position_method": None, "country": None, "admin_division": None, "water_body": None,
            "habitat_type": [hab_raw[i] for i in li], "geomorphic_setting": None, "bioregion": None,
            "marine_ecoregion": None, "salinity_class": None, "salinity_class_level": None,
            "vegetation_class": None, "vegetation_class_level": None, "tidal_position": None,
            "tidal_position_level": None, "modification_status": [[] for _ in range(n)],
            "dominant_species": [[] for _ in range(n)], "elevation": np.nan, "elevation_units": None,
            "elevation_datum": None, "elevation_method": None, "water_depth_m": np.nan, "distance_to_shore_km": np.nan,
        }
    )
    sample_locs: dict[str, set[str]] = {}
    for i in range(n_rows):
        sample_locs.setdefault(sample_key[i], set()).add(loc_key[i])
    n_samples_multi_loc = sum(1 for v in sample_locs.values() if len(v) > 1)
    if n_samples_multi_loc:
        raise ContractError("a sample spans more than one location")

    # ---- per-row methods (D1)
    refx = refs.set_index("reference_id")
    method_rows: list[dict] = []
    # an error value with NO error type: assumed to be a standard deviation (user rule, 2026-11-17)
    sd_assumed = [err[i] is not None and etype[i] not in ERROR_TYPES_STATED for i in range(n_rows)]
    converted = [False] * n_rows
    n_sign_flipped = 0
    for i in range(n_rows):
        converted[i] = not (orig_rate[i] == rate[i] and orig_unit[i] == unit[i])
        n_sign_flipped += orig_rate[i] < 0 < rate[i]  # type: ignore[operator]
        rr = refx.loc[ref[i]]
        params = {
            "nfix_rate_id": rid[i], "rate_original": rates.nfix_rate_original.iat[i], "unit_original": orig_unit[i],
            "error_original": txt(rates.nfix_error_original.iat[i]), "error_converted": txt(rates.nfix_error_converted.iat[i]),
            "error_type": txt(etype[i]), "ARA_conversion_factor": txt(rates.ARA_conversion_factor.iat[i]),
            "incubation_time": txt(rates.nfix_incubation_time.iat[i]),
            "incubation_temp_c": txt(rates.nfix_incubation_temp.iat[i]), "day": day[i],
            "hour_minute": txt(rates.hour_minute.iat[i]), "season": txt(rates.season.iat[i]), "substrate": sub[i],
            "substrate_details": details[i], "habitat": hab[i],
            "extraction_location": txt(rr.extraction_location),
            "extraction_location_details": txt(rr.extraction_location_details), "data_archive_doi": txt(rr.doi_data),
        }
        conv = (f"source converted: {rates.nfix_rate_original.iat[i]} {orig_unit[i]} -> "
                f"{rates.nfix_rate_converted.iat[i]} {unit[i]}") if converted[i] else None
        method_rows.append(method_row(
            SOURCE_KEY, rid[i], "fulweiler_n2fix_water" if sub[i] == "wc" else "fulweiler_n2fix_benthic",
            status="matched", technique=rates.nfix_method.iat[i], conversion_applied=conv,
            params=json.dumps(params, sort_keys=True), notes=SD_ASSUMED_NOTE if sd_assumed[i] else None,
        ))
    j1_attrs = sorted({a for a, _c in ANC_J1.values()})
    for a in j1_attrs:
        method_rows.append(method_row(SOURCE_KEY, a, a, status="method_id_null"))

    # ---- facts
    def row_flags(i: int) -> list[str]:
        fl = []
        if year[i] is None:
            fl.append("year_missing")
        if sub[i] == "wc":
            fl.append("habitat_from_scope")
        if sal_units_missing[i]:
            fl.append("salinity_units_missing")
        return fl

    rows: list[dict] = []
    n_err_other = n_err_missing = 0
    for i in range(n_rows):
        attr = "fulweiler_n2fix_water" if sub[i] == "wc" else "fulweiler_n2fix_benthic"
        decl = ATTRIBUTE_DECL[attr]
        if unit[i] not in decl.units:
            raise ContractError(f"{attr}: unit {unit[i]!r} not declared")
        fl = row_flags(i)
        if rate[i] == 0:
            fl.append("zero_value")
        if dup[i]:
            fl.append("duplicate_source_row")
        if converted[i]:
            fl.append("converted_by_source")
        sd = se = np.nan
        if err[i] is not None:
            if etype[i] == "stdev.":
                sd = err[i]
            elif etype[i] == "std. err":
                se = err[i]
            elif etype[i] in ("C.I.", "range"):
                fl.append("error_type_other")
                n_err_other += 1
            else:
                if not _no_error_type(etype[i]):
                    raise ContractError(f"{rid[i]}: unlisted error type {etype[i]!r}")
                sd = err[i]  # assumed SD; flagged, and noted in method.notes
                fl.append("error_type_missing")
                n_err_missing += 1
        (sub_f,) = decl.subfields
        rows.append(
            {
                "source_key": SOURCE_KEY, "fact_key": f"{rid[i]}:{attr}", "reference_key": ref[i],
                "location_key": loc_key[i], "sample_key": sample_key[i], "method_key": rid[i], "attribute": attr,
                "measurement_type": decl.measurement_type, "subfield": sub_f, "value": float(rate[i]),  # type: ignore[arg-type]
                "units_raw": unit[i], "sd": sd, "se": se, "n": None if nn[i] is None else int(nn[i]),
                "flags": sorted(fl), "source_row_ref": f"{RATES_FILE}#data_row={i}:{attr}",
            }
        )
    n_rate_facts = len(rows)

    # J1: one fact per (sample, attribute, distinct (value, unit)); a value in two units is two values
    j1_cells = [(sample_key[i], a, (x, u), j) for i, a, x, u, j in j1_raw]
    plan_cells = [(plan_key[i], a, (x, u), j) for i, a, x, u, j in j1_raw]
    kept, n_collapsed = collapse_covariates(j1_cells)
    kept_plan, n_collapsed_plan = collapse_covariates(plan_cells)
    anc_to_row = {j: row_of[anc.nfix_rate_id.iat[j]] for j in range(len(anc))}
    for sk, attr, (x, u), j, conflict in kept:
        i = anc_to_row[j]
        decl = ATTRIBUTE_DECL[attr]
        if u not in decl.units:
            raise ContractError(f"{attr}: unit {u!r} not declared")
        fl = row_flags(i)
        if x == 0:
            fl.append("zero_value")
        if conflict:
            fl.append("covariate_conflict")
        (sub_f,) = decl.subfields
        rows.append(
            {
                "source_key": SOURCE_KEY, "fact_key": f"{sk}:{attr}:{x!r}:{u}", "reference_key": ref[i],
                "location_key": loc_key[i], "sample_key": sk, "method_key": attr, "attribute": attr,
                "measurement_type": "J1", "subfield": sub_f, "value": float(x), "units_raw": u,
                "sd": np.nan, "se": np.nan, "n": None, "flags": sorted(fl),
                "source_row_ref": f"{ANC_FILE}#data_row={j}:{attr}",
            }
        )
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array(list(fact["n"]), dtype="Int64")
    method = pd.DataFrame(method_rows)
    # drop unused rows of sample / location / method (only rows a fact references exist)
    sample = sample[sample.sample_key.isin(set(fact.sample_key))].reset_index(drop=True)
    loc = loc[loc.location_key.isin(set(fact.location_key))].reset_index(drop=True)
    method = method[method.method_key.isin(set(fact.method_key))].sort_values("method_key").reset_index(drop=True)

    # ---- references
    def doi_of(v: str) -> str | None:
        if na(v):
            return None
        d = re.sub(r"^https?://(dx\.)?doi\.org/", "", v.strip(), flags=re.I).lower()
        if not d.startswith("10."):
            raise ContractError(f"unexpected DOI {v!r}")
        return d

    refs_sorted = refs.sort_values("reference_id").reset_index(drop=True)
    reference_df = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": refs_sorted.reference_id})
    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "citation_key": [f"{r}|0" for r in refs_sorted.reference_id],
            "reference_key": refs_sorted.reference_id, "bibliography_id": [txt(v) for v in refs_sorted.reference],
            "publication_type": None, "doi": [doi_of(v) for v in refs_sorted.doi],
            "title": [txt(v) for v in refs_sorted.title], "authors": [txt(v) for v in refs_sorted.authors],
            "year": pd.array([None if (y := num(v, "publication_year")) is None else int(y)
                              for v in refs_sorted.publication_year], dtype="Int64"),
            "url": [txt(v) for v in refs_sorted.url], "openalex_id": None, "doi_resolved": None,
            "doi_resolution_source": None,
        }
    )
    source = pd.DataFrame(
        [{"source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
          "access_date": access_date, "license": SOURCE_LICENSE,
          "notes": CITATION + " License from the package's EML (CC BY 4.0); version is the package id's revision."}]
    )
    tables = {
        "source": source, "reference": reference_df, "citation": citation,
        "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
        "method": method, "fact": fact,
    }

    # ---- accounting
    n_j1_cells_with_unit = len(j1_raw)
    n_j1_facts = len(kept)
    if n_rate_facts != n_rows:
        raise ContractError("rate facts != rate rows")
    if len(fact) != n_rate_facts + n_j1_facts:
        raise ContractError("fact count mismatch")
    if j1_cells_total != n_j1_facts + n_collapsed + len(skips):
        raise ContractError("J1 cells != J1 facts + collapsed + skipped")
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference_df.reference_key),
        ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    report = {
        "source_rows": n_rows, "ancillary_rows": len(anc), "facts": len(fact), "skipped": len(skips),
        "rate_facts": n_rate_facts, "j1_facts": n_j1_facts, "j1_collapsed": n_collapsed,
        "j1_numeric_cells": j1_cells_total, "j1_cells_with_unit": n_j1_cells_with_unit,
        "j1_facts_under_plan_key": len(kept_plan), "j1_collapsed_under_plan_key": n_collapsed_plan,
        "j1_conflict_facts": int(sum(1 for *_x, c in kept if c)),
        "j1_conflict_samples": len({(sk, a) for sk, a, _v, _j, c in kept if c}),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"]).size().items()
        },
        "rate_facts_by_unit": {f"{a}|{u}": int(c) for (a, u), c in
                               fact[fact.measurement_type != "J1"].groupby(["attribute", "units_raw"]).size().items()},
        "j1_units_by_attribute": {a: sorted(set(g.units_raw)) for a, g in fact[fact.measurement_type == "J1"].groupby("attribute")},
        "substrate_counts": pd.Series(sub).value_counts().to_dict(),
        "habitat_counts": pd.Series(hab).value_counts().to_dict(),
        "error_type_counts": pd.Series(etype).value_counts().to_dict(),
        "error_value_with_type_other": n_err_other, "error_value_with_type_missing": n_err_missing,
        "rows_with_sd": int(fact.sd.notna().sum()), "rows_with_se": int(fact.se.notna().sum()),
        "rows_with_n": int(fact.n.notna().sum()),
        "rows_converted_by_source": int(sum(converted)),
        "rows_negative_original_stored_positive": n_sign_flipped,
        "rows_in_duplicate_groups": int(dup.sum()),
        "duplicate_groups": int(rates.drop(columns="nfix_rate_id")[dup].astype(str).drop_duplicates().shape[0]),
        "rows_year_missing": int(sum(1 for y in year if y is None)),
        "rows_month_missing": int(sum(1 for m in month if m is None)),
        "rows_with_salinity": int(sum(1 for s in sal if s is not None)),
        "salinity_unit_cells_without_a_value": n_unit_without_value,
        "rows_salinity_units_missing": int(sum(sal_units_missing)),
        "salinity_units_values": pd.Series([u or "(none)" for u, s in zip(sal_units, sal) if s is not None]).value_counts().to_dict(),
        "salinity_max": max(s for s in sal if s is not None),
        "samples": len(sample), "samples_under_plan_key": len(set(plan_key)),
        "samples_spanning_more_than_one_location": n_samples_multi_loc,
        "locations": len(loc), "methods": len(method), "references": len(reference_df),
        "citations_with_doi": int(citation.doi.notna().sum()),
        "citations_without_doi": int(citation.doi.isna().sum()),
        "flag_counts": count_flags(fact["flags"]),
        "anc_columns_not_loaded_non_na_cells": not_loaded,
        "wc_rows_with_numeric_sample_depth": int(sum(1 for i in range(n_rows) if sub[i] == "wc" and sdepth[i] is not None)),
        "nonwc_rows_with_sample_depth_text": int(sum(1 for i in range(n_rows) if sub[i] != "wc" and sdepth[i] is not None)),
    }
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    rates, anc, refs = read_raw(args.raw_dir)
    tables, skips, report = build_tables(rates, anc, refs, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
