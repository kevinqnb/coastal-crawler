#!/usr/bin/env python3
"""Chang et al. global database of actual nitrogen loss rates in coastal and marine sediments -> compilation-contract tables.

Chang, Y., Tan, E., Gao, D., Liu, C., Zhang, Z., Huang, Z., et al. (2024): Global database of actual nitrogen loss rates in coastal
and marine sediments. figshare, doi:10.6084/m9.figshare.27745770.v3 (paper: Earth System Science Data 17, 3521, 2025). Raw files (this
directory): ``Upload_data.xlsx`` (sheets ``Raw_data`` and ``Experiment information``, 473 rows each, aligned by position) and
``Raw_data references.docx`` (69 numbered references).

Grain: one fact per non-blank numeric cell of the nine value columns below, in the unit of the column header (thin and no-break spaces
normalised, asserted). Nothing is converted or capped.

Column -> attribute (type/subfield, unit):
  Denitrification (umol N m-2 h-1)   chang_denitrification        C4 denitrification
  Anammox (umol N m-2 h-1)           chang_anammox                C4 anammox
  P14 (umol N m-2 h-1)               chang_total_n2_production    C4 total_n2_production (extension). P14 equals
                                     denitrification + anammox to rounding wherever both exist (asserted)
  Organic carbon (%)                 chang_organic_carbon         C1 organic_carbon
  C/N (mol:mol)                      chang_c_n_ratio              C1 c_n_ratio
  Oxygen penetration depth (mm)      chang_o2_penetration_depth   C2 o2_penetration_depth
  Nitrate (uM)                       water_nitrate                J1 nitrate
  Ammonium (uM)                      water_ammonium               J1 ammonium
  Dissolved oxygen (uM)              water_o2                     J1 dissolved_oxygen
Not loaded: ``ra (%)`` (the anammox share, derived by the authors and not always equal to Anammox / P14) and ``Process`` (a label for
whether anammox was measured).

Water chemistry: the paper calls nitrate, ammonium and dissolved oxygen "water physicochemical factors" without naming the layer, so
they are J1 water concentrations. Oxygen is J1 ``water_o2``, not the ``bottom_water_o2`` slot, because the source does not say bottom
water. Temperature (sample.water_temperature_c) and salinity (sample.water_salinity, ``psu`` from the header) are sample slots. Water
depth is the depth of water over the sediment, so it is ``location.water_depth_m``. One sample per source row, so each J1 value is one
fact per sample and attribute.

Missing codes (the paper: "NM represents parameters that were not measured, and empty or NA indicates data not available"): blank gives
no value. ``na`` and ``n/a`` give NULL (counted in the report, no fact, no skip). ``nm`` in a fact-bearing column is a skip with reason
``not_measured`` (in a slot column it would be NULL; none occur). Any other non-numeric string raises.

Locations: location_key = reference no | Locations | Site | lat | lon | water depth. site_name is Locations and source_site_id is Site.
No habitat is given or stated for the dataset, so habitat_type is NULL with no flag, and the label map declares the habitat column with
an empty-label row only. Rows without coordinates get ``coords_missing`` (latitude and longitude are both blank or both present,
asserted).

Samples: one per source row (keyed row<n>). sampling_year is Year (blank gives NULL and ``year_missing``) and sampling_month is Month_1
(the Month names are asserted to agree).

Methods: the ``Experiment information`` row aligned to each data row (reference, location and site asserted equal). For the three rate
attributes, one method row per distinct (attribute, 15NO3 addition concentration, incubation environment, incubation temperature,
incubation time, termination, rate calculation). technique is the rate calculation string, light_dark is ``dark`` where the incubation
environment says so, and all six strings go to ``params``. The paper says every rate is from a dark intact-core incubation, but the file
does not say so per row, so incubation_type is NULL. Covariate attributes (organic carbon, C/N, O2 penetration, J1) get one
method_id_null row each.

References: ``References No.`` 1 to 69 is the study id, ``reference_key`` = ``ref_no:<n>``. The docx supplies the text (title is the
whole entry, and the year is parsed from the ``, YYYY.`` ending the journal part). There are no DOIs in the file.

Flags (nothing is changed or dropped): coords_missing, year_missing, zero_value, duplicate_source_row (rows identical in all columns of
both sheets).

Provenance: ``source_row_ref`` = ``Upload_data.xlsx[Raw_data]#data_row=<0-based row>:<attribute>``.

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "chang-denitrification"
SOURCE_NAME = "Chang et al. global database of actual nitrogen loss rates in coastal and marine sediments"
SOURCE_DOI = "10.6084/m9.figshare.27745770.v3"
SOURCE_VERSION = "v3 (2024-11-15)"  # figshare record, read by the integration plan; the file has no version
SOURCE_LICENSE = "CC BY 4.0"  # figshare record, as above
DATA_FILE = "Upload_data.xlsx"
REFS_FILE = "Raw_data references.docx"
RAW_SHEET, EXP_SHEET = "Raw_data", "Experiment information"
CITATION = (
    "Chang, Y., Tan, E., Gao, D., Liu, C., Zhang, Z., Huang, Z., et al. (2024): Global database of actual nitrogen "
    "loss rates in coastal and marine sediments [Dataset]. figshare, https://doi.org/10.6084/m9.figshare.27745770.v3 "
    "(paper: Earth System Science Data 17, 3521, 2025)"
)
ALLOWED_SKIP_REASONS = frozenset({"not_measured"})
check_skip_reasons(ALLOWED_SKIP_REASONS)

RATE_UNIT = "μmol N m−2 h−1"  # header spelling (U+03BC, U+2212) with thin / no-break spaces as plain spaces
RAW_COLUMNS = [
    "References No.", "Locations", "Site", "Year", "Month", "Month_1", "Longitude (°)", "Latitude (°)",
    f"Denitrification ({RATE_UNIT})", f"Anammox ({RATE_UNIT})", "Process", f"P14 ({RATE_UNIT})", "ra (%)",
    "Organic carbon (%)", "Oxygen penetration depth (mm)", "C/N (mol:mol)", "Water depth (m)", "Temperature (℃)",
    "Salinity (psu)", "Dissolved oxygen (μM)", "Nitrate (μM)", "Ammonium (μM)",
]
EXP_COLUMNS = [
    "References No.", "Locations", "Site", "15NO3– addition final concentration (μM)", "Incubation environment",
    "Incubation temperature (°C)", "Incubation time (h)", "Termination", "Rate calculation",
]
EXP_PARAM_NAMES = ["no3_addition_final_concentration_uM", "incubation_environment", "incubation_temperature",
                   "incubation_time_h", "termination", "rate_calculation"]
# header -> (attribute, units_raw)
FACT_COLUMNS: dict[str, tuple[str, str]] = {
    f"Denitrification ({RATE_UNIT})": ("chang_denitrification", RATE_UNIT),
    f"Anammox ({RATE_UNIT})": ("chang_anammox", RATE_UNIT),
    f"P14 ({RATE_UNIT})": ("chang_total_n2_production", RATE_UNIT),
    "Organic carbon (%)": ("chang_organic_carbon", "%"),
    "C/N (mol:mol)": ("chang_c_n_ratio", "mol:mol"),
    "Oxygen penetration depth (mm)": ("chang_o2_penetration_depth", "mm"),
    "Nitrate (μM)": ("water_nitrate", "μM"),
    "Ammonium (μM)": ("water_ammonium", "μM"),
    "Dissolved oxygen (μM)": ("water_o2", "μM"),
}
RATE_ATTRS = frozenset({"chang_denitrification", "chang_anammox", "chang_total_n2_production"})
SLOT_COLUMNS = ["Water depth (m)", "Temperature (℃)", "Salinity (psu)"]
NA_CODES = frozenset({"na", "n/a"})
NM_CODE = "nm"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"])}
SUM_TOL = 0.05  # P14 vs denitrification + anammox, absolute, umol N m-2 h-1 (observed max 0.042)
N_REFS = 69
_REF_ENTRY = re.compile(r"(\d+)\.\s*(.+)", re.DOTALL)
_REF_YEAR = re.compile(r",\s*((?:19|20)\d{2})\.")


def norm_header(s: object) -> str:
    if not isinstance(s, str):
        raise ContractError(f"header {s!r} is not text")
    return re.sub(r"\s+", " ", s.replace(" ", " ").replace("\xa0", " ")).strip()


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ").replace(" ", " ")).strip()


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v.strip() == "")


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)


def _number(v: object, what: str) -> float:
    if not _is_num(v) or not math.isfinite(float(v)):  # type: ignore[arg-type]
        raise ContractError(f"{what}: non-numeric value {v!r}")
    return float(v)  # type: ignore[arg-type]


def _text(v: object) -> str | None:
    """Blank -> None; numbers become their plain text (a Site or a setting written as a number)."""
    if _blank(v):
        return None
    if isinstance(v, str):
        return norm_ws(v)
    if _is_num(v):
        f = float(v)  # type: ignore[arg-type]
        return str(int(f)) if f == int(f) else repr(f)
    raise ContractError(f"unexpected cell {v!r}")


def classify(v: object, what: str) -> tuple[str, float | None]:
    """('blank'|'na'|'nm'|'num', value). Any other string raises."""
    if _blank(v):
        return "blank", None
    if _is_num(v):
        return "num", _number(v, what)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in NA_CODES:
            return "na", None
        if t == NM_CODE:
            return "nm", None
    raise ContractError(f"{what}: unrecognised non-numeric value {v!r}")


# ---------------------------------------------------------------- raw reading


def read_raw(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    xl = pd.ExcelFile(path)
    if xl.sheet_names != [RAW_SHEET, EXP_SHEET]:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names}")
    kw = dict(dtype=object, keep_default_na=False, na_values=[])
    return xl.parse(RAW_SHEET, **kw), xl.parse(EXP_SHEET, **kw)  # type: ignore[arg-type]


def read_references(path: Path) -> dict[int, str]:
    """Numbered entries of the references docx (paragraph text of word/document.xml)."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paras = ["".join(t.text or "" for t in p.iter(f"{ns}t")) for p in root.iter(f"{ns}p")]
    paras = [norm_ws(p) for p in paras if p.strip()]
    out: dict[int, str] = {}
    for p in paras:
        m = _REF_ENTRY.fullmatch(p)
        if not m:
            if p == "The references listed in the database.":
                continue
            raise ContractError(f"{path.name}: paragraph is neither the title nor a numbered entry: {p[:60]!r}")
        k = int(m.group(1))
        if k in out:
            raise ContractError(f"{path.name}: reference number {k} twice")
        out[k] = m.group(2)
    if sorted(out) != list(range(1, N_REFS + 1)):
        raise ContractError(f"{path.name}: reference numbers are not 1..{N_REFS}")
    return out


# ---------------------------------------------------------------- main build


def build_tables(
    raw: pd.DataFrame, exp: pd.DataFrame, refs: dict[int, str], access_date: str
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if [norm_header(c) for c in raw.columns] != RAW_COLUMNS:
        raise ContractError(f"{RAW_SHEET}: unexpected columns {[norm_header(c) for c in raw.columns]}")
    if [norm_header(c) for c in exp.columns] != EXP_COLUMNS:
        raise ContractError(f"{EXP_SHEET}: unexpected columns {[norm_header(c) for c in exp.columns]}")
    raw = raw.set_axis(RAW_COLUMNS, axis=1)
    exp = exp.set_axis(EXP_COLUMNS, axis=1)
    if len(raw) != len(exp):
        raise ContractError(f"{RAW_SHEET} has {len(raw)} rows but {EXP_SHEET} has {len(exp)}")
    n_rows = len(raw)
    for k in ("References No.", "Locations", "Site"):
        a = [_text(v) for v in raw[k]]
        b = [_text(v) for v in exp[k]]
        if a != b:
            raise ContractError(f"{RAW_SHEET} and {EXP_SHEET} are not aligned on {k!r}")
    if not (len(raw["Process"]) == n_rows):
        raise ContractError("row count")

    ref_no = [int(_number(v, "References No.")) for v in raw["References No."]]
    if set(ref_no) != set(refs):
        raise ContractError(f"reference numbers in the data {sorted(set(ref_no) ^ set(refs))} differ from the docx")
    loc_name = [_text(v) for v in raw["Locations"]]
    if any(x is None for x in loc_name):
        raise ContractError("blank Locations")
    site = [_text(v) for v in raw["Site"]]
    dup = pd.concat([raw, exp.iloc[:, 3:]], axis=1).astype(str).duplicated(keep=False).to_numpy()

    year = [None if _blank(v) else int(_number(v, "Year")) for v in raw["Year"]]
    month = []
    for a, b in zip(raw["Month"], raw["Month_1"]):
        if _blank(a) != _blank(b):
            raise ContractError(f"Month {a!r} and Month_1 {b!r}: one is blank")
        if _blank(a):
            month.append(None)
        else:
            if MONTHS.get(str(a).strip()) != int(_number(b, "Month_1")):
                raise ContractError(f"Month {a!r} disagrees with Month_1 {b!r}")
            month.append(int(_number(b, "Month_1")))
    lat = [None if _blank(v) else _number(v, "Latitude") for v in raw["Latitude (°)"]]
    lon = [None if _blank(v) else _number(v, "Longitude") for v in raw["Longitude (°)"]]
    if any((a is None) != (b is None) for a, b in zip(lat, lon)):
        raise ContractError("latitude and longitude are not blank together")
    if any(a is not None and not (-90 <= a <= 90) for a in lat) or any(
        b is not None and not (-180 <= b <= 180) for b in lon
    ):
        raise ContractError("coordinates outside the valid range")

    def slot(col: str) -> list[float | None]:
        out = []
        for v in raw[col]:
            kind, x = classify(v, col)
            if kind == "num":
                out.append(x)
            elif kind in ("blank", "na", "nm"):
                out.append(None)
        return out

    depth, temp, sal = (slot(c) for c in SLOT_COLUMNS)
    if any(d is not None and d < 0 for d in depth):
        raise ContractError("negative water depth")

    # ---- cells: numeric -> fact; nm -> skip; na / blank -> nothing
    cells: dict[str, list[tuple[str, float | None]]] = {}
    coded = {"na": {}, "nm": {}}
    for col, (attr, _u) in FACT_COLUMNS.items():
        got = [classify(v, col) for v in raw[col]]
        cells[col] = got
        for kind in ("na", "nm"):
            coded[kind][col] = sum(1 for k, _ in got if k == kind)
    # P14 is total N2 production: equals denitrification + anammox on every row that has both
    D, A, P = (cells[c] for c in list(FACT_COLUMNS)[:3])
    n_both = 0
    for i in range(n_rows):
        if D[i][0] == "num" and A[i][0] == "num" and P[i][0] == "num":
            n_both += 1
            if abs(P[i][1] - (D[i][1] + A[i][1])) > SUM_TOL:  # type: ignore[operator]
                raise ContractError(f"row {i}: P14 differs from denitrification + anammox by more than {SUM_TOL}")
    if any(P[i][0] != "num" for i in range(n_rows)):
        raise ContractError("a row without P14")
    proc = [_text(v) for v in raw["Process"]]
    for i in range(n_rows):  # Process says whether anammox was measured
        if (proc[i] == "Denitrification and anammox") != (A[i][0] == "num"):
            raise ContractError(f"row {i}: Process {proc[i]!r} and the Anammox cell disagree")

    # ---- locations
    def f(x: float | None) -> str:
        return "" if x is None else repr(float(x))

    loc_key = [
        f"{ref_no[i]}|{loc_name[i]}|{site[i] or ''}|{f(lat[i])}|{f(lon[i])}|{f(depth[i])}" for i in range(n_rows)
    ]
    first = {}
    for i, k in enumerate(loc_key):
        first.setdefault(k, i)
    keys = sorted(first)
    n = len(keys)
    idx = [first[k] for k in keys]
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "location_key": keys,
            "site_name": [loc_name[i] for i in idx],
            "source_site_id": [site[i] for i in idx],
            "source_core_id": keys,
            "latitude": [np.nan if lat[i] is None else lat[i] for i in idx],
            "longitude": [np.nan if lon[i] is None else lon[i] for i in idx],
            "position_accuracy": np.nan,
            "position_accuracy_units": None,
            "position_method": None,
            "country": None,
            "admin_division": None,
            "water_body": None,
            "habitat_type": None,
            "geomorphic_setting": None,
            "bioregion": None,
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
            "water_depth_m": [np.nan if depth[i] is None else depth[i] for i in idx],
            "distance_to_shore_km": np.nan,
        }
    )

    # ---- samples (one per source row)
    sample_key = [f"row{i}" for i in range(n_rows)]
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": sample_key,
            "source_sample_id": None,
            "depth_min_cm": np.nan,
            "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": "intact sediment core incubation; no sediment depth interval",
            "sampling_year": pd.array(year, dtype="Int64"),
            "sampling_month": pd.array(month, dtype="Int64"),
            "taxon": None,
            "taxon_rank": None,
            "vegetated_or_bare": None,
            "marsh_zone": None,
            "sediment_type": None,
            "photosynthetic_sediment": None,
            "sample_depth_m": np.nan,
            "water_temperature_c": [np.nan if t is None else t for t in temp],
            "water_salinity": [np.nan if s is None else s for s in sal],
            "water_salinity_units": ["psu" if s is not None else None for s in sal],
            "bottom_water_o2": np.nan,
            "bottom_water_o2_units": None,
        }
    )

    # ---- methods
    exp_strs = [[_text(exp.iat[i, j]) for j in range(3, 9)] for i in range(n_rows)]
    method_rows: dict[str, dict] = {}

    def method_for(attr: str, i: int) -> str:
        if attr in RATE_ATTRS:
            e = exp_strs[i]
            key = attr + "|" + "|".join(x or "" for x in e)
            if key not in method_rows:
                env, rate = e[1], e[5]
                method_rows[key] = {
                    "source_key": SOURCE_KEY, "method_key": key, "source_method_id": None, "attribute": attr,
                    "method_status": "matched" if rate else "method_id_null",
                    "technique": rate, "coring_method": None, "carbon_type": None,
                    "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
                    "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": None,
                    "light_dark": "dark" if env == "dark" else None, "incubation_type": None,
                    "experimental_status": None,
                    "params": json.dumps(dict(zip(EXP_PARAM_NAMES, e)), sort_keys=True),
                }
            return key
        if attr not in method_rows:
            method_rows[attr] = {
                "source_key": SOURCE_KEY, "method_key": attr, "source_method_id": None, "attribute": attr,
                "method_status": "method_id_null", "technique": None, "coring_method": None, "carbon_type": None,
                "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
                "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": None,
                "light_dark": None, "incubation_type": None, "experimental_status": None, "params": json.dumps({}),
            }
        return attr

    # ---- facts and skips
    rows, skips = [], []
    n_numeric = {a: 0 for a, _ in FACT_COLUMNS.values()}
    for col, (attr, units) in FACT_COLUMNS.items():
        decl = ATTRIBUTE_DECL[attr]
        if units not in decl.units:
            raise ContractError(f"{attr}: unit {units!r} not declared")
        m = re.search(r"\((.+)\)$", col)
        if not m or m.group(1) != units:
            raise ContractError(f"{col}: header unit is not {units!r}")
        for i, (kind, x) in enumerate(cells[col]):
            if kind == "nm":
                skips.append({"data_row": i, "attribute": attr, "reason": "not_measured", "cell": "nm"})
                continue
            if kind != "num":
                continue
            n_numeric[attr] += 1
            flags = []
            if lat[i] is None:
                flags.append("coords_missing")
            if year[i] is None:
                flags.append("year_missing")
            if x == 0:
                flags.append("zero_value")
            if dup[i]:
                flags.append("duplicate_source_row")
            rows.append(
                {
                    "source_key": SOURCE_KEY,
                    "fact_key": f"{sample_key[i]}:{attr}",
                    "reference_key": f"ref_no:{ref_no[i]}",
                    "location_key": loc_key[i],
                    "sample_key": sample_key[i],
                    "method_key": method_for(attr, i),
                    "attribute": attr,
                    "measurement_type": decl.measurement_type,
                    "subfield": next(iter(decl.subfields)),
                    "value": float(x),  # type: ignore[arg-type]
                    "units_raw": units,
                    "sd": np.nan,
                    "se": np.nan,
                    "n": None,
                    "flags": sorted(flags),
                    "source_row_ref": f"{DATA_FILE}[{RAW_SHEET}]#data_row={i}:{attr}",
                }
            )
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    method = pd.DataFrame(sorted(method_rows.values(), key=lambda r: r["method_key"]))

    # ---- references
    used = sorted(set(ref_no))
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": [f"ref_no:{k}" for k in used]})
    cit_year = []
    for k in used:
        ys = _REF_YEAR.findall(refs[k])
        cit_year.append(int(ys[-1]) if ys else None)
    citation = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "citation_key": [f"ref_no:{k}|{k}" for k in used],
            "reference_key": [f"ref_no:{k}" for k in used],
            "bibliography_id": [str(k) for k in used],
            "publication_type": None,
            "doi": None,
            "title": [refs[k] for k in used],
            "authors": None,
            "year": pd.array(cit_year, dtype="Int64"),
            "url": None,
            "openalex_id": None,
            "doi_resolved": None,
            "doi_resolution_source": None,
        }
    )

    source = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
                "access_date": access_date, "license": SOURCE_LICENSE,
                "notes": CITATION + " Version and license are from the figshare record (the workbook states neither).",
            }
        ]
    )
    tables = {
        "source": source, "reference": reference, "citation": citation,
        "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
        "method": method, "fact": fact,
    }

    # ---- accounting: every numeric cell is a fact, every nm cell a skip, na / blank nothing
    n_cells_num = sum(1 for col in FACT_COLUMNS for k, _ in cells[col] if k == "num")
    n_cells_nm = sum(coded["nm"].values())
    if len(fact) != n_cells_num:
        raise ContractError(f"facts {len(fact)} != numeric cells {n_cells_num}")
    if len(skips) != n_cells_nm:
        raise ContractError(f"skips {len(skips)} != nm cells {n_cells_nm}")
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key),
        ("reference", fact.reference_key, reference.reference_key),
        ("method", fact.method_key, method.method_key),
        ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    flag_counts: dict[str, int] = {}
    for fl in fact["flags"]:
        for x in fl:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    report = {
        "source_rows": n_rows,
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"]).size().items()
        },
        "numeric_cells_by_column": {c: int(sum(1 for k, _ in cells[c] if k == "num")) for c in FACT_COLUMNS},
        "coded_missing_cells_na": coded["na"],
        "coded_missing_cells_nm_skipped": coded["nm"],
        "skipped": len(skips),
        "rows_with_p14_and_denitrification_and_anammox": n_both,
        "rows_without_denitrification": int(sum(1 for k, _ in D if k != "num")),
        "rows_with_anammox": int(sum(1 for k, _ in A if k == "num")),
        "samples": len(sample),
        "locations": len(loc),
        "locations_without_coordinates": int(loc.latitude.isna().sum()),
        "locations_without_depth": int(loc.water_depth_m.isna().sum()),
        "distinct_locations_strings": len(set(loc_name)),
        "methods": len(method),
        "methods_by_attribute": method.attribute.value_counts().sort_index().to_dict(),
        "references": len(reference),
        "citations": len(citation),
        "citations_without_year": int(citation.year.isna().sum()),
        "flag_counts": flag_counts,
        "rows_by_flag": {
            "no_coordinates": int(sum(1 for a in lat if a is None)),
            "no_year": int(sum(1 for y in year if y is None)),
            "no_month": int(sum(1 for m in month if m is None)),
            "duplicate": int(dup.sum()),
        },
        "rows_with_slot_values": {
            "water_depth": int(sum(d is not None for d in depth)),
            "temperature": int(sum(t is not None for t in temp)),
            "salinity": int(sum(s is not None for s in sal)),
        },
        "temperature_negative_rows": int(sum(1 for t in temp if t is not None and t < 0)),
        "water_depth_zero_rows": int(sum(1 for d in depth if d == 0)),
        "incubation_environment_values": dict(pd.Series([e[1] or "(blank)" for e in exp_strs]).value_counts()),
        "process_values": dict(pd.Series([p or "(blank)" for p in proc]).value_counts()),
        "p14_sum_tolerance": SUM_TOL,
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
    raw, exp = read_raw(args.raw_dir / DATA_FILE)
    refs = read_references(args.raw_dir / REFS_FILE)
    tables, skips, report = build_tables(raw, exp, refs, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
