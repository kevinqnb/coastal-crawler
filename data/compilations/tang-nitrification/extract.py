#!/usr/bin/env python3
"""Tang et al. database of nitrification and nitrifiers in the global ocean -> compilation-contract tables.

Tang, W., Ward, B.B., Beman, M., Bristow, L., Clark, D., Fawcett, S., et al. (2023): Database of nitrification and nitrifiers in the
global ocean. Earth System Science Data 15, 5039-5077, doi:10.5194/essd-15-5039-2023; data doi:10.5281/zenodo.8355912 (Zenodo v1,
2023-09-18, CC BY 4.0). Raw file (this directory): ``Database of nitrification and nitrifiers in the global ocean.xlsx`` (8 sheets). The
``Template_...xlsx`` beside it is the empty submission template and is not read.

Loaded (phase 1):
  ``volumetric ammonia oxidation`` -> ``tang_ammonia_oxidation``  C5 ammonia_oxidation, nmol N L-1 d-1
  ``volumetric nitrite oxidation`` -> ``tang_nitrite_oxidation``  C5 nitrite_oxidation, nmol N L-1 d-1
  (``sd`` is the sheet's ``Standard deviation of rate`` where it is a number), and from the same rows the J1 water values
  ``Ammonium+ammonia`` -> ``water_ammonium``, ``Nitrite`` -> ``water_nitrite``, ``Nitrate`` -> ``water_nitrate``, ``O2`` -> ``water_o2``
  (all ``μM``, U+03BC, from the headers) and ``pH`` -> ``water_ph`` (``unitless``).
The sheets ``rate metadata`` and ``rate metadata 2`` give the method row of each ``Data source`` (technique is ``Method``, every column in
``params``). Not loaded (listed in ``extract_report.json``): the gene-abundance sheets ``ammonia oxidizer`` and ``NOB`` with their qPCR
metadata sheets (phase 2), and the rate sheets' ``PAR``, ``Light level`` and ``Potential density`` columns (phase 2 ``other``).

Missing codes (per column): blank and ``NA`` give no value (counted). ``NM`` (not measured) and ``BDL`` (below detection limit) in a
fact-bearing column give a skip row with reason ``not_measured`` or ``below_detection_limit`` (the detection limit is copied into the skip
row). Any other non-numeric string raises. In the slot columns (depth, temperature, salinity), ``NA``, ``NM`` and ``BDL`` give NULL.

J1 rules (INTEGRATION_PLAN 1.2):
  * A J1 value belongs to the SAMPLE: one fact per (sample, attribute). The sample key is latitude | longitude | depth | date (floats cast
    before the key is built, datetimes as ISO dates, text dates verbatim). The plan's wording also names the data source, but its own counts
    (2,352 collapsed repeats) reproduce only WITHOUT it: the two rate sheets spell the same paper differently ('Narure Comms' / 'Nature Comms',
    'LO.' / 'L&O.', trailing dots) and the same station is reported under two papers (Beman 2012 / 2013, Wan 2018 / 2022). So the reference
    stays on the fact and is not part of the key. Reference strings are never merged by similarity. A collapsed value keeps the reference of
    the first raw cell (sheet order, then row order), and ``source_row_ref`` points at that cell.
  * Equal values (exactly, no tolerance) of one (sample, attribute) collapse into one fact and are counted. Different values are all loaded
    with ``covariate_conflict``, and nothing is averaged or dropped.
  * J1 values on a row whose rate is ``BDL`` or ``NM`` still load (the sample exists because they reference it).
  * ``pH`` (the plan said the column was empty) has no unit anywhere in the sheet. It is J1 ``water_ph`` with units ``unitless`` (the user's
    decision, as for Fulweiler).
  * Sample slots: ``sample_depth_m`` is Depth, plus ``water_temperature_c`` and ``water_salinity`` (``psu``). One sample's rows must agree on
    a slot. ``SLOT_CONFLICT_SAMPLES`` names the one sample where two replicate casts disagree (-67.774 N, 80 m, 2011-01-17): its slots are
    NULL (not averaged) and the case is in the report. Any other disagreement raises.

Dates: a datetime gives year and month. The 29 text forms are an explicit table (``DATE_TEXT``). ``YYYY Month`` gives year and month, a range
inside one year (``1993 March to May``) gives the year with month NULL, and the cross-year range ``2008 Dec-2009 Jan`` gives year and month
NULL (``year_missing``). Any range text is kept in ``sample.depth_notes``.

``Data source`` column: the reference string is on the first row of a block, and the following rows leave it blank, EXCEPT that comment
lines ('Data shown in Figure 3', 'detection limit ...', 'INSPIRE', 'SSB Autumn') are written into the same column. Comment lines are
recognised by the explicit ``NOTE_PREFIXES`` and ``NOTE_EXACT`` tables, and everything else must look like a reference (a year followed by a
full stop, or 'unpublished') or the extract raises. A row belongs to the last reference above it. The block's ``detection limit`` comments and
the ``rate metadata`` limit are copied into every BDL skip row. reference_key is ``ref:<normalised string>`` (no DOIs in the file).

Locations: location_key = latitude | longitude. habitat_type is the constant ``water column`` (not stated by the source, so ``habitat_from_scope``
on every fact). Longitudes are within +-180 (asserted).

Methods: for rate facts, one row per (attribute, Data source), ``matched`` when the string equals one in that process's metadata sheet and
otherwise method_id_unmatched (technique NULL because unknown). J1 attributes get one method_id_null row each.

Flags: habitat_from_scope (all), covariate_conflict, zero_value, negative_value, year_missing, depth_missing.

Provenance: ``source_row_ref`` = ``<file>[<sheet>]#data_row=<0-based row>:<attribute>``.

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from _g4_common import J1Cell, collapse_j1, is_blank, is_num, norm_ws, number  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "tang-nitrification"
SOURCE_NAME = "Tang et al. database of nitrification and nitrifiers in the global ocean"
SOURCE_DOI = "10.5281/zenodo.8355912"
SOURCE_VERSION = "v1 (2023-09-18)"  # Zenodo record, read by the integration plan; the workbook states no version
SOURCE_LICENSE = "CC BY 4.0"  # Zenodo record
DATA_FILE = "Database of nitrification and nitrifiers in the global ocean.xlsx"
CITATION = (
    "Tang, W., Ward, B.B., Beman, M., Bristow, L., Clark, D., Fawcett, S., Frey, C., Fripiat, F., Herndl, G.J., "
    "Mdutyana, M., Paulot, F., Peng, X., Santoro, A.E., Shiozaki, T., Sintes, E., Stock, C., Sun, X., Wan, X.S., Xu, M.N., "
    "Zhang, Y. (2023): Database of nitrification and nitrifiers in the global ocean [Dataset]. Zenodo, "
    "https://doi.org/10.5281/zenodo.8355912 (paper: Earth System Science Data 15, 5039-5077, "
    "https://doi.org/10.5194/essd-15-5039-2023)"
)
ALLOWED_SKIP_REASONS = frozenset({"below_detection_limit", "not_measured"})
check_skip_reasons(ALLOWED_SKIP_REASONS)

SHEET_NAMES = [
    "volumetric ammonia oxidation", "rate metadata", "ammonia oxidizer", "qPCR metadata",
    "volumetric nitrite oxidation", "rate metadata 2", "NOB", "qPCR metadata 2",
]
RATE_UNIT = "nmol N L-1 d-1"
# (sheet, attribute, metadata sheet)
PROCESSES = [
    ("volumetric ammonia oxidation", "tang_ammonia_oxidation", "rate metadata"),
    ("volumetric nitrite oxidation", "tang_nitrite_oxidation", "rate metadata 2"),
]
DATA_COLUMNS = [
    "Data source", "Date", "Latitude", "Longitude", "Depth (m)", f"Rate ({RATE_UNIT})",
    f"Standard deviation of rate ({RATE_UNIT})", "PAR (μmol Einstein m-2 s-1)", "Light level (%)", "Temperature (°C)",
    "Salinity (psu)", "Potential density (kg m-3)", "Ammonium+ammonia (μM)", "Nitrite (μM)", "Nitrate (μM)", "pH", "O2 (μM)",
]
RATE_COL = f"Rate ({RATE_UNIT})"
SD_COL = f"Standard deviation of rate ({RATE_UNIT})"
DEPTH_COL, TEMP_COL, SAL_COL = "Depth (m)", "Temperature (°C)", "Salinity (psu)"
# header -> (attribute, units_raw)
J1_COLUMNS: dict[str, tuple[str, str]] = {
    "Ammonium+ammonia (μM)": ("water_ammonium", "μM"),
    "Nitrite (μM)": ("water_nitrite", "μM"),
    "Nitrate (μM)": ("water_nitrate", "μM"),
    "O2 (μM)": ("water_o2", "μM"),
    "pH": ("water_ph", "unitless"),  # no unit anywhere in the sheet: `unitless`, as decided by the user
}
NOT_LOADED_COLUMNS = ["PAR (μmol Einstein m-2 s-1)", "Light level (%)", "Potential density (kg m-3)"]
_META_COMMON = [
    "Data source", "Method", "Tracer", "Final tracer concentration (nM)", "Tracer enrichment (%)",
]
META_COLUMNS = {
    "rate metadata": _META_COMMON + [
        "Nitrite carrier (nM)", "Substrate kinetic experiment", "Incubation condition", "in-situ light simulation",
        "in-situ temperature simulation", "in-situ oxygen simulation", "Incubator", "Incubation volume (mL)",
        "Incubation time (hours)", "Termination", "Final measured product", "Isotope analysis method",
        "Rate calculation", "Detection limit (nmol N L-1 d-1)",
    ],
    "rate metadata 2": _META_COMMON + [
        "Nitrate carrier (nM)", "Substracte kinetic experiment", "Incubation condition", "in-situ light simulation",
        "in-situ temperature simulation", "in-situ oxygen simulation", "Incubator", "Incubation volume (mL)",
        "Incubation time (hours)", "Termination", "Final measured product", "Isotope analysis method",
        "Rate calculation", "Detection limit (nmol N L-1 d-1)",
    ],
}
META_LIMIT_COL = "Detection limit (nmol N L-1 d-1)"
NA_CODES = frozenset({"NA"})
BDL_CODE, NM_CODE = "BDL", "NM"

# Comment lines inside the 'Data source' column (everything that is not a reference).
NOTE_PREFIXES = (
    "data shown", "data provided", "data from", "data obtained", "detection limit", "average data", "extracted data",
)
NOTE_EXACT = frozenset({
    "inspire", "ssb autumn", "ssb spring", "ukoa", "arctic", "time-series study",
    "(agulhas current/sw subtropical indian ocean)",
})
_REF_RE = re.compile(r"(?:(?:19|20)\d\d[a-z]?\.)|unpublished")
_YEAR_RE = re.compile(r"(?:19|20)\d\d")

# every text date form in the two rate sheets -> (year, month); None where the form does not give one
DATE_TEXT: dict[str, tuple[int | None, int | None]] = {
    "1982 June": (1982, 6), "1982 Nov": (1982, 11), "1983 May": (1983, 5), "1983 Nov": (1983, 11),
    "1985 Feb": (1985, 2), "1993 March to May": (1993, None), "1994 Nov": (1994, 11), "1995 Jan": (1995, 1),
    "2000 May": (2000, 5), "2005 April": (2005, 4), "2005 October": (2005, 10), "2007 July": (2007, 7),
    "2008 Dec-2009 Jan": (None, None), "2008 May/June": (2008, None), "2009 Feb": (2009, 2),
    "2009 October": (2009, 10), "2010 April": (2010, 4), "2010 August": (2010, 8), "2011 April": (2011, 4),
    "2011 August": (2011, 8), "2011 January": (2011, 1), "2012 January": (2012, 1), "2012 March": (2012, 3),
    "2013 June": (2013, 6), "2015 April": (2015, 4), "2015 August": (2015, 8), "2016 May": (2016, 5),
    "2017 July": (2017, 7), "2017 June": (2017, 6),
}
DATE_RANGES = frozenset({"1993 March to May", "2008 Dec-2009 Jan", "2008 May/June"})
# Samples whose rows disagree on a slot (temperature / salinity); their slots are NULL. Key format of sample_key.
SLOT_CONFLICT_SAMPLES = frozenset({"-67.774|-69.27616666666667|80.0|2011-01-17"})


def norm_header(s: object) -> str:
    if not isinstance(s, str):
        raise ContractError(f"header {s!r} is not text")
    return norm_ws(s)


def code_of(v: object, what: str) -> tuple[str, float | None]:
    """('blank'|'NA'|'NM'|'BDL'|'num', value). Any other non-numeric cell raises."""
    if is_blank(v):
        return "blank", None
    if is_num(v):
        return "num", number(v, what)
    if isinstance(v, str):
        t = v.strip()
        if t in NA_CODES:
            return "NA", None
        if t == NM_CODE:
            return "NM", None
        if t == BDL_CODE:
            return "BDL", None
    raise ContractError(f"{what}: unrecognised non-numeric value {v!r}")


def is_note(t: str) -> bool:
    low = t.lower()
    return low in NOTE_EXACT or low.startswith(NOTE_PREFIXES)


def parse_date(v: object, what: str) -> tuple[str, int | None, int | None, str | None]:
    """(date part of the sample key, year, month, range text or None)."""
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        d = pd.Timestamp(v)
        if d.hour or d.minute or d.second:
            raise ContractError(f"{what}: date {v!r} carries a time of day")
        return d.date().isoformat(), d.year, d.month, None
    if isinstance(v, str):
        t = norm_ws(v)
        if t not in DATE_TEXT:
            raise ContractError(f"{what}: unrecognised date text {v!r}")
        y, m = DATE_TEXT[t]
        return t, y, m, (t if t in DATE_RANGES else None)
    raise ContractError(f"{what}: unrecognised date cell {v!r}")


def read_raw(path: Path) -> dict[str, pd.DataFrame]:
    xl = pd.ExcelFile(path)
    if xl.sheet_names != SHEET_NAMES:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names}")
    kw = dict(dtype=object, keep_default_na=False, na_values=[])
    return {s: xl.parse(s, **kw) for s in SHEET_NAMES}  # type: ignore[arg-type]


def _text(v: object) -> str | None:
    if is_blank(v):
        return None
    if isinstance(v, str):
        return norm_ws(v)
    if is_num(v):
        f = float(v)  # type: ignore[arg-type]
        return str(int(f)) if f == int(f) else repr(f)
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        return pd.Timestamp(v).isoformat()
    raise ContractError(f"unexpected cell {v!r}")


def _fkey(x: float | None) -> str:
    return "" if x is None else repr(float(x))


# ---------------------------------------------------------------- rows of one rate sheet


def read_rate_sheet(df: pd.DataFrame, sheet: str) -> list[dict]:
    cols = [norm_header(c) for c in df.columns]
    if cols != DATA_COLUMNS:
        raise ContractError(f"{sheet}: unexpected columns {cols}")
    df = df.set_axis(DATA_COLUMNS, axis=1)
    rows: list[dict] = []
    ref: str | None = None
    notes: list[str] = []
    for i in range(len(df)):
        r = df.iloc[i]
        t = norm_ws(r["Data source"]) if isinstance(r["Data source"], str) else ""
        if r["Data source"] is not None and not isinstance(r["Data source"], str) and not is_blank(r["Data source"]):
            raise ContractError(f"{sheet} row {i}: Data source cell {r['Data source']!r} is not text")
        if t and not is_note(t):
            if not _REF_RE.search(t):
                raise ContractError(f"{sheet} row {i}: Data source {t!r} is neither a known comment nor a reference")
            if t != ref:
                ref, notes = t, []
        elif t:
            if ref is None:
                raise ContractError(f"{sheet} row {i}: a comment before the first reference")
            notes.append(t)
        if ref is None:
            raise ContractError(f"{sheet}: first row has no reference")
        what = f"{sheet} row {i}"
        lat, lon = r["Latitude"], r["Longitude"]
        if not (is_num(lat) and is_num(lon)):
            raise ContractError(f"{what}: coordinates {lat!r}, {lon!r}")
        lat, lon = float(lat), float(lon)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ContractError(f"{what}: coordinates out of range")
        kind, depth = code_of(r[DEPTH_COL], what + " depth")
        if kind in ("NM", "BDL"):
            raise ContractError(f"{what}: depth coded {kind}")
        if depth is not None and depth < 0:
            raise ContractError(f"{what}: negative depth")
        datekey, year, month, rng = parse_date(r["Date"], what)
        rows.append({
            "sheet": sheet, "i": i, "ref": ref, "notes": list(notes), "lat": lat, "lon": lon, "depth": depth,
            "datekey": datekey, "year": year, "month": month, "range": rng,
            "rate": code_of(r[RATE_COL], what + " rate"), "sd": code_of(r[SD_COL], what + " sd"),
            "temp": code_of(r[TEMP_COL], what + " temperature"), "sal": code_of(r[SAL_COL], what + " salinity"),
            "j1": {c: code_of(r[c], f"{what} {c}") for c in J1_COLUMNS},
            "not_loaded": {c: code_of(r[c], f"{what} {c}")[0] for c in NOT_LOADED_COLUMNS},
        })
        rows[-1]["sample_key"] = f"{_fkey(lat)}|{_fkey(lon)}|{_fkey(depth)}|{datekey}"
    return rows


def read_meta(df: pd.DataFrame, sheet: str) -> dict[str, dict]:
    cols = [norm_header(c) for c in df.columns]
    if cols != META_COLUMNS[sheet]:
        raise ContractError(f"{sheet}: unexpected columns {cols}")
    out: dict[str, dict] = {}
    for i in range(len(df)):
        ds = _text(df.iat[i, 0])
        if ds is None:
            raise ContractError(f"{sheet} row {i}: blank Data source")
        if ds in out:
            raise ContractError(f"{sheet}: Data source {ds!r} twice")
        out[ds] = {c: _text(df.iat[i, j]) for j, c in enumerate(cols) if j > 0}
    return out


# ---------------------------------------------------------------- main build


def build_tables(
    raw: dict[str, pd.DataFrame], access_date: str
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    records: list[dict] = []
    meta_by_sheet: dict[str, dict[str, dict]] = {}
    for sheet, attr, meta_sheet in PROCESSES:
        recs = read_rate_sheet(raw[sheet], sheet)
        for r in recs:
            r["attr"] = attr
            r["meta_sheet"] = meta_sheet
        records.extend(recs)
        meta_by_sheet[meta_sheet] = read_meta(raw[meta_sheet], meta_sheet)
    for n, r in enumerate(records):  # fixed raw order: sheet order, then row order
        r["order"] = n

    # ---- sample slots: rows of one sample must agree
    slot_vals: dict[str, dict[str, set[float]]] = {}
    for r in records:
        d = slot_vals.setdefault(r["sample_key"], {"temp": set(), "sal": set()})
        for k in ("temp", "sal"):
            if r[k][0] == "num":
                d[k].add(r[k][1])  # type: ignore[arg-type]
    conflicts = {k for k, d in slot_vals.items() if len(d["temp"]) > 1 or len(d["sal"]) > 1}
    if conflicts != set(SLOT_CONFLICT_SAMPLES):
        raise ContractError(f"samples whose rows disagree on temperature/salinity {sorted(conflicts)} differ from "
                            f"the allow-listed {sorted(SLOT_CONFLICT_SAMPLES)}")

    # ---- facts and skips
    skips: list[dict] = []
    rate_facts: list[dict] = []
    j1_cells: list[J1Cell] = []
    cell_counts = {"rate_num": 0, "rate_skipped": 0, "j1_num": 0, "j1_skipped": 0}
    coded: dict[str, dict[str, int]] = {"NA": {}, "NM": {}, "BDL": {}}  # per column

    def tally(col: str, kind: str) -> None:
        if kind in coded:
            coded[kind][col] = coded[kind].get(col, 0) + 1

    def skip_row(r: dict, attr: str, reason: str, cell: str, extra: dict | None = None) -> None:
        s = {"file": DATA_FILE, "sheet": r["sheet"], "data_row": r["i"], "attribute": attr, "reason": reason,
             "cell": cell, "reference": r["ref"]}
        if extra:
            s.update(extra)
        skips.append(s)

    n_rows_rate_skipped_with_j1 = 0
    for r in records:
        meta = meta_by_sheet[r["meta_sheet"]].get(r["ref"])
        limit_info = {
            "detection_limit_metadata": meta[META_LIMIT_COL] if meta else None,
            "detection_limit_comments": [n for n in r["notes"] if n.lower().startswith("detection limit")],
        }
        kind, val = r["rate"]
        tally(RATE_COL, kind)
        if kind == "num":
            cell_counts["rate_num"] += 1
            rate_facts.append({"rec": r, "value": val})
        elif kind in ("BDL", "NM"):
            cell_counts["rate_skipped"] += 1
            skip_row(r, r["attr"], "below_detection_limit" if kind == "BDL" else "not_measured", kind,
                     limit_info if kind == "BDL" else None)
        for col, (kind, val) in ((c, r["j1"][c]) for c in J1_COLUMNS):
            tally(col, kind)
            attr = J1_COLUMNS[col][0]
            if kind == "num":
                cell_counts["j1_num"] += 1
                j1_cells.append(J1Cell(r["order"], r["sample_key"], attr, val,  # type: ignore[arg-type]
                                       f"{DATA_FILE}[{r['sheet']}]#data_row={r['i']}:{attr}", r))
            elif kind in ("BDL", "NM"):
                cell_counts["j1_skipped"] += 1
                skip_row(r, attr, "below_detection_limit" if kind == "BDL" else "not_measured", kind,
                         limit_info if kind == "BDL" else None)
        if r["rate"][0] in ("BDL", "NM") and any(r["j1"][c][0] == "num" for c in J1_COLUMNS):
            n_rows_rate_skipped_with_j1 += 1

    j1_facts, j1_counts = collapse_j1(j1_cells)

    # ---- flags / fact rows
    def sample_flags(r: dict) -> list[str]:
        fl = ["habitat_from_scope"]
        if r["year"] is None:
            fl.append("year_missing")
        if r["depth"] is None:
            fl.append("depth_missing")
        return fl

    fact_rows: list[dict] = []
    methods: dict[str, dict] = {}

    def method_for_rate(r: dict) -> str:
        key = f"{r['attr']}|ref:{r['ref']}"
        if key not in methods:
            meta = meta_by_sheet[r["meta_sheet"]].get(r["ref"])
            methods[key] = _method_row(
                key, r["attr"], "matched" if meta else "method_id_unmatched",
                meta["Method"] if meta else None, json.dumps(meta or {}, sort_keys=True),
            )
        return key

    def method_for_cov(attr: str) -> str:
        if attr not in methods:
            methods[attr] = _method_row(attr, attr, "method_id_null", None, json.dumps({}))
        return attr

    sd_without_fact = 0
    for rf in rate_facts:
        r, v = rf["rec"], rf["value"]
        attr = r["attr"]
        decl = ATTRIBUTE_DECL[attr]
        if RATE_UNIT not in decl.units or not RATE_COL.endswith(f"({RATE_UNIT})"):
            raise ContractError(f"{attr}: rate unit")
        fl = sample_flags(r)
        if v == 0:
            fl.append("zero_value")
        if v < 0:
            fl.append("negative_value")
        sdk, sdv = r["sd"]
        if sdv is not None and sdv < 0:
            fl.append("negative_sd")
        fact_rows.append({
            "order": r["order"], "source_key": SOURCE_KEY, "fact_key": f"{r['sheet']}#{r['i']}:{attr}",
            "reference_key": f"ref:{r['ref']}", "location_key": f"{_fkey(r['lat'])}|{_fkey(r['lon'])}",
            "sample_key": r["sample_key"], "method_key": method_for_rate(r), "attribute": attr,
            "measurement_type": decl.measurement_type, "subfield": next(iter(decl.subfields)), "value": v,
            "units_raw": RATE_UNIT, "sd": np.nan if sdv is None else sdv, "se": np.nan, "n": None,
            "flags": sorted(fl), "source_row_ref": f"{DATA_FILE}[{r['sheet']}]#data_row={r['i']}:{attr}",
        })
    for r in records:
        if r["sd"][0] == "num" and r["rate"][0] != "num":
            sd_without_fact += 1
    for jf in j1_facts:
        c = jf.cell
        r = c.payload
        attr = c.attribute
        decl = ATTRIBUTE_DECL[attr]
        units = next(u for col, (a, u) in J1_COLUMNS.items() if a == attr)
        if units not in decl.units or (attr != "water_ph" and not any(
                col.endswith(f"({units})") for col, (a, _u) in J1_COLUMNS.items() if a == attr)):
            raise ContractError(f"{attr}: unit {units!r} not declared / not the header's")
        fl = sample_flags(r)
        if c.value == 0:
            fl.append("zero_value")
        if c.value < 0:
            fl.append("negative_value")
        if jf.covariate_conflict:
            fl.append("covariate_conflict")
        fact_rows.append({
            "order": r["order"], "source_key": SOURCE_KEY, "fact_key": f"{r['sheet']}#{r['i']}:{attr}",
            "reference_key": f"ref:{r['ref']}", "location_key": f"{_fkey(r['lat'])}|{_fkey(r['lon'])}",
            "sample_key": r["sample_key"], "method_key": method_for_cov(attr), "attribute": attr,
            "measurement_type": decl.measurement_type, "subfield": next(iter(decl.subfields)), "value": c.value,
            "units_raw": units, "sd": np.nan, "se": np.nan, "n": None, "flags": sorted(fl), "source_row_ref": c.ref,
        })
    fact_rows.sort(key=lambda f: (f["order"], f["attribute"]))
    fact = pd.DataFrame(fact_rows).drop(columns="order")
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    if fact.fact_key.duplicated().any():
        raise ContractError("duplicate fact_key")

    # ---- accounting
    n_cells_in = cell_counts["rate_num"] + cell_counts["rate_skipped"] + cell_counts["j1_num"] \
        + cell_counts["j1_skipped"]
    n_collapsed = j1_counts["collapsed_repeats"]
    if len(fact) + len(skips) + n_collapsed != n_cells_in:
        raise ContractError(f"cells {n_cells_in} != facts {len(fact)} + skipped {len(skips)} + collapsed {n_collapsed}")
    if len(fact) != cell_counts["rate_num"] + j1_counts["facts"]:
        raise ContractError("fact count differs from rate cells + J1 facts")
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")

    # ---- samples (only those a fact references)
    first_rec: dict[str, dict] = {}
    for r in records:
        first_rec.setdefault(r["sample_key"], r)
    used = sorted(set(fact.sample_key))
    s_rows = []
    for k in used:
        r = first_rec[k]
        t = sorted(slot_vals[k]["temp"]) if k not in SLOT_CONFLICT_SAMPLES else []
        s = sorted(slot_vals[k]["sal"]) if k not in SLOT_CONFLICT_SAMPLES else []
        if len(t) > 1 or len(s) > 1:
            raise ContractError(f"sample {k}: slot conflict")
        s_rows.append({
            "source_key": SOURCE_KEY, "sample_key": k, "source_sample_id": None,
            "depth_min_cm": np.nan, "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": f"date as given: {r['range']}" if r["range"] else None,
            "sampling_year": r["year"], "sampling_month": r["month"], "taxon": None, "taxon_rank": None,
            "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None, "photosynthetic_sediment": None,
            "sample_depth_m": np.nan if r["depth"] is None else r["depth"],
            "water_temperature_c": t[0] if t else np.nan, "water_salinity": s[0] if s else np.nan,
            "water_salinity_units": "psu" if s else None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
        })
    sample = pd.DataFrame(s_rows)
    sample["sampling_year"] = pd.array(sample["sampling_year"].tolist(), dtype="Int64")
    sample["sampling_month"] = pd.array(sample["sampling_month"].tolist(), dtype="Int64")

    # ---- locations (only those a fact references)
    lk = sorted(set(fact.location_key))
    first_loc = {}
    for r in records:
        first_loc.setdefault(f"{_fkey(r['lat'])}|{_fkey(r['lon'])}", r)
    n = len(lk)
    loc = pd.DataFrame({
        "source_key": SOURCE_KEY, "location_key": lk, "site_name": None, "source_site_id": None, "source_core_id": lk,
        "latitude": [first_loc[k]["lat"] for k in lk], "longitude": [first_loc[k]["lon"] for k in lk],
        "position_accuracy": np.nan, "position_accuracy_units": None, "position_method": None, "country": None,
        "admin_division": None, "water_body": None, "habitat_type": "water column", "geomorphic_setting": None,
        "bioregion": None, "marine_ecoregion": None, "salinity_class": None, "salinity_class_level": None,
        "vegetation_class": None, "vegetation_class_level": None, "tidal_position": None, "tidal_position_level": None,
        "modification_status": [[] for _ in range(n)], "dominant_species": [[] for _ in range(n)],
        "elevation": np.nan, "elevation_units": None, "elevation_datum": None, "elevation_method": None,
        "water_depth_m": np.nan, "distance_to_shore_km": np.nan,
    })

    # ---- references
    ref_strs = sorted({k[len("ref:"):] for k in fact.reference_key})
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": [f"ref:{s}" for s in ref_strs]})
    years = []
    for s in ref_strs:
        ys = _YEAR_RE.findall(s)
        years.append(int(ys[0]) if ys else None)
    citation = pd.DataFrame({
        "source_key": SOURCE_KEY, "citation_key": [f"ref:{s}|{s}" for s in ref_strs],
        "reference_key": [f"ref:{s}" for s in ref_strs], "bibliography_id": ref_strs, "publication_type": None,
        "doi": None, "title": None, "authors": None, "year": pd.array(years, dtype="Int64"), "url": None,
        "openalex_id": None, "doi_resolved": None, "doi_resolution_source": None,
    })
    method = pd.DataFrame(sorted(methods.values(), key=lambda m: m["method_key"]))
    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + " Version and license are from the Zenodo record (the workbook states neither).",
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

    # ---- report
    flag_counts: dict[str, int] = {}
    for fl in fact["flags"]:
        for x in fl:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    gene_sheets = {}
    for s in ("ammonia oxidizer", "NOB"):
        df = raw[s]
        gene_sheets[s] = {"rows": len(df), "numeric_cells_in_copies_columns": int(sum(
            1 for c in df.columns if "copies" in str(c) for v in df[c] if is_num(v)))}
    all_refs_in_data = {r["ref"] for r in records}
    matched_meta = {(m, r["ref"]) for r in records for m in [r["meta_sheet"]] if r["ref"] in meta_by_sheet[m]}
    report = {
        "source_rows_by_sheet": {s: sum(1 for r in records if r["sheet"] == s) for s, _, _ in PROCESSES},
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"]).size().items()
        },
        "cells_in": n_cells_in,
        "cells_rate_numeric": cell_counts["rate_num"],
        "cells_rate_skipped": cell_counts["rate_skipped"],
        "cells_j1_numeric": cell_counts["j1_num"],
        "cells_j1_skipped_bdl_nm": cell_counts["j1_skipped"],
        "j1_collapse": j1_counts,
        "j1_collapsed_repeats_by_attribute": {
            a: int(sum(1 for c in j1_cells if c.attribute == a) - sum(1 for f in j1_facts if f.cell.attribute == a))
            for a in sorted({c.attribute for c in j1_cells})},
        "j1_conflict_definition": "conflict_groups = (sample, attribute) pairs with >=2 different values; "
                                   "conflict_facts = the distinct values loaded with covariate_conflict",
        "j1_facts_on_rows_whose_rate_was_skipped": int(sum(
            1 for jf in j1_facts if jf.cell.payload["rate"][0] in ("BDL", "NM"))),
        "rows_with_skipped_rate_and_a_j1_value": n_rows_rate_skipped_with_j1,
        "skipped": len(skips),
        "bdl_skips_without_any_detection_limit_text": int(sum(
            1 for s in skips if s["reason"] == "below_detection_limit" and "detection_limit_metadata" in s
            and s["detection_limit_metadata"] is None and not s["detection_limit_comments"])),
        "float_nan_cells_read_as_blank": {
            c: int(sum(1 for sheet, _, _ in PROCESSES for v in raw[sheet][c2]
                       if isinstance(v, float) and v != v)) for c, c2 in
            ((norm_header(c), c) for c in raw[PROCESSES[0][0]].columns)
            if sum(1 for sheet, _, _ in PROCESSES for v in raw[sheet][c2] if isinstance(v, float) and v != v)},
        "skipped_by_reason": pd.Series([s["reason"] for s in skips]).value_counts().sort_index().to_dict(),
        "skipped_by_attribute_reason": {
            f"{a}|{rsn}": int(c) for (a, rsn), c in pd.DataFrame(skips).groupby(["attribute", "reason"]).size().items()},
        "coded_missing_cells_by_column": coded,
        "sd_numeric_without_rate_fact": sd_without_fact,
        "facts_with_sd": int(fact.sd.notna().sum()),
        "slot_conflict_samples_nulled": sorted(SLOT_CONFLICT_SAMPLES),
        "samples": len(sample),
        "samples_distinct_in_raw": len({r["sample_key"] for r in records}),
        "locations": len(loc),
        "methods": len(method),
        "methods_by_status": method.method_status.value_counts().sort_index().to_dict(),
        "references": len(reference),
        "references_in_data": len(all_refs_in_data),
        "references_without_facts": sorted(all_refs_in_data - set(ref_strs)),
        "metadata_rows": {m: len(d) for m, d in meta_by_sheet.items()},
        "metadata_rows_matching_a_data_reference": len(matched_meta),
        "metadata_sources_not_in_data": {m: sorted(set(d) - {r["ref"] for r in records if r["meta_sheet"] == m})
                                         for m, d in meta_by_sheet.items()},
        "citations_without_year": int(citation.year.isna().sum()),
        "flag_counts": flag_counts,
        "samples_without_year": int(sample.sampling_year.isna().sum()),
        "samples_without_month": int(sample.sampling_month.isna().sum()),
        "samples_without_depth": int(sample.sample_depth_m.isna().sum()),
        "samples_with_temperature": int(sample.water_temperature_c.notna().sum()),
        "samples_with_salinity": int(sample.water_salinity.notna().sum()),
        "not_loaded_columns_numeric_cells": {
            c: int(sum(1 for r in records if r["not_loaded"][c] == "num")) for c in NOT_LOADED_COLUMNS},
        "not_loaded_sheets": gene_sheets,
    }
    return tables, skips, report


def _method_row(key: str, attr: str, status: str, technique: str | None, params: str) -> dict:
    return {
        "source_key": SOURCE_KEY, "method_key": key, "source_method_id": None, "attribute": attr,
        "method_status": status, "technique": technique, "coring_method": None, "carbon_type": None,
        "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
        "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": None, "light_dark": None,
        "incubation_type": None, "experimental_status": None, "params": params,
    }


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
            fh.write(json.dumps(s, sort_keys=True, default=str) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
