#!/usr/bin/env python3
"""Mattei and Scardi global marine phytoplankton production dataset -> contract tables.

Mattei, F., Scardi, M. (2021): Global marine phytoplankton production dataset. PANGAEA, doi:10.1594/PANGAEA.932417
(CC-BY-4.0, published 2021-06-09); paper Earth System Science Data 13, 4967-4985, doi:10.5194/essd-13-4967-2021. Raw file
(this directory): ``Global_marine_phytoplankton_production_dataset.txt`` (tab-separated, 37,722 data rows x 49 columns, quoted
fields, the ``Short reference`` carries a trailing CR LF inside its quotes). The file has no PANGAEA header, so version and
license are copied from the dataset page (INTEGRATION_PLAN 1.4).

Grain. A data row is one sampling depth of a profile. ``Profile number`` (6,084 values, asserted equal to the number of distinct
values) groups the rows of a profile; eight profile numbers span two (latitude, longitude, date) stations and the
station of a profile-level fact is that of the profile's FIRST row (named in ``sample.depth_notes`` and in the report).

Row-level (one fact per numeric cell, 37,722 rows):
  Depth-resolved primary production (mg C m^-3 day^-1)  francesco_pp_volumetric   B8 primary_production_per_volume
  Depth-resolved chl a (mg m^-3)                         water_chl_a                J1 chlorophyll_a
Profile-level values repeated on every row of a profile are loaded ONCE per profile; the extract asserts they are constant
within the profile (exact string equality) and counts the repeats as collapsed cells:
  Depth-integrated primary production (mg C m^-2 day^-1) francesco_pp_integrated   B8 depth_integrated_primary_production
  Pbopt (mg C mg Chla-1 h-1)                             francesco_pbopt           B8 chlorophyll_specific_production
  Depth-integrated chl a (mg m^-2)                       water_chl_a_integrated    J1 chlorophyll_a
  Total Chl a (mg m^-2)                                  water_chl_a_total         J1 chlorophyll_a
The plan puts the last two under ONE attribute (``water_chl_a_integrated``); they differ on every profile (none of the 6,084 is
equal), so under one attribute each profile would carry a bogus ``covariate_conflict``. They are two attributes here.

J1: a J1 value belongs to the SAMPLE. The row-level sample key is profile | sampling depth | date | latitude | longitude (strings
as written); equal values of one sample and attribute collapse and are counted, different values are all loaded with
``covariate_conflict`` (one profile-depth pair of the file: 12.1 vs 10.4 mg m^-3 at 3 m in profile 1711; another pair, profile
2282, is equal and collapses). The profile-level samples are keyed ``profile<n>``.

Context. ``Sampling depth`` -> ``sample.sample_depth_m`` (row-level samples; NULL on profile-level samples). ``SST`` (all rows, both flags) is NOT loaded
(the user's decision): it is a SURFACE temperature, not the temperature at the sampling depth, so ``water_temperature_c`` stays NULL. ``Date`` (dd/mm/yyyy; year and month asserted equal to the ``Year`` and
``Month`` columns) -> sampling_year / month. ``Event`` (the original cruise or programme, blank on 850 rows) ->
``sample.source_sample_id``. A longitude below -180 (one profile) is wrapped by +360 and the facts carry ``lon_wrapped``.
Habitat ``Water column`` from the dataset's scope, flag ``habitat_from_scope`` on every fact.

Not loaded (derived, reconstructed or categorical; counted in the report): ``SST`` (both flags), ``surface PAR`` (phase 2, both flags),
``Day length`` and ``Day of the year`` (computed), ``Mixed Layer Depth``, ``Euphotic zone depth``, ``Max production depth``,
``Max sampling depth``, ``Bottom depth`` and its sd (GEBCO), ``Distance from coastline`` (NASA OBPG grid),
``Production to biomass ratio`` (derived, D7), ``hemisphere``, ``Northern hemisphere season`` and every ``* magnitude``
category (Jenks classes). Every numeric cell of the nine loaded columns is a number: no missing codes occur, and a non-numeric
cell raises.

References. ``Short reference`` names the source the compilers took a profile from (many are databases, not papers); the
original cruise is ``Event``. ``reference_key`` = ``doi:<lower-case doi>`` where ``Paper doi`` is given, ``ref:<short
reference>`` where it is blank; a cell with two DOIs (``a; b``) gives the composite key ``doi:a+doi:b`` and one citation
per DOI (INTEGRATION_PLAN 3.4). A DOI shared by several short references is one reference with all of them in the citation's
``bibliography_id``. Every fact carries ``secondary_compilation``.

Flags: habitat_from_scope, secondary_compilation (all), lon_wrapped, covariate_conflict, zero_value.

Provenance: ``source_row_ref`` = ``<file>#data_row=<0-based data row>:<attribute>``; for a profile-level fact the data row is
the first row of its profile.

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
from _g4_common import J1Cell, collapse_j1, norm_ws  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "francesco-phytoplankton"
SOURCE_NAME = "Mattei and Scardi global marine phytoplankton production dataset"
SOURCE_DOI = "10.1594/PANGAEA.932417"
SOURCE_VERSION = "PANGAEA 932417 (published 2021-06-09; no version number)"
SOURCE_LICENSE = "CC-BY-4.0"
DATA_FILE = "Global_marine_phytoplankton_production_dataset.txt"
CITATION = (
    "Mattei, F., Scardi, M. (2021): Global marine phytoplankton production dataset [Dataset]. PANGAEA, "
    "https://doi.org/10.1594/PANGAEA.932417 (paper: Earth System Science Data 13, 4967-4985, "
    "https://doi.org/10.5194/essd-13-4967-2021)"
)
COLUMNS = [
    "Count", "Event", "Short reference", "Paper doi", "Data doi/data link", "Profile number", "Date", "Year", "Month",
    "Day of the year", "Latitude", "Longitude", "Day length (h)", "Bottom depth (m)", "Bottom depth sd (m)",
    "Mixed Layer Depth (m)", "Distance from coastline (Km)", "Euphotic zone depth (m)", "Sampling depth (m)",
    "Max sampling depth (m)", "Max production depth (m)", "SST (°C)", "SST_flag", "surface PAR (E m^-2 day^-1)",
    "PAR_flag", "Pbopt (mg C mg Chla-1 h-1)", "Depth-resolved chl a (mg m^-3)", "Depth-integrated chl a (mg m^-2)",
    "Total Chl a (mg m^-2)", "Depth-resolved primary production (mg C m^-3 day^-1)",
    "Depth-integrated primary production (mg C m^-2 day^-1)", "Production to biomass ratio (mg C day-1 / mg Chl a)",
    "hemisphere", "Northern hemisphere season", "Bottom depth magnitude", "Bottom depth SD magnitude", "MLD magnitude",
    "Distance from coastline magnitude", "Euphotic zone depth magnitude", "Max sampling depth magnitude",
    "Max production depth magnitude", "SST magnitude", "surface PAR magnitude", "Pbopt magnitude",
    "Surface chl a magnitude", "Depth-integrated chl a magnitude", "Total chl a magnitude",
    "Depth-integrated primary production magnitude", "Production to biomass ratio magnitude",
]
ROW_COLUMNS: dict[str, tuple[str, bool]] = {  # header -> (attribute, is_j1)
    "Depth-resolved primary production (mg C m^-3 day^-1)": ("francesco_pp_volumetric", False),
    "Depth-resolved chl a (mg m^-3)": ("water_chl_a", True),
}
PROFILE_COLUMNS: dict[str, tuple[str, bool]] = {
    "Depth-integrated primary production (mg C m^-2 day^-1)": ("francesco_pp_integrated", False),
    "Pbopt (mg C mg Chla-1 h-1)": ("francesco_pbopt", False),
    "Depth-integrated chl a (mg m^-2)": ("water_chl_a_integrated", True),
    "Total Chl a (mg m^-2)": ("water_chl_a_total", True),
}
PROFILE_CONSTANT = [*PROFILE_COLUMNS, "Short reference", "Paper doi", "Max sampling depth (m)"]
NOT_LOADED = [
    "surface PAR (E m^-2 day^-1)", "Day length (h)", "Day of the year", "Mixed Layer Depth (m)", "Euphotic zone depth (m)",
    "Max production depth (m)", "Max sampling depth (m)", "Bottom depth (m)", "Bottom depth sd (m)",
    "Distance from coastline (Km)", "Production to biomass ratio (mg C day-1 / mg Chl a)",
]
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_DOI_RE = re.compile(r"https://doi\.org/(10\.\S+)")


def _num(v: str, what: str) -> float:
    if not _NUM_RE.fullmatch(v):
        raise ContractError(f"{what}: non-numeric value {v!r}")
    return float(v)


def unit_of(header: str) -> str:
    m = re.fullmatch(r".* \((.+)\)", header)
    if not m:
        raise ContractError(f"{header}: no unit in the header")
    return m.group(1)


def read_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", dtype=object, keep_default_na=False, na_values=[], encoding="utf-8")
    if list(df.columns) != COLUMNS:
        raise ContractError(f"{path.name}: unexpected columns {list(df.columns)}")
    return df


def parse_dois(cell: str) -> list[str]:
    """DOIs of a ``Paper doi`` cell ('a; b'), bare and lower-case; blank -> []."""
    if cell.strip() == "":
        return []
    out = []
    for part in cell.split(";"):
        m = _DOI_RE.fullmatch(part.strip())
        if not m:
            raise ContractError(f"Paper doi {cell!r}: {part!r} is not an https://doi.org/ link")
        out.append(m.group(1).lower())
    return out


def build_tables(raw: pd.DataFrame, access_date: str) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if list(raw.columns) != COLUMNS:
        raise ContractError("unexpected columns")
    n = len(raw)
    C = {c: raw[c].tolist() for c in COLUMNS}
    if [int(_num(v, "Count")) for v in C["Count"]] != list(range(1, n + 1)):
        raise ContractError("Count is not 1..N in file order")
    for h, (a, _j) in {**ROW_COLUMNS, **PROFILE_COLUMNS}.items():
        if unit_of(h) not in ATTRIBUTE_DECL[a].units:
            raise ContractError(f"{a}: header unit {unit_of(h)!r} not declared")
    profile = [str(int(_num(v, "Profile number"))) for v in C["Profile number"]]
    n_profiles = len(set(profile))
    first_row: dict[str, int] = {}
    for i, p in enumerate(profile):
        first_row.setdefault(p, i)
    # profile-level columns are constant within the profile
    for col in PROFILE_CONSTANT:
        seen: dict[str, str] = {}
        for p, v in zip(profile, C[col]):
            if seen.setdefault(p, v) != v:
                raise ContractError(f"{col}: not constant within profile {p}")
    lat_s, lon_s, date_s = C["Latitude"], C["Longitude"], C["Date"]
    lat = [_num(v, "Latitude") for v in lat_s]
    lon_raw = [_num(v, "Longitude") for v in lon_s]
    if any(not -90 <= a <= 90 for a in lat):
        raise ContractError("latitude out of range")
    wrapped = [b < -180 for b in lon_raw]
    if any(b > 180 for b in lon_raw) or any(b < -360 for b in lon_raw):
        raise ContractError("longitude beyond the wrappable range")
    lon = [b + 360 if w else b for b, w in zip(lon_raw, wrapped)]
    year, month = [], []
    for d, y, m in zip(date_s, C["Year"], C["Month"]):
        mm = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d)
        if not mm or int(mm.group(3)) != int(_num(y, "Year")) or int(mm.group(2)) != int(_num(m, "Month")):
            raise ContractError(f"Date {d!r} disagrees with Year {y!r} / Month {m!r}")
        year.append(int(mm.group(3))), month.append(int(mm.group(2)))
    depth = [_num(v, "Sampling depth") for v in C["Sampling depth (m)"]]
    if any(d < 0 for d in depth):
        raise ContractError("negative sampling depth")
    sst_flag = C["SST_flag"]
    if set(sst_flag) - {"0", "1"}:
        raise ContractError(f"SST_flag values {set(sst_flag)}")
    sst = [_num(v, "SST") for v in C["SST (°C)"]]
    row_key = [f"{p}|{d}|{dt}|{a}|{b}" for p, d, dt, a, b in zip(profile, C["Sampling depth (m)"], date_s, lat_s, lon_s)]

    # ---- references
    ref_str = [norm_ws(v) for v in C["Short reference"]]
    dois = [parse_dois(v) for v in C["Paper doi"]]
    ref_key = [("+".join(f"doi:{d}" for d in ds) if ds else f"ref:{s}") for ds, s in zip(dois, ref_str)]

    # ---- row-level facts; J1 cells collected for the sample-keyed collapse
    flags_by_row = []
    for i in range(n):
        fl = ["habitat_from_scope", "secondary_compilation"]
        if wrapped[i]:
            fl.append("lon_wrapped")
        flags_by_row.append(fl)
    methods: dict[str, dict] = {}

    def method_for(attr: str) -> str:
        if attr not in methods:
            methods[attr] = {
                "source_key": SOURCE_KEY, "method_key": attr, "source_method_id": None, "attribute": attr,
                "method_status": "method_id_null", "technique": None, "coring_method": None, "carbon_type": None,
                "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
                "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": None,
                "light_dark": None, "incubation_type": None, "experimental_status": None, "params": json.dumps({}),
            }
        return attr

    rows: list[dict] = []
    j1_cells: list[J1Cell] = []

    def fact_row(attr: str, key: str, i: int, x: float, sample_key: str, flags: list[str]) -> dict:
        decl = ATTRIBUTE_DECL[attr]
        h = next(h for h, (a, _j) in {**ROW_COLUMNS, **PROFILE_COLUMNS}.items() if a == attr)
        return {
            "source_key": SOURCE_KEY, "fact_key": key, "reference_key": ref_key[i],
            "location_key": f"{lat_s[i]}|{lon_s[i]}", "sample_key": sample_key, "method_key": method_for(attr),
            "attribute": attr, "measurement_type": decl.measurement_type, "subfield": next(iter(decl.subfields)),
            "value": x, "units_raw": unit_of(h), "sd": np.nan, "se": np.nan, "n": None, "flags": sorted(flags),
            "source_row_ref": f"{DATA_FILE}#data_row={i}:{attr}",
        }

    n_cells = {"row": 0, "profile_occurrences": 0}
    for h, (attr, is_j1) in ROW_COLUMNS.items():
        for i in range(n):
            x = _num(C[h][i], f"{h} row {i}")
            n_cells["row"] += 1
            if is_j1:
                j1_cells.append(J1Cell(i, row_key[i], attr, x, f"{DATA_FILE}#data_row={i}:{attr}", i))
            else:
                fl = list(flags_by_row[i]) + (["zero_value"] if x == 0 else [])
                rows.append(fact_row(attr, f"row{i}:{attr}", i, x, row_key[i], fl))
    # ---- profile-level facts, once per profile (first row)
    for h, (attr, is_j1) in PROFILE_COLUMNS.items():
        for p, i in first_row.items():
            x = _num(C[h][i], f"{h} row {i}")
            if x == 0:
                raise ContractError(f"{h} profile {p}: a zero profile-level value")
            sk = f"profile{p}"
            if is_j1:
                j1_cells.append(J1Cell(i, sk, attr, x, f"{DATA_FILE}#data_row={i}:{attr}", i))
            else:
                rows.append(fact_row(attr, f"profile{p}:{attr}", i, x, sk, list(flags_by_row[i])))
        n_cells["profile_occurrences"] += sum(1 for v in C[h] if _NUM_RE.fullmatch(v))
    j1_facts, j1_counts = collapse_j1(j1_cells)
    for jf in j1_facts:
        c = jf.cell
        i = c.payload
        fl = list(flags_by_row[i])
        if c.value == 0:
            fl.append("zero_value")
        if jf.covariate_conflict:
            fl.append("covariate_conflict")
        attr = c.attribute
        sample_key = c.sample_key
        rows.append(fact_row(attr, f"row{i}:{attr}" if sample_key == row_key[i] else f"profile{profile[i]}:{attr}",
                             i, c.value, sample_key, fl))
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    if fact.fact_key.duplicated().any():
        dups = fact[fact.fact_key.duplicated(keep=False)].fact_key.unique()[:5]
        raise ContractError(f"duplicate fact_key {list(dups)}")

    # ---- accounting
    cells_in = n_cells["row"] * 1 + n_cells["profile_occurrences"]
    profile_repeats = n_cells["profile_occurrences"] - len(PROFILE_COLUMNS) * n_profiles
    n_collapsed = j1_counts["collapsed_repeats"] + profile_repeats
    # the J1 collapse counts profile-level J1 cells once (one per profile), so its own repeats are row-level only
    if len(fact) + n_collapsed != cells_in:
        raise ContractError(f"cells {cells_in} != facts {len(fact)} + collapsed {n_collapsed}")

    # ---- samples
    sample_rows: dict[str, dict] = {}
    slot_conflicts: list[str] = []
    for i in range(n):
        k = row_key[i]
        temp = None  # SST is a surface value: not loaded
        ev = norm_ws(C["Event"][i]) or None
        if k not in sample_rows:
            sample_rows[k] = {"i": i, "temp": temp, "event": ev}
        else:
            s = sample_rows[k]
            if s["temp"] != temp or s["event"] != ev:
                slot_conflicts.append(k)
    if slot_conflicts:
        raise ContractError(f"samples whose rows disagree on temperature or event: {slot_conflicts[:5]}")
    used = set(fact.sample_key)
    s_out = []
    for k in sorted(used):
        if k.startswith("profile") and k not in sample_rows:
            p = k[len("profile"):]
            i = first_row[p]
            stations = {(lat_s[j], lon_s[j], date_s[j]) for j in range(n) if profile[j] == p}
            note = f"profile-level value; max sampling depth {C['Max sampling depth (m)'][i]} m"
            if len(stations) > 1:
                note += f"; profile spans {len(stations)} stations, located at the first row's"
            s_out.append({"key": k, "i": i, "depth": None, "temp": None, "event": norm_ws(C["Event"][i]) or None, "note": note})
        else:
            s = sample_rows[k]
            s_out.append({"key": k, "i": s["i"], "depth": depth[s["i"]], "temp": s["temp"], "event": s["event"], "note": None})
    sample = pd.DataFrame({
        "source_key": SOURCE_KEY, "sample_key": [s["key"] for s in s_out], "source_sample_id": [s["event"] for s in s_out],
        "depth_min_cm": np.nan, "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan,
        "representative_depth_max_cm": np.nan, "depth_notes": [s["note"] for s in s_out],
        "sampling_year": pd.array([year[s["i"]] for s in s_out], dtype="Int64"),
        "sampling_month": pd.array([month[s["i"]] for s in s_out], dtype="Int64"),
        "taxon": None, "taxon_rank": None, "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None,
        "photosynthetic_sediment": None, "sample_depth_m": [np.nan if s["depth"] is None else s["depth"] for s in s_out],
        "water_temperature_c": [np.nan if s["temp"] is None else s["temp"] for s in s_out], "water_salinity": np.nan,
        "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
    })
    # ---- locations
    first_loc: dict[str, int] = {}
    for i in range(n):
        first_loc.setdefault(f"{lat_s[i]}|{lon_s[i]}", i)
    lkeys = sorted(set(fact.location_key))
    nl = len(lkeys)
    loc = pd.DataFrame({
        "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": None, "source_site_id": None, "source_core_id": lkeys,
        "latitude": [lat[first_loc[k]] for k in lkeys], "longitude": [lon[first_loc[k]] for k in lkeys],
        "position_accuracy": np.nan, "position_accuracy_units": None, "position_method": None, "country": None,
        "admin_division": None, "water_body": None, "habitat_type": "water column", "geomorphic_setting": None,
        "bioregion": None, "marine_ecoregion": None, "salinity_class": None, "salinity_class_level": None,
        "vegetation_class": None, "vegetation_class_level": None, "tidal_position": None, "tidal_position_level": None,
        "modification_status": [[] for _ in range(nl)], "dominant_species": [[] for _ in range(nl)],
        "elevation": np.nan, "elevation_units": None, "elevation_datum": None, "elevation_method": None,
        "water_depth_m": np.nan, "distance_to_shore_km": np.nan,
    })
    # ---- references and citations
    by_ref: dict[str, dict] = {}
    for i in range(n):
        r = by_ref.setdefault(ref_key[i], {"dois": dois[i], "short": set(), "urls": {}})
        r["short"].add(ref_str[i])
    used_refs = sorted(set(fact.reference_key))
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": used_refs})
    cit = []
    for rk in used_refs:
        r = by_ref[rk]
        biblio = "; ".join(sorted(r["short"]))
        if r["dois"]:
            for d in r["dois"]:
                cit.append({"citation_key": f"{rk}|doi:{d}", "reference_key": rk, "bibliography_id": biblio, "doi": d,
                            "url": f"https://doi.org/{d}", "year": None})
        else:
            ys = set(re.findall(r"(?:19|20)\d\d", biblio))
            cit.append({"citation_key": f"{rk}|0", "reference_key": rk, "bibliography_id": biblio, "doi": None,
                        "url": None, "year": int(next(iter(ys))) if len(ys) == 1 else None})
    citation = pd.DataFrame({
        "source_key": SOURCE_KEY, "citation_key": [c["citation_key"] for c in cit],
        "reference_key": [c["reference_key"] for c in cit], "bibliography_id": [c["bibliography_id"] for c in cit],
        "publication_type": None, "doi": [c["doi"] for c in cit], "title": None, "authors": None,
        "year": pd.array([c["year"] for c in cit], dtype="Int64"), "url": [c["url"] for c in cit],
        "openalex_id": None, "doi_resolved": None, "doi_resolution_source": None,
    })
    method = pd.DataFrame(sorted(methods.values(), key=lambda m: m["method_key"]))
    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + " Version and license are from the PANGAEA dataset page (the file has no header).",
    }])
    tables = {
        "source": source, "reference": reference, "citation": citation,
        "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
        "method": method, "fact": fact,
    }
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference.reference_key),
        ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    flag_counts: dict[str, int] = {}
    for fl in fact["flags"]:
        for x in fl:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    multi = sorted(p for p in first_row if len({(lat_s[j], lon_s[j], date_s[j]) for j in range(n) if profile[j] == p}) > 1)
    report = {
        "source_rows": n,
        "profiles": n_profiles,
        "profiles_spanning_several_stations": multi,
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"]).size().items()
        },
        "cells_in": cells_in,
        "cells_row_level": n_cells["row"],
        "cells_profile_level_occurrences": n_cells["profile_occurrences"],
        "collapsed_profile_repeats": profile_repeats,
        "j1_collapse": j1_counts,
        "skipped": 0,
        "samples": len(sample),
        "samples_row_level": int(sum(1 for k in sample.sample_key if not k.startswith("profile"))),
        "samples_with_temperature": int(sample.water_temperature_c.notna().sum()),
        "rows_with_in_situ_sst": int(sum(1 for f in sst_flag if f == "0")),
        "rows_with_reconstructed_sst_not_loaded": int(sum(1 for f in sst_flag if f == "1")),
        "par_flag0_rows_not_loaded": int(sum(1 for f in C["PAR_flag"] if f == "0")),
        "locations": len(loc),
        "wrapped_longitude_rows": int(sum(wrapped)),
        "methods": len(method),
        "references": len(reference),
        "references_composite_doi": int(sum(1 for r in used_refs if "+" in r)),
        "citations": len(citation),
        "citations_with_doi": int(citation.doi.notna().sum()),
        "rows_by_reference_key": {k: int(v) for k, v in pd.Series(ref_key).value_counts().sort_index().items()},
        "short_references": len(set(ref_str)),
        "events": len({norm_ws(e) for e in C["Event"] if norm_ws(e)}),
        "rows_without_event": int(sum(1 for e in C["Event"] if not norm_ws(e))),
        "flag_counts": flag_counts,
        "not_loaded_columns": NOT_LOADED,
    }
    return tables, [], report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    raw = read_raw(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(raw, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
