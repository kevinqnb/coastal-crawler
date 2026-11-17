#!/usr/bin/env python3
"""Davis et al. spatiotemporal drivers of hydrochemical ecosystem calcification and organic productivity (meta-data) -> contract tables.

Davis, K., Colefax, A., Tucker, J., Kelaher, B., Santos, I. (2021): Spatiotemporal drivers of hydrochemical ecosystem
calcification and organic productivity: the meta-data. SEANOE, doi:10.17882/80022 ("last version", quality-controlled).
Raw file (this directory): ``89830.xlsx``, sheet ``Metadata1`` (the sheet ``_xltb_storage_`` is junk and not read).
Layout asserted: row 1 = legend, row 2 = headers, row 3 = "how data were used" per column, 116 data rows from sheet row 4
to 119, nothing below, nothing in columns AR to AV. Cells hold formulas (e.g. ``=6.875*24``); the CACHED values
are read (a formula with no cached value raises).

Grain: ONE FACT PER NON-BLANK NUMERIC VALUE CELL of four columns; units are the header's own spelling and values are
carried as given:
  Gnet (mmol m-2 d-1)    davis_gnet            B7 net_ecosystem_calcification   mmol m-2 d-1   (116)
  Pnet (mmol m-2 d-1)    davis_pnet            B5 net_ecosystem_production      mmol m-2 d-1   (105)
  % coral cover          davis_coral_cover     B4 live_hard_coral_cover         %              (95)
  % Coralline            davis_coralline_cover B4 turf_cca_invertebrate_cover   %              (31)
The sheet does not say whether Pnet is on an O2 or a C basis; the unit string is kept verbatim.
Not loaded (counts in the report): ``% calcifiers`` (= coral + coralline, derived by the authors), PAR (the authors:
not used, too many reporting units), avg and Stdev of Omega-aragonite and Temp Stdev (phase 2), NO3 and PO4 (no
units in the sheet), TA:DIC slope, ``Net dissolution at Night?``, ``Decade Studied``. Context: ``Temp avg`` ->
``sample.water_temperature_c`` (no unit in the sheet; the slot is degC, so degC is assumed), ``Depth`` ->
``location.water_depth_m``.

Errors. ``Gnet error`` + its ``error type`` -> ``sd`` / ``se`` of the Gnet fact; ``Pnet error`` + its ``error type``
-> the Pnet fact. Explicit table: ``SE``, ``se`` -> se; ``SD``, ``SD `` -> sd; ``EP-SE`` -> se plus flag
``error_propagated`` (error-propagated SE); ``S`` -> NOT interpreted (could be SE or SD: no sd/se, flag
``error_type_missing``, value in the method params). An error value with NO type also gets no sd/se,
``error_type_missing`` and the value in params; an unlisted type raises. A negative error value is kept as given and
flagged ``negative_sd`` (the Pnet errors of the Kinsey 1977 rows are signed). ``% Coral Error`` has no type column: its
value goes to the params of the cover fact's method row, not to sd/se.

Colour provenance (flags). Font colour of the VALUE cell, read against the legend in row 1: purple (rgb FF7030A0, the colour of
the "Data were calculated from information provided" legend text) -> ``calculated_by_source``; blue (theme 4, the colour of
"Data were sourced from other literature compilations") -> ``secondary_source``; black / none -> no flag. Any other font
colour on a loaded cell is not in the legend: no flag, counted in the report. (Cell FILLS mark "used in Fig. 3 / 4", not
provenance, and are not read.)

Locations: ``location_key`` = Location | Country | lat | lon | Reef Type | Depth | Reef state | Health. ``habitat_type`` and
``geomorphic_setting`` = ``Reef Type`` (whitespace normalised; case variants such as ``reef flat`` / ``Reef flat``
are separate label-map rows). ``modification_status`` = raw ``reef state: <text>`` and ``health: <text>`` (none mapped).
Lat / Long are numbers, apart from 2 text latitudes (``-28.5``) and one longitude written ``149.9 W`` (a trailing
N/S/E/W sets the sign: W and S negative; a letter on an already signed value raises). Wave action, season, heat type, the
two ``Method`` columns, night measured, duration, timescale, site returned, TA:DIC slope and the notes go to params.

Samples: one per row (``row<i>``, i = 0-based data row = sheet row - 4); ``sampling_year`` = ``Year Studied`` (6 blank: flag
``year_missing``).

Methods: for the rate attributes (Gnet, Pnet) one row per distinct (attribute, technique, params); ``technique`` = the first
``Method`` column (raw: L, E, S, CV, OTAA, C, chamber, ...), the second (grouped: FL, S, CV, OTAA, GF) is in params. Cover
facts carry one ``method_id_null`` row each (the % coral cover facts per row, holding ``% Coral Error`` in params).

References: ``Short Reference`` (author, year, journal text; 53 distinct after whitespace normalisation) -> ``ref:<string>``;
the sheet has no reference list, so the citation carries only ``bibliography_id`` = the string and ``year`` = ``Year Pub``
(asserted single-valued per string); the later DOI-resolution job resolves them.

Provenance: ``source_row_ref`` = ``89830.xlsx[Metadata1]#data_row=<0-based data row>:<attribute>``.

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
import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from _g3_common import blank, count_flags, is_num, method_row, norm_ws, number, text  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "davis-calcification"
SOURCE_NAME = "Spatiotemporal drivers of hydrochemical ecosystem calcification and organic productivity: the meta-data (Davis et al.)"
SOURCE_DOI = "10.17882/80022"
SOURCE_VERSION = "SEANOE 80022 (2021-11-26), last version"
SOURCE_LICENSE = "CC BY 4.0"  # SEANOE page, read by the integration plan
DATA_FILE, SHEET = "89830.xlsx", "Metadata1"
CITATION = (
    "Davis, K., Colefax, A., Tucker, J., Kelaher, B., Santos, I. (2021): Spatiotemporal drivers of hydrochemical "
    "ecosystem calcification and organic productivity: the meta-data [Dataset]. SEANOE, https://doi.org/10.17882/80022"
)
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()
check_skip_reasons(ALLOWED_SKIP_REASONS)

FIRST_ROW, LAST_ROW = 4, 119  # sheet rows of the data
N_ROWS = LAST_ROW - FIRST_ROW + 1
PURPLE, BLUE_THEME = "FF7030A0", 4
LEGEND = {"C": "Data were calculated from information provided", "H": "Data were sourced from other literature compilations"}
# column letter -> header (asserted, whitespace normalised)
HEADERS = {
    "A": "Short Reference", "B": "Year Pub", "C": "Year Studied", "D": "Decade Studied", "E": "Location",
    "F": "Country", "G": "Lat", "H": "Long", "I": "Reef Type", "J": "Wave Action", "K": "Timescale",
    "L": "Duration (days)", "M": "Site returned in separate year?", "N": "Season Year", "O": "Season",
    "P": "Heat Type", "Q": "Method", "R": "Method", "S": "Night measured?", "T": "TA:DIC SLOPE", "U": "avg Ωarag",
    "V": "Stdev Ωarag", "W": "Temp avg", "X": "Temp Stdev", "Y": "Depth", "Z": "% coral cover", "AA": "% Coral Error",
    "AB": "% Coralline", "AC": "% calcifiers", "AD": "NO3", "AE": "PO4", "AF": "PAR (daytime average)",
    "AG": "Net dissolution at Night? (study average)", "AH": "Reef state", "AI": "Health",
    "AJ": "Gnet (mmol m-2 d-1)", "AK": "Gnet error", "AL": "error type", "AM": "Pnet (mmol m-2 d-1)",
    "AN": "Pnet error", "AO": "error type", "AP": "Data found", "AQ": "Notes",
}
FACT_COLUMNS = {  # column -> (attribute, units_raw)
    "AJ": ("davis_gnet", "mmol m-2 d-1"), "AM": ("davis_pnet", "mmol m-2 d-1"),
    "Z": ("davis_coral_cover", "%"), "AB": ("davis_coralline_cover", "%"),
}
ERROR_COLUMNS = {"AJ": ("AK", "AL"), "AM": ("AN", "AO")}  # value column -> (error column, error type column)
ERROR_TYPES = {"SE": ("se", False), "se": ("se", False), "SD": ("sd", False), "EP-SE": ("se", True), "S": (None, False)}
NOT_LOADED = ["Decade Studied", "TA:DIC SLOPE", "avg Ωarag", "Stdev Ωarag", "Temp Stdev", "% calcifiers", "NO3", "PO4",
              "PAR (daytime average)", "Net dissolution at Night? (study average)"]
PARAM_COLUMNS = {
    "wave_action": "J", "timescale": "K", "duration_days": "L", "site_returned_in_separate_year": "M",
    "season_year": "N", "season": "O", "heat_type": "P", "method_grouped": "R", "night_measured": "S",
    "ta_dic_slope": "T", "data_found": "AP", "notes": "AQ",
}
_TEXT_CELLS = ("A", "E", "F", "I", "J", "K", "N", "O", "P", "Q", "R", "S", "AG", "AH", "AI", "AL", "AO", "AP", "AQ")
_LONLAT = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*([NSEW]?)\s*")


def col_index(letter: str) -> int:
    return openpyxl.utils.column_index_from_string(letter)


def font_colour(cell) -> tuple[str, str | int] | None:
    fc = cell.font.color if cell.font else None
    if fc is None:
        return None
    if fc.type == "rgb":
        return ("rgb", fc.rgb)
    if fc.type == "theme":
        return ("theme", fc.theme if not fc.tint else f"{fc.theme}:{round(fc.tint, 2)}")  # type: ignore[return-value]
    return ("indexed", fc.indexed)


def provenance(colour: tuple | None) -> str:
    if colour == ("rgb", PURPLE):
        return "calculated"
    if colour == ("theme", BLUE_THEME):
        return "compilation"
    if colour is None or colour == ("theme", 1):
        return "plain"
    return "other"


def lonlat(v: object, what: str, limit: float) -> float | None:
    """Number, or text 'number [N|S|E|W]' (W and S negative; a letter on a negative value raises)."""
    if blank(v):
        return None
    if is_num(v):
        x = float(v)  # type: ignore[arg-type]
    elif isinstance(v, str):
        m = _LONLAT.fullmatch(v)
        if not m:
            raise ContractError(f"{what}: unrecognised value {v!r}")
        x = float(m.group(1))
        if m.group(2):
            if x < 0:
                raise ContractError(f"{what}: a hemisphere letter on the negative value {v!r}")
            x = -x if m.group(2) in "SW" else x
    else:
        raise ContractError(f"{what}: unrecognised value {v!r}")
    if not (-limit <= x <= limit):
        raise ContractError(f"{what}: {x} outside the valid range")
    return x


# ---------------------------------------------------------------- raw reading


def read_raw(path: Path) -> tuple[list[dict], dict]:
    """Rows as {column letter: (cached value, font colour)} for sheet rows 4..119, plus the layout facts checked."""
    wv = openpyxl.load_workbook(path, data_only=True)[SHEET]
    wf = openpyxl.load_workbook(path)[SHEET]
    if wv.max_row < LAST_ROW or any(wv.cell(r, c).value is not None for r in range(LAST_ROW + 1, wv.max_row + 1)
                                    for c in range(1, wv.max_column + 1)):
        raise ContractError(f"{path.name}: data rows are not exactly sheet rows {FIRST_ROW}..{LAST_ROW}")
    for letter, head in HEADERS.items():
        got = wv.cell(2, col_index(letter)).value
        if norm_ws(str(got)) != head:
            raise ContractError(f"{path.name}: header {letter}2 is {got!r}, expected {head!r}")
    extra = [c for c in range(col_index("AR"), wv.max_column + 1)
             if any(wv.cell(r, c).value is not None for r in range(1, LAST_ROW + 1))]
    if extra:
        raise ContractError(f"{path.name}: unexpected content in columns beyond AQ")
    for letter, label in LEGEND.items():
        if norm_ws(str(wv[f"{letter}1"].value)) != label:
            raise ContractError(f"{path.name}: legend {letter}1 is not {label!r}")
    if font_colour(wf["C1"]) != ("rgb", PURPLE) or font_colour(wf["H1"]) != ("theme", BLUE_THEME):
        raise ContractError(f"{path.name}: the legend font colours are not the expected purple and blue")
    rows = []
    n_formulas_uncached = 0
    for r in range(FIRST_ROW, LAST_ROW + 1):
        row = {}
        for letter in HEADERS:
            c = col_index(letter)
            v = wv.cell(r, c).value
            raw_f = wf.cell(r, c).value
            if isinstance(raw_f, str) and raw_f.startswith("=") and v is None:
                n_formulas_uncached += 1
            row[letter] = (v, font_colour(wf.cell(r, c)), isinstance(raw_f, str) and raw_f.startswith("="))
        rows.append(row)
    if n_formulas_uncached:
        raise ContractError(f"{n_formulas_uncached} formula cells have no cached value")
    return rows, {"formula_cells": sum(1 for row in rows for _v, _c, f in row.values() if f)}


# ---------------------------------------------------------------- main build


def build_tables(rows: list[dict], layout: dict, access_date: str) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    n = len(rows)
    val = lambda i, c: rows[i][c][0]  # noqa: E731

    def num_cell(i: int, c: str, what: str) -> float | None:
        v = val(i, c)
        if blank(v):
            return None
        return number(v, f"row {i} {what}")

    ref_raw = [text(val(i, "A")) for i in range(n)]
    if any(r is None for r in ref_raw):
        raise ContractError("a row without Short Reference")
    years_of: dict[str, set[int]] = {}
    for i in range(n):
        years_of.setdefault(ref_raw[i], set()).add(int(number(val(i, "B"), "Year Pub")))  # type: ignore[arg-type]
    string_year = {r: [int(y) for y in re.findall(r"\b((?:19|20)\d\d)\b", r)] for r in years_of}
    year_pub: dict[str, int] = {}
    year_pub_conflicts: dict[str, list[int]] = {}
    for r, ys in years_of.items():
        if len(ys) == 1:
            year_pub[r] = next(iter(ys))
        else:  # the rows of one string disagree on Year Pub: use the year written in the string, which must be one of them
            year_pub_conflicts[r] = sorted(ys)
            if len(string_year[r]) != 1 or string_year[r][0] not in ys:
                raise ContractError(f"{r!r}: Year Pub values {sorted(ys)} and no usable year in the string")
            year_pub[r] = string_year[r][0]
    year_pub_differs_from_string = {r: [year_pub[r], string_year[r]] for r in years_of
                                    if r not in year_pub_conflicts and string_year[r] != [year_pub[r]]}
    year = [None if blank(val(i, "C")) else int(number(val(i, "C"), "Year Studied")) for i in range(n)]
    loc_name, country, reef = (
        [text(val(i, c)) for i in range(n)] for c in ("E", "F", "I")
    )
    if any(x is None for x in loc_name + country + reef):
        raise ContractError("a row without Location, Country or Reef Type")
    lat = [lonlat(val(i, "G"), "Lat", 90) for i in range(n)]
    lon = [lonlat(val(i, "H"), "Long", 180) for i in range(n)]
    if any((a is None) != (b is None) for a, b in zip(lat, lon)):
        raise ContractError("Lat and Long are not missing together")
    depth = [num_cell(i, "Y", "Depth") for i in range(n)]
    if any(d is not None and d < 0 for d in depth):
        raise ContractError("a negative Depth")
    temp = [num_cell(i, "W", "Temp avg") for i in range(n)]
    reef_state, health = [text(val(i, "AH")) for i in range(n)], [text(val(i, "AI")) for i in range(n)]
    for c in ("T", "U", "V", "X", "AA", "AC", "AD", "AE", "AF"):  # unloaded numeric columns must stay numeric
        for i in range(n):
            num_cell(i, c, HEADERS[c])
    not_loaded = {h: int(sum(0 if blank(val(i, c)) else 1 for i in range(n))) for c, h in HEADERS.items() if h in NOT_LOADED}

    # ---- locations
    def f(x: float | None) -> str:
        return "" if x is None else repr(float(x))

    loc_key = ["|".join([loc_name[i], country[i], f(lat[i]), f(lon[i]), reef[i], f(depth[i]),  # type: ignore[list-item]
                         reef_state[i] or "", health[i] or ""]) for i in range(n)]
    first_loc: dict[str, int] = {}
    for i, k in enumerate(loc_key):
        first_loc.setdefault(k, i)
    lkeys = sorted(first_loc)
    li = [first_loc[k] for k in lkeys]
    nl = len(lkeys)

    def mod_status(i: int) -> list[str]:
        out = []
        if reef_state[i]:
            out.append(f"reef state: {reef_state[i]}")
        if health[i]:
            out.append(f"health: {health[i]}")
        return out

    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": [loc_name[i] for i in li],
            "source_site_id": None, "source_core_id": lkeys,
            "latitude": [np.nan if lat[i] is None else lat[i] for i in li],
            "longitude": [np.nan if lon[i] is None else lon[i] for i in li], "position_accuracy": np.nan,
            "position_accuracy_units": None, "position_method": None, "country": [country[i] for i in li],
            "admin_division": None, "water_body": None, "habitat_type": [reef[i] for i in li],
            "geomorphic_setting": [reef[i] for i in li], "bioregion": None, "marine_ecoregion": None,
            "salinity_class": None, "salinity_class_level": None, "vegetation_class": None,
            "vegetation_class_level": None, "tidal_position": None, "tidal_position_level": None,
            "modification_status": [mod_status(i) for i in li], "dominant_species": [[] for _ in range(nl)],
            "elevation": np.nan, "elevation_units": None, "elevation_datum": None, "elevation_method": None,
            "water_depth_m": [np.nan if depth[i] is None else depth[i] for i in li], "distance_to_shore_km": np.nan,
        }
    )

    # ---- samples
    sample_key = [f"row{i}" for i in range(n)]
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "sample_key": sample_key, "source_sample_id": None,
            "depth_min_cm": np.nan, "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan, "depth_notes": None,
            "sampling_year": pd.array(year, dtype="Int64"), "sampling_month": pd.array([None] * n, dtype="Int64"),
            "taxon": None, "taxon_rank": None, "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None,
            "photosynthetic_sediment": None, "sample_depth_m": np.nan,
            "water_temperature_c": [np.nan if t is None else t for t in temp], "water_salinity": np.nan,
            "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
        }
    )

    # ---- facts
    method_rows: dict[str, dict] = {}
    facts: list[dict] = []
    n_cells = {a: 0 for a, _u in FACT_COLUMNS.values()}
    colour_counts: dict[str, dict[str, int]] = {a: {} for a, _u in FACT_COLUMNS.values()}
    n_err_no_type = n_err_type_no_error = n_err_negative = 0
    err_type_counts: dict[str, int] = {}
    for col, (attr, units) in FACT_COLUMNS.items():
        decl = ATTRIBUTE_DECL[attr]
        if units not in decl.units:
            raise ContractError(f"{attr}: unit {units!r} not declared")
        header_unit = re.search(r"\(([^()]+)\)$", HEADERS[col])
        if header_unit and header_unit.group(1) != units:
            raise ContractError(f"{HEADERS[col]}: header unit is not {units!r}")
        if not header_unit and units != "%":
            raise ContractError(f"{HEADERS[col]}: no unit in the header")
        (sub_f,) = decl.subfields
        for i in range(n):
            x = num_cell(i, col, HEADERS[col])
            if x is None:
                continue
            n_cells[attr] += 1
            fl = []
            prov = provenance(rows[i][col][1])
            colour_counts[attr][prov] = colour_counts[attr].get(prov, 0) + 1
            if prov == "calculated":
                fl.append("calculated_by_source")
            elif prov == "compilation":
                fl.append("secondary_source")
            if x == 0:
                fl.append("zero_value")
            if year[i] is None:
                fl.append("year_missing")
            sd = se = np.nan
            if col in ERROR_COLUMNS:
                ecol, tcol = ERROR_COLUMNS[col]
                ev = num_cell(i, ecol, HEADERS[ecol])
                et = text(val(i, tcol))
                if et is not None and et not in ERROR_TYPES and et.strip() not in ERROR_TYPES:
                    raise ContractError(f"row {i}: unlisted error type {et!r}")
                if et is not None:
                    err_type_counts[et] = err_type_counts.get(et, 0) + 1
                if ev is None and et is not None:
                    n_err_type_no_error += 1
                if ev is not None:
                    if ev < 0:
                        fl.append("negative_sd")
                        n_err_negative += 1
                    kind, propagated = ERROR_TYPES[et] if et is not None else (None, False)
                    if kind == "sd":
                        sd = ev
                    elif kind == "se":
                        se = ev
                    else:
                        fl.append("error_type_missing")
                        n_err_no_type += et is None
                    if propagated:
                        fl.append("error_propagated")
            # method
            if col in ERROR_COLUMNS:
                params = {k: text(val(i, c)) if isinstance(val(i, c), str) or val(i, c) is None else val(i, c)
                          for k, c in PARAM_COLUMNS.items()}
                params["error_value"] = None if col not in ERROR_COLUMNS else num_cell(i, ERROR_COLUMNS[col][0], "error")
                params["error_type_raw"] = text(val(i, ERROR_COLUMNS[col][1]))
                tech = text(val(i, "Q"))
                pj = json.dumps(params, sort_keys=True, default=str)
                mkey = f"{attr}|{tech}|{pj}"
                if mkey not in method_rows:
                    method_rows[mkey] = method_row(SOURCE_KEY, mkey, attr, status="matched" if tech else "method_id_null",
                                                   technique=tech, params=pj)
            elif attr == "davis_coral_cover":
                ce = num_cell(i, "AA", "% Coral Error")
                pj = json.dumps({"coral_error_value": ce, "coral_error_type": None}, sort_keys=True)
                mkey = f"{attr}|{pj}"
                if mkey not in method_rows:
                    method_rows[mkey] = method_row(SOURCE_KEY, mkey, attr, status="method_id_null", params=pj)
            else:
                mkey = attr
                if mkey not in method_rows:
                    method_rows[mkey] = method_row(SOURCE_KEY, mkey, attr, status="method_id_null")
            facts.append(
                {
                    "source_key": SOURCE_KEY, "fact_key": f"row{i}:{attr}", "reference_key": f"ref:{ref_raw[i]}",
                    "location_key": loc_key[i], "sample_key": sample_key[i], "method_key": mkey, "attribute": attr,
                    "measurement_type": decl.measurement_type, "subfield": sub_f, "value": float(x), "units_raw": units,
                    "sd": sd, "se": se, "n": None, "flags": sorted(fl),
                    "source_row_ref": f"{DATA_FILE}[{SHEET}]#data_row={i}:{attr}",
                }
            )
    fact = pd.DataFrame(facts)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    method = pd.DataFrame(sorted(method_rows.values(), key=lambda r: r["method_key"]))
    sample = sample[sample.sample_key.isin(set(fact.sample_key))].reset_index(drop=True)
    loc = loc[loc.location_key.isin(set(fact.location_key))].reset_index(drop=True)

    # ---- references
    ref_strings = sorted(set(ref_raw))  # type: ignore[type-var]
    reference_df = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": [f"ref:{r}" for r in ref_strings]})
    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "citation_key": [f"ref:{r}|0" for r in ref_strings],
            "reference_key": [f"ref:{r}" for r in ref_strings], "bibliography_id": ref_strings,
            "publication_type": None, "doi": None, "title": None, "authors": None,
            "year": pd.array([year_pub[r] for r in ref_strings], dtype="Int64"), "url": None, "openalex_id": None,
            "doi_resolved": None, "doi_resolution_source": None,
        }
    )
    source = pd.DataFrame(
        [{"source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
          "access_date": access_date, "license": SOURCE_LICENSE,
          "notes": CITATION + " Version and license are from the SEANOE page (the workbook states neither)."}]
    )
    tables = {
        "source": source, "reference": reference_df, "citation": citation,
        "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
        "method": method, "fact": fact,
    }

    # ---- accounting: every numeric value cell of the four columns is a fact; no skips
    if len(fact) != sum(n_cells.values()):
        raise ContractError("facts != numeric value cells")
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference_df.reference_key),
        ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    other_colours = {a: {k: v for k, v in c.items() if k == "other"} for a, c in colour_counts.items()}
    report = {
        "source_rows": n, "facts": len(fact), "skipped": 0, "formula_cells": layout["formula_cells"],
        "numeric_cells_by_attribute": n_cells,
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"]).size().items()
        },
        "font_colour_class_by_attribute": colour_counts,
        "value_cells_with_unlisted_font_colour": sum(v.get("other", 0) for v in other_colours.values()),
        "error_type_counts": err_type_counts, "error_values_without_type": n_err_no_type,
        "error_types_without_value": n_err_type_no_error, "negative_error_values": n_err_negative,
        "error_cells_beside_a_blank_value": int(sum(
            1 for col, (ecol, _t) in ERROR_COLUMNS.items() for i in range(n)
            if num_cell(i, col, "value") is None and num_cell(i, ecol, "error") is not None)),
        "coral_error_cells": int(sum(1 for i in range(n) if num_cell(i, "AA", "% Coral Error") is not None)),
        "coral_error_cells_beside_a_blank_cover": int(sum(
            1 for i in range(n) if num_cell(i, "Z", "cover") is None and num_cell(i, "AA", "% Coral Error") is not None)),
        "facts_with_sd": int(fact.sd.notna().sum()), "facts_with_se": int(fact.se.notna().sum()),
        "not_loaded_non_blank_cells": not_loaded,
        "samples": len(sample), "locations": len(loc), "methods": len(method),
        "methods_by_attribute": method.attribute.value_counts().sort_index().to_dict(),
        "references": len(reference_df), "citations": len(citation),
        "year_pub_conflicts_within_a_string": year_pub_conflicts,
        "year_pub_differs_from_year_in_string": year_pub_differs_from_string,
        "rows_year_missing": int(sum(1 for y in year if y is None)),
        "rows_with_temperature": int(sum(1 for t in temp if t is not None)),
        "rows_with_depth": int(sum(1 for d in depth if d is not None)),
        "reef_type_counts": pd.Series(reef).value_counts().to_dict(),
        "text_coordinates_rows": int(sum(1 for i in range(n) if isinstance(val(i, "G"), str) or isinstance(val(i, "H"), str))),
        "flag_counts": count_flags(fact["flags"]),
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
    rows, layout = read_raw(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(rows, layout, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
