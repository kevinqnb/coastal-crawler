#!/usr/bin/env python3
"""Shao et al. global ocean diazotroph database v2 -> contract tables (phase 1: N2 fixation rates and J1 water values).

Shao, Z., Xu, Y., Wang, H., Luo, W., Wang, L., Huang, Y., et al. (2023): Version 2 of the global ocean diazotroph database.
figshare, doi:10.6084/m9.figshare.21677687.v3 (paper: Earth System Science Data 2023). Only ``DiazotrophsDatabase-20240109.xlsx``
(version 2024-01-09, CC BY 4.0) is read; the 20230626 workbook and the update notes are not. Sheets loaded: the five rate sheets
``N2_Fixation (Incub. 24 h)``, ``N2_Fixation (Incub.<24 h)``, ``N2_Fixation_Integral (24 h)``, ``N2_Fixation_Integral (<24 h)`` and
``Cell_Specific_N2_Fixation_Rate``. Not loaded (rows counted in the report): ``Cell_Count``, ``Cell_Count_Integral``, ``nifH_Gene``,
``nifH_Gene_Integral``, ``NCD_Gene`` (phase 2), ``Reference List`` (used only to attach citation titles by DOI).

Every sheet is read with ``header=None``; row 0 is the header, normalised (whitespace collapsed, ' )' -> ')') and compared to an
explicit expected list (any other header raises). ``data_row`` is the 0-based row below the header.

Rates (main sheets, ONE attribute per fraction, plan 5.12): Trichodesmium / Heterocyst / <10 um / >10 um / Whole Seawater / Total /
under Light / under Dark -> ``shao_n2fix_{trichodesmium,heterocyst,lt10um,gt10um,whole_seawater,total,light,dark}``, C5 n2_fixation;
the unit is the sheet's header unit (``μmol N m-3 d-1``, ``m-3 h-1``, ``m-2 d-1``, ``m-2 h-1``; U+03BC). Components and Total coexist
in a row (double counting; nothing is summed). ``Cell_Specific`` sheet: 16 organism triples (rate per cell, cellular N, rate per
cellular N): ``shao_n2fix_per_cell`` (fmol N cell-1 d-1), ``shao_cellular_n_content`` (``other``, fmol N cell-1) and
``shao_n2fix_per_cellular_n`` (fmol N fmol N-1 d-1); the organism is ``sample.taxon`` with ``taxon_rank`` from ``TAXA`` (free text:
genus / clade / phylum / cell_type / functional_group / host_of_clade).

J1 (plan 1.2): nitrate (``Nitrate (µM)``, U+00B5), phosphate (``Phosphate (µM)``) and chlorophyll a (``Chlorophyll-a (mg m-3)``) ->
``water_nitrate`` / ``water_phosphate`` / ``water_chl_a``; on the integral sheets the near-surface columns (the same attributes). The
J1 sample key is reference | latitude | longitude | date | depth | family (volumetric / integral / cell); equal values of one sample and
attribute collapse (value, comparator and sd must all be equal), different values are all loaded with ``covariate_conflict``. Fe and
NPP and the C fixation column are not loaded (phase 2, counted). Temperature and salinity go to the sample slots on the volumetric
and cell sheets only: the integral sheets' columns are NEAR-SURFACE values of an integral sample and, like Francesco's SST, are
not loaded.

Cell contents (all fact-bearing columns; user rules): a number -> a fact; blank / whitespace -> nothing (counted); ``mean (sd)``
(e.g. ``2.38 (0.64)``) -> value = the mean, ``sd`` = the bracket number, flag ``source_mean_reported``; a malformed one (``0.49 (0.02``,
``0.01 (0.00)*``) -> skip ``unparseable_value`` (listed in the report); ``<0.02`` / ``> 3`` -> value = the printed number,
``value_comparator`` = the sign and flag ``inequality``; BELOW_DETECTION codes (``n.d.``, ``nd``, ``bdl``, ``bld``, ``bd``, ``b.d.``,
``dnq``, ``u.d.``, ``< DL``, ``DL``) -> skip ``below_detection_limit``; NOT_MEASURED codes (``nm``, ``n.a.``, ``na``, ``n/a``, ``n``,
``/``, ``-``, ``nan``) -> skip ``not_measured``; any other text raises. Counts per code are in the report. In the slot columns
(temperature, salinity) codes and censored values give NULL (counted).

Notes column (last column of each sheet) -> ``method.notes`` verbatim, one method row per distinct (attribute, METHODS string,
incubation time, notes); a number in the Notes cell (misplaced data on the integral sheets) is written as its text and counted.
Flags from Notes: ``converted_by_source`` where a note matches ``CONVERSION_PATTERNS`` (hourly -> daily or daily -> hourly
statements; counts per pattern in the report) and ``qc_rejected_by_source`` where it says ``REJECTED by Quality Control`` (the
values are loaded; the flag is on every rate fact of the row, the note says which fraction).

References: the ``SOURCE`` column mixes references and comment lines. A cell is a REFERENCE if it contains a DOI or an author-year
pattern (starts with a capital letter, has a four-digit year); everything else is a comment. A row belongs to the last reference
above it. Comment-looking leftovers are NOT guessed: every non-reference string is printed in ``extract_report.json`` with the number
of rows that follow it before the next reference, for review. ``reference_key`` = ``doi:<lower-case doi>`` where a DOI is in the
string, else ``ref:<string>``; the citation title comes from ``Reference List`` where its DOI matches.

Habitat ``Water column`` from scope (flag ``habitat_from_scope`` on every fact). Dates: datetime -> year/month; text ranges
(``2008/7/19-8/3``) -> year if both ends agree, month NULL, the text in ``sample.depth_notes``. Depth: volumetric depth ->
``sample_depth_m``; the integral sheets' depth is the integration depth -> ``depth_notes``; a depth that is text goes to
``depth_notes``. Coordinates given as text with a tab (``25.128\\t``) are read as numbers (counted).

Provenance: ``source_row_ref`` = ``<file>[<sheet>]#data_row=<n>:<attribute>`` (Cell_Specific: ``<attribute>[<taxon>]``).

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from _g4_common import J1Cell, collapse_j1, is_blank, is_num, norm_ws  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "shao-nfix"
SOURCE_NAME = "Shao et al. global ocean diazotroph database v2"
SOURCE_DOI = "10.6084/m9.figshare.21677687.v3"
SOURCE_VERSION = "2024-01-09 (figshare v3; DiazotrophsDatabase-20240109.xlsx)"
SOURCE_LICENSE = "CC BY 4.0"
DATA_FILE = "DiazotrophsDatabase-20240109.xlsx"
CITATION = (
    "Shao, Z., Xu, Y., Wang, H., Luo, W., Wang, L., Huang, Y., et al. (2023): Version 2 of the global ocean diazotroph database "
    "[Dataset]. figshare, https://doi.org/10.6084/m9.figshare.21677687.v3 (paper: Earth System Science Data, 2023)"
)
ALLOWED_SKIP_REASONS = frozenset({"below_detection_limit", "not_measured", "unparseable_value"})
check_skip_reasons(ALLOWED_SKIP_REASONS)

SHEETS = ["N2_Fixation (Incub. 24 h)", "N2_Fixation (Incub.<24 h)", "N2_Fixation_Integral (24 h)",
          "N2_Fixation_Integral (<24 h)", "Cell_Specific_N2_Fixation_Rate", "Cell_Count", "Cell_Count_Integral", "nifH_Gene",
          "nifH_Gene_Integral", "NCD_Gene", "Reference List"]
PHASE2_SHEETS = ["Cell_Count", "Cell_Count_Integral", "nifH_Gene", "nifH_Gene_Integral", "NCD_Gene"]
FRACTIONS = ["trichodesmium", "heterocyst", "lt10um", "gt10um", "whole_seawater", "total", "light", "dark"]
# sheet -> (family, unit tail of the headers, sheet code)
RATE_SHEETS = {
    "N2_Fixation (Incub. 24 h)": ("vol", "μmol N m-3 d-1", "v24"),
    "N2_Fixation (Incub.<24 h)": ("vol", "μmol N m-3 h-1", "v1"),
    "N2_Fixation_Integral (24 h)": ("int", "μmol N m-2 d-1", "i24"),
    "N2_Fixation_Integral (<24 h)": ("int", "μmol N m-2 h-1", "i1"),
}
CELL_SHEET = "Cell_Specific_N2_Fixation_Rate"
TAXA = [  # (label as in the headers, taxon_rank)
    ("Trichodesmium", "genus"), ("UCYN-A", "clade"), ("UCYN-A1", "clade"), ("UCYN-A2", "clade"), ("UCYN-B", "clade"),
    ("UCYN-C", "clade"), ("Richelia", "genus"), ("Calothrix", "genus"), ("Heterocyst", "cell_type"),
    ("Cyanobacteria", "phylum"), ("Non-cyanobacteria diazotroph", "functional_group"), ("UCYN-A host", "host_of_clade"),
    ("UCYN-A1 host", "host_of_clade"), ("UCYN-A2 host", "host_of_clade"), ("UCYN-A3 host", "host_of_clade"),
    ("UCYN-B host", "host_of_clade"),
]


def _vol_headers(unit: str) -> list[str]:
    return ["SOURCE: Related article or authors", "METHODS: Sampling/Analysis", "Incubation Time (hour)", "DATE (yyyy-mm-dd)",
            "LATITUDE", "LONGITUDE", "DEPTH (m)", f"Trichodesmium N2 Fixation ({unit})", f"Heterocyst N2 Fixation ({unit})",
            f"< 10 μm N2 Fixation ({unit})", f"> 10 μm N2 Fixation ({unit})", f"Whole Seawater N2 Fixation ({unit})",
            f"Total N2 Fixation ({unit})", f"N2 Fixation under Light ({unit})", f"N2 Fixation under Dark ({unit})",
            "Temperature (˚C)", "Salinity (PSU)", "Nitrate (µM)", "Phosphate (µM)", "Fe (nM)", "Chlorophyll-a (mg m-3)",
            "Net Primary Production (μmol C m-3 d-1)", "Notes"]


def _int_headers(unit: str) -> list[str]:
    return ["SOURCE: Related article or authors", "METHODS: Sampling/Analysis", "Incubation Time (hour)", "DATE (yyyy-mm-dd)",
            "LATITUDE", "LONGITUDE", "DEPTH (m)", f"Trichodesmium N2 Fixation ({unit})", f"Heterocyst N2 Fixation ({unit})",
            f"< 10 μm N2 Fixation ({unit})", f">10 μm N2 Fixation ({unit})", f"Whole Seawater N2 Fixation ({unit})",
            f"Total N2 Fixation ({unit})", "Near-surface Temperature (˚C)", "Near-surface Salinity (PSU)",
            "Near-surface Nitrate (µM)", "Near-surface Phosphate (µM)", "Near-surface Fe (nM)",
            "Near-surface Chlorophyll-a (mg m-3)", "Notes"]


def _clean_header(h: object) -> str:
    return re.sub(r"\s+\)", ")", norm_ws(str(h)))


def expected_headers(sheet: str) -> list[str]:
    if sheet in RATE_SHEETS:
        fam, unit, _ = RATE_SHEETS[sheet]
        return _vol_headers(unit) if fam == "vol" else _int_headers(unit)
    return _cell_headers()


def _cell_headers() -> list[str]:
    out = ["SOURCE: Related article or authors", "METHODS: Sampling/Analysis", "Incubation Time (hour)", "DATE (yyyy-mm-dd)",
           "LATITUDE", "LONGITUDE", "DEPTH (m)"]
    for lab, _rank in TAXA:
        out += [f"{lab}|rate", f"{lab}|cellular_n", f"{lab}|rate_per_n"]
    out += ["Temperature (˚C)", "Salinity (PSU)", "Nitrate (µM)", "Phosphate (µM)", "Fe (nM)", "Chlorophyll-a (mg m-3)",
            "C fixation rate (fmol C cell-1 d-1)", "Net Primary Production (fmol C m-3 d-1)", "Notes", "(unnamed)"]
    return out


def check_cell_headers(hdr: list[str]) -> None:
    """The 16 organism triples are checked by the tokens of their headers; everything else by an explicit list."""
    exp = _cell_headers()
    if len(hdr) != len(exp):
        raise ContractError(f"{CELL_SHEET}: {len(hdr)} columns, expected {len(exp)}")
    for j, (h, e) in enumerate(zip(hdr, exp)):
        if "|" not in e:
            if (e == "(unnamed)" and h != "") or (e != "(unnamed)" and h != e):
                raise ContractError(f"{CELL_SHEET} column {j}: header {h!r}, expected {e!r}")
            continue
        lab, kind = e.split("|")
        low = h.lower()
        ok = {"rate": "(fmol n cell-1 d-1)" in low and "cellular" not in low and low.startswith(lab.lower() + " "),
              "cellular_n": "cellular biomass" in low and low.endswith("(fmol n cell-1)"),
              "rate_per_n": "(fmol n fmol n-1 d-1)" in low}[kind]
        if not ok:
            raise ContractError(f"{CELL_SHEET} column {j}: header {h!r} does not match {e!r}")


# ---- cell classification
BDL_CODES = frozenset({"n.d.", "n.d", "nd", "bdl", "bld", "bd", "b.d.", "dnq", "u.d.", "< dl", "<dl", "dl"})
NM_CODES = frozenset({"nm", "n.a.", "n.a", "na", "n/a", "n", "/", "-", "nan"})
_NUM = r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_PLAIN = re.compile(rf"-?{_NUM}")
_CENSORED = re.compile(rf"([<>])\s*({_NUM})")
_MEAN_SD = re.compile(rf"({_NUM})\s*\(\s*({_NUM})\s*\)")


def classify(v: object, what: str) -> tuple[str, dict]:
    """(kind, info): kind in blank / num / mean_sd / censored / bdl / nm / malformed. Any other text raises."""
    if is_blank(v):
        return "blank", {}
    if is_num(v):
        x = float(v)  # type: ignore[arg-type]
        if x != x or x in (float("inf"), float("-inf")):
            raise ContractError(f"{what}: non-finite number")
        return "num", {"value": x}
    if not isinstance(v, str):
        raise ContractError(f"{what}: unexpected cell {v!r}")
    t = norm_ws(v)
    low = t.lower()
    if _PLAIN.fullmatch(t):
        return "num", {"value": float(t), "text": True}
    m = _MEAN_SD.fullmatch(t)
    if m:
        return "mean_sd", {"value": float(m.group(1)), "sd": float(m.group(2))}
    m = _CENSORED.fullmatch(t)
    if m:
        return "censored", {"value": float(m.group(2)), "comparator": m.group(1)}
    if low in BDL_CODES:
        return "bdl", {"code": low}
    if low in NM_CODES:
        return "nm", {"code": low}
    if re.fullmatch(rf"{_NUM}\s*\(.*|{_NUM}\s*\({_NUM}\)\*", t):
        return "malformed", {}
    raise ContractError(f"{what}: unrecognised cell {v!r}")


# ---- notes
CONVERSION_PATTERNS = {
    "hourly rates multiplied (to 12/24 h or daylength)": re.compile(r"hourly rates?\s*(\*|multiplied)", re.I),
    "daily NFR divided by 24 h (to hourly)": re.compile(r"dividing it by 24", re.I),
}
QC_PATTERN = re.compile(r"REJECTED by Quality Control", re.I)

# ---- references
_DOI = re.compile(r"10\.\d{4,9}/[^\s,;]+")
_YEAR = re.compile(r"\b(?:19|20)\d\d[a-z]?\b")


def doi_of(s: str) -> str | None:
    m = _DOI.search(s)
    return m.group(0).rstrip(".)") .lower() if m else None


def is_reference(s: str) -> bool:
    return doi_of(s) is not None or bool(re.match(r"[A-Z]", s) and _YEAR.search(_DOI.sub("", s)))


_DATE_RANGE = re.compile(r"(\d{4})/\d{1,2}(?:/\d{1,2})?-(?:(\d{4})/)?\d{1,2}(?:/\d{1,2})?")


def parse_date(v: object, what: str) -> tuple[str, int | None, int | None, str | None]:
    if isinstance(v, (dt.datetime, pd.Timestamp)):
        d = pd.Timestamp(v)
        return d.date().isoformat(), d.year, d.month, None
    if isinstance(v, str):
        t = norm_ws(v)
        m = _DATE_RANGE.fullmatch(t)
        if m:
            y2 = m.group(2)
            return t, (int(m.group(1)) if y2 is None or y2 == m.group(1) else None), None, t
    raise ContractError(f"{what}: unrecognised date {v!r}")


def read_sheets(path: Path) -> dict[str, pd.DataFrame]:
    xl = pd.ExcelFile(path)
    if xl.sheet_names != SHEETS:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names}")
    return {s: xl.parse(s, header=None, dtype=object, keep_default_na=False, na_values=[]) for s in SHEETS}  # type: ignore[arg-type]


def _coord(v: object, what: str, stats: Counter) -> float:
    if is_num(v):
        return float(v)  # type: ignore[arg-type]
    if isinstance(v, str) and _PLAIN.fullmatch(v.strip()):
        stats["coordinates_text_with_whitespace"] += 1
        return float(v.strip())
    raise ContractError(f"{what}: coordinate {v!r}")


def build_tables(raw: dict[str, pd.DataFrame], access_date: str) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    stats: Counter = Counter()
    skips: list[dict] = []
    code_counts: dict[str, Counter] = {k: Counter() for k in ("bdl", "nm", "malformed_cells", "censored", "mean_sd", "plain_text_number")}
    leftovers: dict[str, dict] = {}
    facts: list[dict] = []
    j1_cells: list[J1Cell] = []
    cell_numeric_j1 = Counter()
    samples: dict[str, dict] = {}
    methods: dict[str, dict] = {}
    ref_rows: dict[str, str] = {}
    conv_counts: Counter = Counter()
    notes_numeric = Counter()
    unused_cells: Counter = Counter()
    slot_stats: Counter = Counter()
    slot_conflicts: dict[str, set] = {}
    n_cells_in = 0

    def add_skip(sheet: str, i: int, attr: str, kind: str, v: object, ref: str, extra: dict | None = None) -> None:
        reason = {"bdl": "below_detection_limit", "nm": "not_measured", "malformed": "unparseable_value"}[kind]
        s = {"file": DATA_FILE, "sheet": sheet, "data_row": i, "attribute": attr, "reason": reason, "cell": str(v), "reference": ref}
        if extra:
            s.update(extra)
        skips.append(s)
        if kind == "malformed":
            code_counts["malformed_cells"][str(v)] += 1
        else:
            code_counts[kind][norm_ws(str(v)).lower()] += 1

    for sheet in [*RATE_SHEETS, CELL_SHEET]:
        df = raw[sheet]
        hdr = [_clean_header(h) for h in df.iloc[0]]
        is_cell = sheet == CELL_SHEET
        if is_cell:
            check_cell_headers(hdr)
            fam, unit_tail, code = "cell", "", "cs"
        else:
            if hdr != expected_headers(sheet):
                raise ContractError(f"{sheet}: unexpected headers {hdr}")
            fam, unit_tail, code = RATE_SHEETS[sheet]
        body = df.iloc[1:].reset_index(drop=True)
        ncol = 64 if is_cell else len(hdr)
        notes_col = ncol - 1
        if hdr[notes_col] != "Notes":
            raise ContractError(f"{sheet}: last column is not Notes")
        # column layout
        if is_cell:
            slot_cols = {"temp": 55, "sal": 56}
            j1_cols = {"water_nitrate": 57, "water_phosphate": 58, "water_chl_a": 60}
            unused_cols = {"Fe": 59, "C fixation rate": 61, "Net Primary Production": 62}
            rate_cols: list[tuple[str, int, str, str, str]] = []  # (attr, col, unit, taxon, rank)
            for k, (lab, rank) in enumerate(TAXA):
                c0 = 7 + 3 * k
                rate_cols += [("shao_n2fix_per_cell", c0, "fmol N cell-1 d-1", lab, rank),
                              ("shao_cellular_n_content", c0 + 1, "fmol N cell-1", lab, rank),
                              ("shao_n2fix_per_cellular_n", c0 + 2, "fmol N fmol N-1 d-1", lab, rank)]
        elif fam == "vol":
            slot_cols = {"temp": 15, "sal": 16}
            j1_cols = {"water_nitrate": 17, "water_phosphate": 18, "water_chl_a": 20}
            unused_cols = {"Fe": 19, "Net Primary Production": 21}
            rate_cols = [(f"shao_n2fix_{f}", 7 + k, unit_tail, "", "") for k, f in enumerate(FRACTIONS)]
        else:
            slot_cols = {}
            j1_cols = {"water_nitrate": 15, "water_phosphate": 16, "water_chl_a": 18}
            unused_cols = {"Fe": 17, "Near-surface Temperature": 13, "Near-surface Salinity": 14}
            rate_cols = [(f"shao_n2fix_{f}", 7 + k, unit_tail, "", "") for k, f in enumerate(FRACTIONS[:6])]
        # units come from the headers
        def header_unit(j: int) -> str:
            m = re.search(r"\(([^()]+)\)$", hdr[j])
            if not m:
                raise ContractError(f"{sheet} column {j}: no unit in header {hdr[j]!r}")
            return m.group(1)
        for attr, j, unit, *_ in rate_cols:
            if not is_cell and header_unit(j) != unit:
                raise ContractError(f"{sheet} column {j}: unit {header_unit(j)!r} != {unit!r}")
            if is_cell and header_unit(j) != unit:
                raise ContractError(f"{sheet} column {j}: unit {header_unit(j)!r} != {unit!r}")
            if unit not in ATTRIBUTE_DECL[attr].units:
                raise ContractError(f"{attr}: unit {unit!r} not declared")
        j1_unit = {}
        for attr, j in j1_cols.items():
            j1_unit[attr] = header_unit(j)
            if j1_unit[attr] not in ATTRIBUTE_DECL[attr].units:
                raise ContractError(f"{attr}: unit {j1_unit[attr]!r} not declared")
        # ---- walk rows
        ref: str | None = None
        since_ref = 0
        last_leftover: str | None = None
        for i in range(len(body)):
            row = list(body.iloc[i])
            what = f"{sheet} row {i}"
            src = norm_ws(str(row[0])) if not is_blank(row[0]) else ""
            if not is_blank(row[0]) and not isinstance(row[0], str):
                raise ContractError(f"{what}: SOURCE cell {row[0]!r} is not text")
            if src:
                if is_reference(src):
                    ref = src
                    last_leftover = None
                    stats["reference_cells"] += 1
                else:
                    d = leftovers.setdefault(src, {"sheet": sheet, "count": 0, "rows_following_before_next_reference": 0})
                    d["count"] += 1
                    last_leftover = src
            if ref is None:
                raise ContractError(f"{what}: no reference above")
            ref_rows[ref] = ref
            if last_leftover is not None:
                leftovers[last_leftover]["rows_following_before_next_reference"] += 1
            refkey = (f"doi:{doi_of(ref)}" if doi_of(ref) else f"ref:{ref}")
            lat = _coord(row[4], what + " latitude", stats)
            lon = _coord(row[5], what + " longitude", stats)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ContractError(f"{what}: coordinates out of range")
            datekey, year, month, rng = parse_date(row[3], what)
            dv = row[6]
            depth: float | None = None
            dnote: str | None = None
            if is_num(dv):
                depth = float(dv)  # type: ignore[arg-type]
            elif not is_blank(dv):
                dnote = norm_ws(str(dv))
            depth_key = "" if is_blank(dv) else norm_ws(str(dv))
            base = f"{refkey}|{lat!r}|{lon!r}|{datekey}|{depth_key}|{fam}"
            method_s = norm_ws(str(row[1])) if not is_blank(row[1]) else ""
            inc = "" if is_blank(row[2]) else norm_ws(str(row[2]))
            nv = row[notes_col]
            if is_blank(nv):
                notes = ""
            elif is_num(nv):
                notes = repr(float(nv)) if float(nv) != int(float(nv)) else str(int(float(nv)))  # type: ignore[arg-type]
                notes_numeric[sheet] += 1
            else:
                notes = str(nv).strip()
            flags_note: list[str] = []
            for name, pat in CONVERSION_PATTERNS.items():
                if pat.search(notes):
                    flags_note.append("converted_by_source")
                    conv_counts[name] += 1
            qc = bool(QC_PATTERN.search(notes))
            if qc:
                stats["rows_with_qc_rejected_note"] += 1
            # sample slots
            slot: dict[str, float | None] = {"temp": None, "sal": None}
            for k, j in slot_cols.items():
                kind, info = classify(row[j], what + f" {k}")
                if kind in ("num", "mean_sd"):
                    slot[k] = info["value"]
                elif kind != "blank":
                    slot_stats[f"{k}_cell_not_a_plain_number"] += 1
            def sample_row(key: str, taxon: str | None, rank: str | None) -> None:
                s = {"year": year, "month": month, "depth": depth, "temp": slot["temp"], "sal": slot["sal"], "taxon": taxon,
                     "rank": rank, "note": "; ".join(x for x in (
                         f"date as given: {rng}" if rng else None,
                         (f"integration depth: {depth:g} m" if (fam == "int" and depth is not None) else None),
                         (f"depth as given: {dnote}" if dnote else None)) if x) or None,
                     "lat": lat, "lon": lon, "ref": refkey}
                if fam == "int":
                    s["depth"], s["temp"], s["sal"] = None, None, None
                old = samples.setdefault(key, s)
                for k2 in ("temp", "sal"):  # rows of one sample: missing values are filled, two different values -> NULL
                    if old is s or s[k2] is None or old[k2] == s[k2]:
                        continue
                    if old[k2] is None and key not in slot_conflicts:
                        old[k2] = s[k2]
                    else:
                        slot_conflicts.setdefault(key, set()).add(k2)
                        old[k2] = None

            def method_key(attr: str, lab: str) -> str:
                mk = f"{attr}|{method_s}|{inc}|{lab}|{notes}"
                if mk not in methods:
                    methods[mk] = {
                        "source_key": SOURCE_KEY, "method_key": mk, "source_method_id": None, "attribute": attr,
                        "method_status": "matched" if method_s else "method_id_null", "technique": method_s or None,
                        "coring_method": None, "carbon_type": None, "carbon_measured_or_modeled": None,
                        "fraction_carbon_method": None, "carbonates_removed": None, "carbonate_removal_method": None,
                        "conversion_applied": None, "in_situ_ex_situ": None,
                        "light_dark": "light" if attr.endswith("_light") else ("dark" if attr.endswith("_dark") else None),
                        "incubation_type": None, "experimental_status": None,
                        "params": json.dumps({"incubation_time_h": inc or None, "sheet": sheet}, sort_keys=True),
                        "notes": notes or None,
                    }
                return mk

            def make_fact(attr: str, j: int, unit: str, info: dict, kind: str, taxon: str, skey: str, mk: str,
                          extra_flags: list[str]) -> dict:
                decl = ATTRIBUTE_DECL[attr]
                fl = ["habitat_from_scope", *extra_flags]
                comp = None
                sd = np.nan
                if kind == "mean_sd":
                    fl.append("source_mean_reported")
                    sd = info["sd"]
                if kind == "censored":
                    fl.append("inequality")
                    comp = info["comparator"]
                if info["value"] == 0:
                    fl.append("zero_value")
                label = f"{attr}[{taxon}]" if taxon else attr
                return {
                    "source_key": SOURCE_KEY, "fact_key": f"{code}#{i}:{label}", "reference_key": refkey,
                    "location_key": f"{lat!r}|{lon!r}", "sample_key": skey, "method_key": mk, "attribute": attr,
                    "measurement_type": decl.measurement_type, "subfield": next(iter(decl.subfields)),
                    "value": info["value"], "value_comparator": comp, "units_raw": unit, "sd": sd, "se": np.nan, "n": None,
                    "flags": sorted(set(fl)), "source_row_ref": f"{DATA_FILE}[{sheet}]#data_row={i}:{label}",
                }

            def tally(kind: str, info: dict) -> None:
                if kind == "censored":
                    code_counts["censored"][f"{info['comparator']}"] += 1
                elif kind == "mean_sd":
                    code_counts["mean_sd"]["mean (sd)"] += 1
                elif kind == "num" and info.get("text"):
                    code_counts["plain_text_number"]["number stored as text"] += 1

            for attr, j, unit, taxon, rank in rate_cols:
                v = row[j]
                kind, info = classify(v, what + f" col {j}")
                if kind == "blank":
                    continue
                n_cells_in += 1
                label = f"{attr}[{taxon}]" if taxon else attr
                if kind in ("bdl", "nm", "malformed"):
                    add_skip(sheet, i, label, kind, v, refkey)
                    continue
                tally(kind, info)
                skey = f"{base}|{taxon}" if taxon else base
                sample_row(skey, taxon or None, rank or None)
                fl = list(flags_note)
                if qc:
                    fl.append("qc_rejected_by_source")
                f = make_fact(attr, j, unit, info, kind, taxon, skey, method_key(attr, taxon), fl)
                if attr == "shao_cellular_n_content":
                    f["measurement_type"], f["subfield"] = "other", None
                facts.append(f)
            for attr, j in j1_cols.items():
                v = row[j]
                kind, info = classify(v, what + f" col {j}")
                if kind == "blank":
                    continue
                n_cells_in += 1
                if kind in ("bdl", "nm", "malformed"):
                    add_skip(sheet, i, attr, kind, v, refkey)
                    continue
                tally(kind, info)
                cell_numeric_j1[attr] += 1
                sample_row(base, None, None)
                j1_cells.append(J1Cell(len(j1_cells), base, attr, info["value"],
                                       f"{DATA_FILE}[{sheet}]#data_row={i}:{attr}", (kind, info, sheet, i, code, refkey, lat, lon, j1_unit[attr]),
                                       (kind, info.get("comparator"), info.get("sd"))))
            for name, j in unused_cols.items():
                if not is_blank(row[j]):
                    unused_cells[f"{sheet}|{name}"] += 1
            if is_cell and not is_blank(row[64] if len(row) > 64 else ""):
                unused_cells[f"{sheet}|(unnamed column 64)"] += 1

    # ---- J1 facts through the collapse
    j1_facts, j1_counts = collapse_j1(j1_cells)
    for jf in j1_facts:
        c = jf.cell
        kind, info, sheet, i, code, refkey, lat, lon, unit = c.payload
        decl = ATTRIBUTE_DECL[c.attribute]
        fl = ["habitat_from_scope"]
        comp = None
        sd = np.nan
        if kind == "mean_sd":
            fl.append("source_mean_reported")
            sd = info["sd"]
        if kind == "censored":
            fl.append("inequality")
            comp = info["comparator"]
        if c.value == 0:
            fl.append("zero_value")
        if jf.covariate_conflict:
            fl.append("covariate_conflict")
        mkey = c.attribute
        if mkey not in methods:
            methods[mkey] = {
                "source_key": SOURCE_KEY, "method_key": mkey, "source_method_id": None, "attribute": c.attribute,
                "method_status": "method_id_null", "technique": None, "coring_method": None, "carbon_type": None,
                "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
                "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": None, "light_dark": None,
                "incubation_type": None, "experimental_status": None, "params": json.dumps({}), "notes": None}
        facts.append({
            "source_key": SOURCE_KEY, "fact_key": f"{code}#{i}:{c.attribute}", "reference_key": refkey,
            "location_key": f"{lat!r}|{lon!r}", "sample_key": c.sample_key, "method_key": mkey, "attribute": c.attribute,
            "measurement_type": decl.measurement_type, "subfield": next(iter(decl.subfields)), "value": c.value,
            "value_comparator": comp, "units_raw": unit, "sd": sd, "se": np.nan, "n": None,
            "flags": sorted(fl), "source_row_ref": c.ref,
        })
    fact = pd.DataFrame(facts)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    if fact.fact_key.duplicated().any():
        raise ContractError("duplicate fact_key")
    # ---- accounting
    n_skipped = len(skips)
    n_coll = j1_counts["collapsed_repeats"]
    if len(fact) + n_skipped + n_coll != n_cells_in:
        raise ContractError(f"cells {n_cells_in} != facts {len(fact)} + skipped {n_skipped} + collapsed {n_coll}")
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")
    # ---- samples
    used_s = sorted(set(fact.sample_key))
    s_rows = [samples[k] for k in used_s]
    sample = pd.DataFrame({
        "source_key": SOURCE_KEY, "sample_key": used_s, "source_sample_id": None, "depth_min_cm": np.nan, "depth_max_cm": np.nan,
        "representative_depth_min_cm": np.nan, "representative_depth_max_cm": np.nan, "depth_notes": [s["note"] for s in s_rows],
        "sampling_year": pd.array([s["year"] for s in s_rows], dtype="Int64"),
        "sampling_month": pd.array([s["month"] for s in s_rows], dtype="Int64"),
        "taxon": [s["taxon"] for s in s_rows], "taxon_rank": [s["rank"] for s in s_rows], "vegetated_or_bare": None,
        "marsh_zone": None, "sediment_type": None, "photosynthetic_sediment": None,
        "sample_depth_m": [np.nan if s["depth"] is None else s["depth"] for s in s_rows],
        "water_temperature_c": [np.nan if s["temp"] is None else s["temp"] for s in s_rows],
        "water_salinity": [np.nan if s["sal"] is None else s["sal"] for s in s_rows],
        "water_salinity_units": ["PSU" if s["sal"] is not None else None for s in s_rows],
        "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
    })
    lkeys = sorted(set(fact.location_key))
    nl = len(lkeys)
    ll = {f"{s['lat']!r}|{s['lon']!r}": (s["lat"], s["lon"]) for s in s_rows}
    loc = pd.DataFrame({
        "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": None, "source_site_id": None, "source_core_id": lkeys,
        "latitude": [ll[k][0] for k in lkeys], "longitude": [ll[k][1] for k in lkeys], "position_accuracy": np.nan,
        "position_accuracy_units": None, "position_method": None, "country": None, "admin_division": None, "water_body": None,
        "habitat_type": "water column", "geomorphic_setting": None, "bioregion": None, "marine_ecoregion": None,
        "salinity_class": None, "salinity_class_level": None, "vegetation_class": None, "vegetation_class_level": None,
        "tidal_position": None, "tidal_position_level": None, "modification_status": [[] for _ in range(nl)],
        "dominant_species": [[] for _ in range(nl)], "elevation": np.nan, "elevation_units": None, "elevation_datum": None,
        "elevation_method": None, "water_depth_m": np.nan, "distance_to_shore_km": np.nan,
    })
    # ---- references
    ref_list = raw["Reference List"]
    if [norm_ws(str(h)) for h in ref_list.iloc[0]] != ["Reference"]:
        raise ContractError("Reference List header")
    entries = [norm_ws(str(v)) for v in ref_list.iloc[1:, 0] if not is_blank(v)]
    by_doi = {}
    for e in entries:
        d = doi_of(e)
        if d:
            by_doi.setdefault(d, e)
    used_refs = sorted(set(fact.reference_key))
    ref_text = {}
    for r in ref_rows:
        ref_text[f"doi:{doi_of(r)}" if doi_of(r) else f"ref:{r}"] = r
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": used_refs})
    cit_title, cit_year, cit_doi, cit_bib = [], [], [], []
    for k in used_refs:
        t = ref_text[k]
        d = doi_of(t)
        ys = _YEAR.findall(_DOI.sub("", t))
        cit_bib.append(t)
        cit_doi.append(d)
        cit_year.append(int(re.sub(r"[a-z]", "", ys[0])) if ys else None)
        cit_title.append(by_doi.get(d) if d else None)
    citation = pd.DataFrame({
        "source_key": SOURCE_KEY, "citation_key": [f"{k}|0" for k in used_refs], "reference_key": used_refs,
        "bibliography_id": cit_bib, "publication_type": None, "doi": cit_doi, "title": cit_title, "authors": None,
        "year": pd.array(cit_year, dtype="Int64"), "url": None, "openalex_id": None, "doi_resolved": None,
        "doi_resolution_source": None,
    })
    method = pd.DataFrame(sorted(methods.values(), key=lambda m: m["method_key"]))
    used_m = set(fact.method_key)
    method = method[method.method_key.isin(used_m)].reset_index(drop=True)
    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + " Version and license are from the figshare record.",
    }])
    tables = {"source": source, "reference": reference, "citation": citation, "study_screening": empty_table("study_screening"),
              "location": loc, "sample": sample, "method": method, "fact": fact}
    for what, a, b in (("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference.reference_key),
                       ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key)):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    flag_counts: dict[str, int] = {}
    for fl in fact["flags"]:
        for x in fl:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    report = {
        "source_rows_by_sheet": {s: len(raw[s]) - 1 for s in [*RATE_SHEETS, CELL_SHEET]},
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {f"{t}|{s}": int(c) for (t, s), c in fact.fillna({"subfield": ""}).groupby(["measurement_type", "subfield"]).size().items()},
        "facts_by_sheet": fact.fact_key.str.split("#").str[0].value_counts().sort_index().to_dict(),
        "cells_in": n_cells_in,
        "j1_collapse": j1_counts,
        "skipped": n_skipped,
        "skipped_by_reason": dict(Counter(s["reason"] for s in skips)),
        "skipped_by_attribute_reason": {f"{a}|{r}": c for (a, r), c in sorted(Counter((s["attribute"].split("[")[0], s["reason"]) for s in skips).items())},
        "codes_skipped_below_detection_limit": dict(code_counts["bdl"].most_common()),
        "codes_skipped_not_measured": dict(code_counts["nm"].most_common()),
        "malformed_mean_sd_cells_skipped": dict(code_counts["malformed_cells"].most_common()),
        "mean_sd_facts_cells": code_counts["mean_sd"]["mean (sd)"],
        "censored_cells_by_sign": dict(code_counts["censored"]),
        "numbers_stored_as_text": code_counts["plain_text_number"]["number stored as text"],
        "conversion_note_rows_by_pattern": dict(conv_counts),
        "qc_rejected_rows": stats["rows_with_qc_rejected_note"],
        "notes_cells_that_are_numbers": dict(notes_numeric),
        "reference_cells": stats["reference_cells"],
        "references": len(reference),
        "citations_with_doi": int(citation.doi.notna().sum()),
        "citations_with_reference_list_title": int(citation.title.notna().sum()),
        "reference_list_entries": len(entries),
        "non_reference_strings_in_source_column": dict(sorted(leftovers.items())),
        "non_reference_strings_count": len(leftovers),
        "coordinates_text_with_whitespace": stats["coordinates_text_with_whitespace"],
        "slot_cells_not_plain_numbers": dict(slot_stats),
        "samples_with_conflicting_temperature_or_salinity_set_to_null": len(slot_conflicts),
        "samples": len(sample),
        "locations": len(loc),
        "methods": len(method),
        "methods_with_notes": int(method.notes.notna().sum()),
        "flag_counts": flag_counts,
        "not_loaded_nonblank_cells": dict(unused_cells),
        "phase2_sheet_rows": {s: len(raw[s]) - 1 for s in PHASE2_SHEETS},
        "taxa": {t: r for t, r in TAXA},
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
    raw = read_sheets(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(raw, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True, default=str) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str)[:6000])


if __name__ == "__main__":
    main()
