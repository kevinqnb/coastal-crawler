#!/usr/bin/env python3
"""Boynton and Ceballos World Flux Data Set 2016 (v3) -> contract tables.

Boynton, W., Ceballos, M.A. (2017): World Flux Data Set 2016. Mendeley Data, V3, doi:10.17632/8gc3ys3jx6.3
(created 2016-12-16, updated 2017-05-17; Boynton et al. 2018, Estuaries and Coasts). Raw file (this directory):
``Published2016v3WorldFlux DataSet.xlsx`` (v3 only; the v1 and v2 files in the directory are superseded and
never read). Sheets used: ``WorldFluxDataSet2016v3`` (2,879 data rows, one sediment-water flux incubation per row,
unique ``ID#``) and ``DataRefs`` (the reference list). ``Table2DataVariables`` is a picture (the variable and unit
table, read by eye: element basis and "usually top 1 cm" for the sediment variables); the other sheets are
descriptive.

Grain: ONE FACT PER NON-BLANK NUMERIC CELL of the value columns below, except the J1 water concentrations, which
belong to the SAMPLE and are collapsed (see below). Values are carried exactly as reported; units are the
header's own spelling (Table 2 gives the element basis: O2 / N / P / DSi; it is not written into ``units_raw``).
Nothing is converted.

Column -> attribute (type/subfield, header unit):
  O2 or SOC            boynton_soc               B5 total_o2_uptake      umol/m2/hr  (NEGATIVE = into the sediment)
  NH4 / PO4 / NO2 / NO3 / NO23 / DSi Flux
                       boynton_{nh4,po4,no2,no3,no23,dsi}_flux   C3 ammonium/phosphate/nitrite/nitrate/
                       nitrate_nitrite/silicate flux             umol/m2/hr
  RES                  boynton_water_respiration B9 community_respiration  gO2/m2/day (water-column respiration)
  SedCHLa              boynton_sediment_chl_a    B1 sediment_chlorophyll_a mg/m2
  POC(%DW)             boynton_poc               C1 organic_carbon        %DW   (Table 2 says %DW; the plan's
                       "mg C m-2" is not in the file)
  PON(%DW), PP(%DW)    boynton_pon, boynton_pp   other (particulate organic N / P; not total N)   %DW
  pH, NH4, PO4, NO3, NO23, DSi (bottom water)
                       water_ph (unitless, Table 2), water_ammonium, water_phosphate, water_nitrate,
                       water_nitrate_nitrite, water_silicate (uM)  -> J1
Slots (sample / location, Table 2 "Bottom water ..."): Salinity (unitless), Temp (degC), DO(uM) -> sample
``water_salinity`` / ``water_temperature_c`` / ``bottom_water_o2``; Water Depth (m) -> ``location.water_depth_m``
(as Chang, SCOC; ``sample_depth_m`` is a water-column sample depth).
Not loaded: ``Temp. Range`` (annual range text), ``Wet/Dry/Normal`` and ``SEASON`` beyond the sample key (raw text in
the method params / key), ``Date`` as a value (see Dates).

Missing codes: ``ND`` (not determined) -> NULL; ``OSN`` ("other reason, see notes") in a value column -> NULL, counted
in the report (no fact to carry a flag; see the README). Blank = NULL. Any other string in a numeric column raises.

SIGN (not normalised): the source's Edit Notes state the compilers' convention, negative = into the sediment
("in paper positive fluxes were into sediment, but data was entered in our method (negative into sediment)";
"SOC plotted as positive ... We changed flux sign to negative"; "All SOC made negative"). Table 2 states none.
SOC is asserted <= 0 on every row.

J1 and the sample grain. The plan's sample key (reference, station, lat, lon, Date) cannot be stored: within it the
rows can differ in salinity, temperature and DO (which sit on the sample row) and in Month / Season. So the sample key
here is the plan's key PLUS every value written onto the sample row (raw Year, Month, Season, Salinity, Temp, DO).
Each J1 attribute gets one fact per (sample, distinct value): repeats of one value collapse into the first row's fact
(``collapsed`` in the report and in the row accounting), and a sample with two different values of one J1
attribute keeps both with flag ``covariate_conflict``. The report gives J1 counts under both keys.

Locations: ``location_key`` = REGION | Estuary Name | Station Name | lat | lon | water depth | state/area | country |
general characteristics (every field stored). ``site_name`` = Estuary Name, ``source_site_id`` = Station Name,
``bioregion`` = REGION (the source's continent / world region label), ``geomorphic_setting`` = general
characteristics (raw, unmapped: D3), ``admin_division`` = state/area, country. Habitat is NULL, unflagged (D3): the
label map declares the habitat column with one empty-label row.

Dates (one rule): ``sampling_year`` = the ``Year`` column when it is a number, else NULL with flag ``year_missing``
(ranges such as 1979-1981 and ``OSN, averaged several yrs`` are not a year); ``sampling_month`` = the ``Month``
column when it is one month name, else NULL. The ``Date`` column is not loaded; it disagrees with Year on 8 rows
and with Month on 16 (counted in the report; Month, Season and Year agree with each other on the Jorgensen and
Sorensen rows where Date is off).

Methods: for the seven rate attributes (SOC and the six fluxes) one method row per distinct (attribute, technique
and the incubation fields of the row); the other attributes carry ``method_id_null`` rows. ``in_situ_ex_situ`` by
the written rule: ``Method/measurement technique`` (lower-cased) starts with ``in situ`` -> ``in situ``; with
``lab`` or ``shipboard`` -> ``ex situ``; anything else raises. ``light_dark`` from ``Photic/Aphotic`` (the incubation
condition, Table 2): ``Aphotic`` -> ``dark``, ``Photic`` -> ``light``, ``Aphotic&Photic`` -> ``dark and light``,
anything else NULL (raw string in params). The raw technique string is ``technique``. Params: measurement
frequency, incubation hours, stirring, flow-through or batch, photic text, light-reaches-bottom, core diameter,
length, sediment height, volume, area, wet/dry/normal, both ``pub or us`` columns, Edit Notes (verbatim; they
document source errata). ``ND`` is stored as null.

Flags: zero_value (any value 0), negative_value (only for quantities that cannot be negative: not SOC or the
fluxes), year_missing, source_site_averaged (``sites averaged`` = Y), source_time_averaged (``time averaged`` = Y),
covariate_conflict (J1). No row repeats another once ``ID#`` is ignored (asserted), so no duplicate flag.

References: ``Reference`` strings -> ``ref:<string>``; full text and any DOI from ``DataRefs`` by CROSSWALK below
(unique first-author surname + first year, a second-author check for "A and B" strings, and explicit entries for every
ambiguous or misspelt string; strings with no entry get a text-only citation and are listed in the report).

Provenance: ``source_row_ref`` = ``<file>[WorldFluxDataSet2016v3]#data_row=<0-based row>:<attribute>`` (for a J1
fact, the first row of the sample that carries the value).

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from _g3_common import (  # noqa: E402
    blank, classify, collapse_covariates, count_flags, is_num, method_row, norm_ws, number, text,
)
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "boynton-flux"
SOURCE_NAME = "World Flux Data Set 2016 (Boynton and Ceballos)"
SOURCE_DOI = "10.17632/8gc3ys3jx6.3"
SOURCE_VERSION = "Mendeley Data v3 (2017-07-21)"  # read by the integration plan from the Mendeley page
SOURCE_LICENSE = "CC BY 4.0"  # Mendeley page, as above
DATA_FILE = "Published2016v3WorldFlux DataSet.xlsx"
DATA_SHEET, REFS_SHEET = "WorldFluxDataSet2016v3", "DataRefs"
CITATION = (
    "Boynton, W., Ceballos, M.A. (2017): World Flux Data Set 2016 [Dataset]. Mendeley Data, V3, "
    "https://doi.org/10.17632/8gc3ys3jx6.3 (created 2016-12-16, updated 2017-05-17; Boynton et al. 2018, "
    "Estuaries and Coasts)"
)
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()
check_skip_reasons(ALLOWED_SKIP_REASONS)

EXPECTED_COLUMNS = [
    "ID#", "REGION", "Estuary Name", "general characteristics", "Reference", "state/area", "country",
    "Wet/Dry/Normal", "Photic/Aphotic", "Light reaches bottom? (y/n)", "Temp. Range (°C)",
    "Method/measurement technique", "measurement frequency", "incubation duration (hrs)", "stirring",
    "Flowthrough or Batch", "core/chamber diameter (cm)", "total core length (cm)", "sediment height (cm)",
    "volume (cm3)", "area (cm2)", "Station Name", "Latitude", "Longitude", "SEASON", "Date", "Month", "Year",
    "Water Depth (m)", "Salinity", "Temp (°C)", "pH", "NH4(uM)", "PO4(uM)", "NO3(uM)", "NO23(uM)", "DSi(uM)",
    "DO(uM)", "O2 or SOC (umol/m2/hr)", "NH4 Flux (umol/m2/hr)", "PO4 Flux (umol/m2/hr)",
    "NO2 Flux (umol/m2/hr)", "NO3 Flux (umol/m2/hr)", "NO23 Flux (umol/m2/hr)", "DSi Flux (umol/m2/hr)",
    "RES (gO2/m2/day)", "POC(%DW)", "PON(%DW)", "PP(%DW)", "SedCHLa (mg/m2)", "sites averaged Y/N", "pub or us",
    "time averaged Y/N", "pub or us.1", "Edit Notes",
]
CODES = {"nd": "nd", "osn": "osn"}
FLUX_U = "umol/m2/hr"
# header -> (attribute, units_raw, can_be_negative)
RATE_COLUMNS: dict[str, tuple[str, str, bool]] = {
    "O2 or SOC (umol/m2/hr)": ("boynton_soc", FLUX_U, True),
    "NH4 Flux (umol/m2/hr)": ("boynton_nh4_flux", FLUX_U, True),
    "PO4 Flux (umol/m2/hr)": ("boynton_po4_flux", FLUX_U, True),
    "NO2 Flux (umol/m2/hr)": ("boynton_no2_flux", FLUX_U, True),
    "NO3 Flux (umol/m2/hr)": ("boynton_no3_flux", FLUX_U, True),
    "NO23 Flux (umol/m2/hr)": ("boynton_no23_flux", FLUX_U, True),
    "DSi Flux (umol/m2/hr)": ("boynton_dsi_flux", FLUX_U, True),
}
OTHER_COLUMNS: dict[str, tuple[str, str, bool]] = {
    "RES (gO2/m2/day)": ("boynton_water_respiration", "gO2/m2/day", False),
    "SedCHLa (mg/m2)": ("boynton_sediment_chl_a", "mg/m2", False),
    "POC(%DW)": ("boynton_poc", "%DW", False),
    "PON(%DW)": ("boynton_pon", "%DW", False),
    "PP(%DW)": ("boynton_pp", "%DW", False),
}
J1_COLUMNS: dict[str, tuple[str, str]] = {
    "pH": ("water_ph", "unitless"),  # no header unit; Table 2: unitless
    "NH4(uM)": ("water_ammonium", "uM"),
    "PO4(uM)": ("water_phosphate", "uM"),
    "NO3(uM)": ("water_nitrate", "uM"),
    "NO23(uM)": ("water_nitrate_nitrite", "uM"),
    "DSi(uM)": ("water_silicate", "uM"),
}
FACT_COLUMNS = list(RATE_COLUMNS) + list(OTHER_COLUMNS) + list(J1_COLUMNS)
SLOT_COLUMNS = ["Water Depth (m)", "Salinity", "Temp (°C)", "DO(uM)"]
SAMPLE_KEY_COLUMNS = ["Reference", "Station Name", "Latitude", "Longitude", "Date"]  # the plan's grain
SAMPLE_KEY_EXTRA = ["Month", "Year", "SEASON", "Salinity", "Temp (°C)", "DO(uM)"]  # everything on the sample row
PARAM_COLUMNS = {
    "measurement_frequency": "measurement frequency", "incubation_hours": "incubation duration (hrs)",
    "stirring": "stirring", "flowthrough_or_batch": "Flowthrough or Batch",
    "photic_aphotic": "Photic/Aphotic", "light_reaches_bottom": "Light reaches bottom? (y/n)",
    "core_diameter_cm": "core/chamber diameter (cm)", "core_length_cm": "total core length (cm)",
    "sediment_height_cm": "sediment height (cm)", "volume_cm3": "volume (cm3)", "area_cm2": "area (cm2)",
    "wet_dry_normal": "Wet/Dry/Normal", "sites_averaged_pub_or_us": "pub or us",
    "time_averaged_pub_or_us": "pub or us.1", "edit_notes": "Edit Notes",
}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"])}
LIGHT_DARK = {"Aphotic": "dark", "Photic": "light", "Aphotic&Photic": "dark and light"}
N_REFS_ENTRIES = 177  # DataRefs rows after the title and blank rows

# ---------------------------------------------------------------- reference crosswalk
# Composite Reference strings -> the pieces (rule 3.4: one citation per cited paper).
COMPOSITES = {
    "Tucker et al 2014, Hopkinson et al 2001, Giblin et al 1997":
        ["Tucker et al 2014", "Hopkinson et al 2001", "Giblin et al 1997"],
    "Hopkinson 1985 and 1987": ["Hopkinson 1985", "Hopkinson 1987"],
}
# Strings the unique-surname-and-year rule cannot settle: piece -> start of the DataRefs entry, or None (unresolved,
# with the reason). Decided by reading the entries, never by similarity.
CROSSWALK_OVERRIDES: dict[str, tuple[str | None, str]] = {
    "Balzar et al 1983": ("Balzer, W., Grasshofp", "misspelt Balzar; 1983 = the 5-author entry"),
    "Fergurson et al 2004": ("Ferguson, A. J. P., Eyre, B. D., & Gay", "misspelt Fergurson"),
    "Fluxzilla-Mainstem": ("Fluxilla-Mainstem", "entry spelt Fluxilla"),
    "Fluxzilla-PAX": ("Fluxzilla-PAX", "web data, not a paper"),
    "Cowan and Boynton 1996": ("Cowan, J. L. W., & Boynton", "two authors"),
    "Cowan et al 1996": ("Cowan, J. L. W., Pennock", "three authors"),
    "Smith and Teal 1973": ("Smith Jr., K. L., & Teal", "two authors"),
    "Smith et al 1973": ("Smith, K. L., Rowe", "three authors"),
    "Hartwig 1974": (None, "the only Hartwig entry is dated 1976; not assumed to be the same paper"),
    # same author and year twice: settled by the entry's title naming the rows' own system (Estuary Name column)
    "Cook et al 2004": ("Cook, P. L. M., Eyre, B. D.", "rows are Bremer River; the entry is about a sub-tropical river"),
    "Cook et al 2004a": ("Cook, P. L. M., Revill", "rows are Huon estuary sites; the entry is about a temperate estuary's mudflats"),
    "Rysgaard et al 1996": ("Rysgaard, S., Risgaard-Petersen", "rows are Bassin d'Arcachon and Etang du Prevost; the entry is about two lagoons in southern France"),
    "Rysgaard et al 1996a": ("Rysgaard, S., Finster", "rows are Young Sound; the entry is about a northeastern Greenland fjord"),
    "Enoksson and Samuellsson 1987": ("Enoksson, V., & Samuelsson", "misspelt Samuellsson"),
    "Jorgensen and Sorensen 1985": ("Jorgensen, B. B., & Sosrensen", "entry misspelt Sosrensen"),
}
_REF_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_DOI = re.compile(r"10\.\d{4,9}/[^\s]+")


def _fold(s: str) -> str:
    s = s.replace("ø", "o").replace("Ø", "O")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _entry_surname(entry: str) -> str:
    return re.sub(r"\s+jr\.?$", "", _fold(entry.split(",")[0]).strip())


def _piece_surname(piece: str) -> str:
    m = re.match(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' -]*?(?=,|\s+et al|\s+and\s|\s+\d)", piece)
    if not m:
        raise ContractError(f"cannot read a surname from the reference {piece!r}")
    return _fold(m.group(0)).strip()


def _entry_year(entry: str) -> str | None:
    m = re.search(r"[\(,]\s*\(?(1[89]\d\d|20\d\d)", entry)
    return m.group(1) if m else None


def resolve_piece(piece: str, entries: list[str]) -> tuple[str | None, str]:
    """(DataRefs entry or None, how). Raises when the unique rule fails and no override exists."""
    if piece in CROSSWALK_OVERRIDES:
        prefix, why = CROSSWALK_OVERRIDES[piece]
        if prefix is None:
            return None, why
        hit = [e for e in entries if e.startswith(prefix)]
        if len(hit) != 1:
            raise ContractError(f"override {piece!r}: prefix {prefix!r} matches {len(hit)} DataRefs entries")
        return hit[0], "override"
    ys = _REF_YEAR.findall(piece)
    if not ys:
        raise ContractError(f"reference {piece!r}: no year and no override")
    sur = _piece_surname(piece)
    cand = [e for e in entries if _entry_surname(e) == sur and _entry_year(e) == ys[0]]
    m = re.fullmatch(r"(.+?) and ([A-Za-z][A-Za-z -]*?)\s+\d{4}[a-z]?", piece)
    if m and cand:  # "A and B 1999": the second author must be in the entry's author part
        second = _fold(m.group(2)).strip()
        cand = [e for e in cand if second in _fold(e[: e.find("(")] if "(" in e else e)]
    if len(cand) == 1:
        return cand[0], "unique surname and year"
    if len(cand) == 0:
        return None, "no DataRefs entry with this first author and year"
    raise ContractError(f"reference {piece!r} matches {len(cand)} DataRefs entries and has no override")


# ---------------------------------------------------------------- raw reading


def norm_header(s: object) -> str:
    if not isinstance(s, str):
        raise ContractError(f"header {s!r} is not text")
    return norm_ws(s)


def read_raw(path: Path) -> tuple[pd.DataFrame, list[str]]:
    xl = pd.ExcelFile(path)
    if DATA_SHEET not in xl.sheet_names or REFS_SHEET not in xl.sheet_names:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names}")
    kw = dict(dtype=object, keep_default_na=False, na_values=[])
    raw = xl.parse(DATA_SHEET, **kw)  # type: ignore[arg-type]
    refs = xl.parse(REFS_SHEET, header=None, **kw)  # type: ignore[arg-type]
    if refs.shape[1] != 1:
        raise ContractError(f"{REFS_SHEET}: {refs.shape[1]} columns")
    entries = [norm_ws(str(v)) for v in refs[0]]
    if not entries[0].startswith("References-World Flux Data Set"):
        raise ContractError(f"{REFS_SHEET}: unexpected first row {entries[0]!r}")
    entries = [e for e in entries[1:] if e]
    return raw, entries


# ---------------------------------------------------------------- main build


def _cellstr(v: object) -> str:
    return "" if blank(v) else repr(v) if not isinstance(v, str) else norm_ws(v)


def build_tables(raw: pd.DataFrame, entries: list[str], access_date: str) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    cols = [norm_header(c) for c in raw.columns]
    if cols != EXPECTED_COLUMNS:
        raise ContractError(f"{DATA_SHEET}: unexpected columns {cols}")
    raw = raw.set_axis(cols, axis=1)
    n_rows = len(raw)
    ids = [int(number(v, "ID#")) for v in raw["ID#"]]
    if len(set(ids)) != n_rows:
        raise ContractError("ID# is not unique")
    if raw.drop(columns=["ID#"]).astype(str).duplicated().any():
        raise ContractError("rows identical except for ID#: this extract expects none (no duplicate flag)")

    lat = [number(v, "Latitude") for v in raw["Latitude"]]
    lon = [number(v, "Longitude") for v in raw["Longitude"]]
    if any(not (-90 <= a <= 90) for a in lat) or any(not (-180 <= b <= 180) for b in lon):
        raise ContractError("coordinates outside the valid range")
    if any(a == 0 and b == 0 for a, b in zip(lat, lon)):
        raise ContractError("a row at 0, 0")
    estuary = [text(v) for v in raw["Estuary Name"]]
    station = [text(v) for v in raw["Station Name"]]
    if any(x is None for x in estuary) or any(x is None for x in station):
        raise ContractError("blank Estuary Name or Station Name")
    region, state, country = (
        [text(v) for v in raw[c]] for c in ("REGION", "state/area", "country")
    )
    if any(x is None for x in region + state + country):
        raise ContractError("blank REGION, state/area or country")
    gchar = [None if text(v) in (None, "ND") else text(v) for v in raw["general characteristics"]]
    reference = [text(v) for v in raw["Reference"]]
    if any(r is None for r in reference):
        raise ContractError("blank Reference")

    def slot(col: str) -> tuple[list[float | None], int]:
        out, n_osn = [], 0
        for v in raw[col]:
            kind, x = classify(v, col, CODES)
            n_osn += kind == "osn"
            out.append(x if kind == "num" else None)
        return out, n_osn

    depth, _ = slot("Water Depth (m)")
    sal, _ = slot("Salinity")
    temp, _ = slot("Temp (°C)")
    do_, n_osn_do = slot("DO(uM)")
    if any(d is not None and d < 0 for d in depth):
        raise ContractError("negative water depth")

    year: list[int | None] = []
    for v in raw["Year"]:
        year.append(int(number(v, "Year")) if is_num(v) else None)
    year_text_non_numeric = int(sum(1 for y in year if y is None))
    month: list[int | None] = []
    for v in raw["Month"]:
        month.append(MONTHS.get(v.strip()) if isinstance(v, str) else None)
    n_date_year_disagree = n_date_month_disagree = 0
    for i in range(n_rows):
        d = raw["Date"].iat[i]
        if hasattr(d, "year"):
            n_date_year_disagree += year[i] is not None and d.year != year[i]
            n_date_month_disagree += month[i] is not None and d.month != month[i]

    sites_avg = [str(v).strip() for v in raw["sites averaged Y/N"]]
    time_avg = [str(v).strip() for v in raw["time averaged Y/N"]]
    if set(sites_avg) - {"Y", "N", "ND"} or set(time_avg) - {"Y", "N", "ND"}:
        raise ContractError("unexpected value in a sites/time averaged column")

    # ---- cells
    cells: dict[str, list[tuple[str, float | None]]] = {}
    coded = {"nd": {}, "osn": {}}
    for col in FACT_COLUMNS:
        got = [classify(v, col, CODES) for v in raw[col]]
        cells[col] = got
        for kind in ("nd", "osn"):
            coded[kind][col] = sum(1 for k, _ in got if k == kind)
    soc = [x for k, x in cells["O2 or SOC (umol/m2/hr)"] if k == "num"]
    if any(x > 0 for x in soc):  # type: ignore[operator]
        raise ContractError("a positive SOC: the source says every SOC is entered negative")
    for col in ("RES (gO2/m2/day)", "POC(%DW)", "PON(%DW)", "PP(%DW)", "SedCHLa (mg/m2)"):
        if any(x < 0 for k, x in cells[col] if k == "num"):  # type: ignore[operator]
            raise ContractError(f"{col}: negative value")

    # ---- samples
    def skey(i: int) -> str:
        return "|".join(_cellstr(raw[c].iat[i]) for c in SAMPLE_KEY_COLUMNS + SAMPLE_KEY_EXTRA)

    def plan_key(i: int) -> str:
        return "|".join(_cellstr(raw[c].iat[i]) for c in SAMPLE_KEY_COLUMNS)

    sample_key = [skey(i) for i in range(n_rows)]
    first_of_sample: dict[str, int] = {}
    for i, k in enumerate(sample_key):
        first_of_sample.setdefault(k, i)
    skeys = sorted(first_of_sample)
    idx = [first_of_sample[k] for k in skeys]
    for i in range(n_rows):  # the sample row is single-valued by construction: check it
        f = first_of_sample[sample_key[i]]
        if (year[i], month[i], sal[i], temp[i], do_[i], depth[i]) != (year[f], month[f], sal[f], temp[f], do_[f], depth[f]):
            raise ContractError(f"row {i}: sample slot values differ inside one sample key")
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "sample_key": skeys, "source_sample_id": None,
            "depth_min_cm": np.nan, "depth_max_cm": np.nan,
            "representative_depth_min_cm": np.nan, "representative_depth_max_cm": np.nan,
            "depth_notes": "usually top 1 cm for the sediment variables (POC, PON, PP, sediment chlorophyll; "
                           "Table 2); flux incubations have no sediment depth interval",
            "sampling_year": pd.array([year[i] for i in idx], dtype="Int64"),
            "sampling_month": pd.array([month[i] for i in idx], dtype="Int64"),
            "taxon": None, "taxon_rank": None, "vegetated_or_bare": None, "marsh_zone": None,
            "sediment_type": None, "photosynthetic_sediment": None, "sample_depth_m": np.nan,
            "water_temperature_c": [np.nan if temp[i] is None else temp[i] for i in idx],
            "water_salinity": [np.nan if sal[i] is None else sal[i] for i in idx],
            "water_salinity_units": ["unitless" if sal[i] is not None else None for i in idx],
            "bottom_water_o2": [np.nan if do_[i] is None else do_[i] for i in idx],
            "bottom_water_o2_units": ["uM" if do_[i] is not None else None for i in idx],
        }
    )

    # ---- locations
    def f(x: float | None) -> str:
        return "" if x is None else repr(float(x))

    loc_key = [
        "|".join([region[i], estuary[i], station[i], f(lat[i]), f(lon[i]), f(depth[i]), state[i], country[i],  # type: ignore[list-item]
                  gchar[i] or ""]) for i in range(n_rows)
    ]
    first_loc: dict[str, int] = {}
    for i, k in enumerate(loc_key):
        first_loc.setdefault(k, i)
    lkeys = sorted(first_loc)
    li = [first_loc[k] for k in lkeys]
    n = len(lkeys)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": [estuary[i] for i in li],
            "source_site_id": [station[i] for i in li], "source_core_id": lkeys,
            "latitude": [lat[i] for i in li], "longitude": [lon[i] for i in li],
            "position_accuracy": np.nan, "position_accuracy_units": None, "position_method": None,
            "country": [country[i] for i in li], "admin_division": [state[i] for i in li], "water_body": None,
            "habitat_type": None, "geomorphic_setting": [gchar[i] for i in li], "bioregion": [region[i] for i in li],
            "marine_ecoregion": None, "salinity_class": None, "salinity_class_level": None,
            "vegetation_class": None, "vegetation_class_level": None, "tidal_position": None,
            "tidal_position_level": None, "modification_status": [[] for _ in range(n)],
            "dominant_species": [[] for _ in range(n)], "elevation": np.nan, "elevation_units": None,
            "elevation_datum": None, "elevation_method": None,
            "water_depth_m": [np.nan if depth[i] is None else depth[i] for i in li], "distance_to_shore_km": np.nan,
        }
    )

    # ---- methods
    technique = [text(v) for v in raw["Method/measurement technique"]]

    def in_situ(t: str | None) -> str | None:
        if t is None or t == "ND":
            return None
        low = t.lower()
        if low.startswith("in situ"):
            return "in situ"
        if low.startswith("lab") or low.startswith("shipboard"):
            return "ex situ"
        raise ContractError(f"unrecognised measurement technique {t!r}")

    pcols = {k: [text(v) for v in raw[c]] for k, c in PARAM_COLUMNS.items()}
    photic = pcols["photic_aphotic"]
    for p in photic:
        if p is not None and p not in LIGHT_DARK and p not in ("ND", "typically dark at sediment surface"):
            raise ContractError(f"unrecognised Photic/Aphotic {p!r}")
    method_rows: dict[str, dict] = {}

    def method_for(attr: str, i: int, is_rate: bool) -> str:
        if not is_rate:
            if attr not in method_rows:
                method_rows[attr] = method_row(SOURCE_KEY, attr, attr, status="method_id_null")
            return attr
        params = {k: (None if v[i] in (None, "ND") else v[i]) for k, v in pcols.items()}
        t = None if technique[i] in (None, "ND") else technique[i]
        params["technique_raw"] = t
        pj = json.dumps(params, sort_keys=True)
        key = f"{attr}|{pj}"
        if key not in method_rows:
            method_rows[key] = method_row(
                SOURCE_KEY, key, attr, status="matched" if t else "method_id_null", technique=t,
                in_situ_ex_situ=in_situ(t), light_dark=LIGHT_DARK.get(photic[i] or ""), params=pj,
            )
        return key

    # ---- references
    reflist = sorted(set(reference))  # type: ignore[arg-type]
    piece_info: dict[str, tuple[str | None, str]] = {}
    ref_pieces: dict[str, list[str]] = {}
    for r in reflist:
        ps = COMPOSITES.get(r, [r])
        ref_pieces[r] = ps
        for p in ps:
            if p not in piece_info:
                piece_info[p] = resolve_piece(p, entries)
    ref_key_of = {r: "+".join(f"ref:{p}" for p in ps) for r, ps in ref_pieces.items()}

    # ---- facts
    rows: list[dict] = []
    flags_of_row: list[list[str]] = [[] for _ in range(n_rows)]

    def base_flags(i: int, x: float, can_be_negative: bool) -> list[str]:
        fl = []
        if x == 0:
            fl.append("zero_value")
        if x < 0 and not can_be_negative:
            fl.append("negative_value")
        if year[i] is None:
            fl.append("year_missing")
        if sites_avg[i] == "Y":
            fl.append("source_site_averaged")
        if time_avg[i] == "Y":
            fl.append("source_time_averaged")
        return fl

    def add_fact(i: int, fact_key: str, attr: str, units: str, x: float, can_neg: bool, is_rate: bool,
                 extra_flags: list[str] | None = None) -> None:
        decl = ATTRIBUTE_DECL[attr]
        if units not in decl.units:
            raise ContractError(f"{attr}: unit {units!r} not declared")
        (sub,) = decl.subfields
        rows.append(
            {
                "source_key": SOURCE_KEY, "fact_key": fact_key, "reference_key": ref_key_of[reference[i]],  # type: ignore[index]
                "location_key": loc_key[i], "sample_key": sample_key[i], "method_key": method_for(attr, i, is_rate),
                "attribute": attr, "measurement_type": decl.measurement_type, "subfield": sub, "value": float(x),
                "units_raw": units, "sd": np.nan, "se": np.nan, "n": None,
                "flags": sorted(base_flags(i, x, can_neg) + (extra_flags or [])),
                "source_row_ref": f"{DATA_FILE}[{DATA_SHEET}]#data_row={i}:{attr}",
            }
        )

    def header_unit(col: str) -> str | None:
        m = re.search(r"\(([^()]+)\)$", col)
        return m.group(1) if m else None

    n_cells_rate = 0
    for col, (attr, units, can_neg) in {**RATE_COLUMNS, **OTHER_COLUMNS}.items():
        if header_unit(col) != units:
            raise ContractError(f"{col}: header unit is not {units!r}")
        is_rate = col in RATE_COLUMNS
        for i, (kind, x) in enumerate(cells[col]):
            if kind != "num":
                continue
            n_cells_rate += 1
            add_fact(i, f"row{i}:{attr}", attr, units, x, can_neg, is_rate)  # type: ignore[arg-type]

    j1_cells: list[tuple[str, str, float, int]] = []
    j1_cells_plan: list[tuple[str, str, float, int]] = []
    for col, (attr, units) in J1_COLUMNS.items():
        if col != "pH" and header_unit(col) != units:
            raise ContractError(f"{col}: header unit is not {units!r}")
        for i, (kind, x) in enumerate(cells[col]):
            if kind == "num":
                j1_cells.append((sample_key[i], attr, x, i))  # type: ignore[arg-type]
                j1_cells_plan.append((plan_key(i), attr, x, i))  # type: ignore[arg-type]
    kept, n_collapsed = collapse_covariates(j1_cells)
    kept_plan, n_collapsed_plan = collapse_covariates(j1_cells_plan)
    j1_units = {a: u for a, u in J1_COLUMNS.values()}
    for sk, attr, x, i, conflict in kept:
        add_fact(i, f"{sk}:{attr}:{x!r}", attr, j1_units[attr], x, False, False,
                 ["covariate_conflict"] if conflict else None)

    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    # a sample / location row exists only if a fact references it (a source row with no numeric value cell has none)
    sample = sample[sample.sample_key.isin(set(fact.sample_key))].reset_index(drop=True)
    loc = loc[loc.location_key.isin(set(fact.location_key))].reset_index(drop=True)
    n_rows_without_fact = n_rows - len({int(k.split(":")[0][3:]) for k in fact.fact_key if k.startswith("row")}
                                       | {i for *_a, i, _c in kept})
    method = pd.DataFrame(sorted(method_rows.values(), key=lambda r: r["method_key"]))

    # ---- reference / citation tables
    used_refs = sorted(set(fact.reference_key))
    if set(used_refs) != set(ref_key_of.values()):
        raise ContractError("a Reference string has no fact")
    reference_df = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": used_refs})
    cit_rows = []
    for r in reflist:
        for k, p in enumerate(ref_pieces[r]):
            entry, _how = piece_info[p]
            ys = _REF_YEAR.findall(p)
            doi = None
            if entry is not None:
                m = _DOI.search(entry)
                doi = m.group(0).rstrip(".,;)").lower() if m else None
            cit_rows.append(
                {
                    "source_key": SOURCE_KEY, "citation_key": f"{ref_key_of[r]}|{k}", "reference_key": ref_key_of[r],
                    "bibliography_id": p, "publication_type": None, "doi": doi, "title": entry, "authors": None,
                    "year": (int(_entry_year(entry) or ys[0]) if entry else int(ys[0])) if ys else None, "url": None,
                    "openalex_id": None, "doi_resolved": None, "doi_resolution_source": None,
                }
            )
    citation = pd.DataFrame(cit_rows)
    citation["year"] = pd.array(list(citation["year"]), dtype="Int64")

    source = pd.DataFrame(
        [{"source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
          "access_date": access_date, "license": SOURCE_LICENSE,
          "notes": CITATION + " Version and license are from the Mendeley Data page (the workbook says v2 in its "
                              "'Cite as' line and v3 in its Table 1 caption). v3 only is loaded."}]
    )
    tables = {
        "source": source, "reference": reference_df, "citation": citation,
        "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
        "method": method, "fact": fact,
    }
    skips: list[dict] = []

    # ---- accounting
    n_cells_j1 = len(j1_cells)
    if len(fact) != n_cells_rate + len(kept):
        raise ContractError("fact count != rate cells + kept J1 facts")
    if n_cells_j1 != len(kept) + n_collapsed:
        raise ContractError("J1 cells != J1 facts + collapsed")
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference_df.reference_key),
        ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    del flags_of_row
    sign = {a: {"positive": int(sum(1 for k, x in cells[c] if k == "num" and x > 0)),  # type: ignore[operator]
                "negative": int(sum(1 for k, x in cells[c] if k == "num" and x < 0)),  # type: ignore[operator]
                "zero": int(sum(1 for k, x in cells[c] if k == "num" and x == 0))}
            for c, (a, _u, _n) in RATE_COLUMNS.items()}
    notes = pcols["edit_notes"]
    report = {
        "source_rows": n_rows, "facts": len(fact), "skipped": 0,
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"], dropna=False).size().items()
        },
        "numeric_cells_by_column": {c: int(sum(1 for k, _ in cells[c] if k == "num")) for c in FACT_COLUMNS},
        "coded_missing_cells_nd": {c: v for c, v in coded["nd"].items() if v},
        "coded_missing_cells_osn": {c: v for c, v in coded["osn"].items() if v},
        "osn_in_do_slot": n_osn_do,
        "non_j1_numeric_cells": n_cells_rate, "j1_numeric_cells": n_cells_j1,
        "j1_facts": len(kept), "j1_collapsed": n_collapsed,
        "j1_facts_under_plan_key": len(kept_plan), "j1_collapsed_under_plan_key": n_collapsed_plan,
        "j1_conflict_facts": int(sum(1 for *_x, c in kept if c)),
        "j1_conflict_samples": len({(sk, a) for sk, a, _v, _i, c in kept if c}),
        "source_rows_without_any_fact": n_rows_without_fact,
        "samples": len(sample), "samples_under_plan_key": len({plan_key(i) for i in range(n_rows)}),
        "locations": len(loc), "estuary_station_pairs": len({(estuary[i], station[i]) for i in range(n_rows)}),
        "methods": len(method), "methods_by_attribute": method.attribute.value_counts().sort_index().to_dict(),
        "references": len(reference_df), "citations": len(citation),
        "reference_strings": len(reflist), "dataref_entries": len(entries),
        "citations_with_entry": int(citation.title.notna().sum()),
        "citations_with_doi": int(citation.doi.notna().sum()),
        "citations_unresolved": sorted(
            f"{p}: {w}" for p, (e, w) in piece_info.items() if e is None
        ),
        "dataref_entries_not_cited": sorted(
            e[:70] for e in entries if e not in {x for x, _ in piece_info.values() if x is not None}
        ),
        "flag_counts": count_flags(fact["flags"]),
        "rows_by_flag": {
            "year_not_a_number": year_text_non_numeric, "month_not_a_month_name": int(sum(1 for m in month if m is None)),
            "sites_averaged_Y": sites_avg.count("Y"), "time_averaged_Y": time_avg.count("Y"),
        },
        "date_disagrees_with_year_rows": int(n_date_year_disagree),
        "date_disagrees_with_month_rows": int(n_date_month_disagree),
        "date_not_a_date_rows": int(sum(1 for v in raw["Date"] if not hasattr(v, "year"))),
        "sign_counts_by_attribute": sign,
        "edit_notes_negative_into_sediment_rows": int(sum(1 for t in notes if t and "negative into sediment" in t)),
        "edit_notes_changed_sign_rows": int(sum(1 for t in notes if t and "changed flux sign to negative" in t)),
        "edit_notes_all_soc_negative_rows": int(sum(1 for t in notes if t and "SOC made negative" in t)),
        "edit_notes_all_soc_flux_negative_rows": int(sum(1 for t in notes if t and "SOC flux entered as negative" in t)),
        "rows_with_water_depth": int(sum(d is not None for d in depth)),
        "technique_values": dict(pd.Series([t or "(blank)" for t in technique]).value_counts()),
        "photic_values": dict(pd.Series([p or "(blank)" for p in photic]).value_counts()),
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
    raw, entries = read_raw(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(raw, entries, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
