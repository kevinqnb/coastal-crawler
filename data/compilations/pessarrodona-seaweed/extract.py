#!/usr/bin/env python3
"""Pessarrodona et al. global dataset of seaweed net primary productivity -> contract tables.

Pessarrodona, A., Filbee-Dexter, K., Krumhansl, K.A., Moore, P.J., Pedersen, M.F., Wernberg, T. (2021): A global dataset
of seaweed net primary productivity. figshare, doi:10.6084/m9.figshare.14882322.v5 (Scientific Data 2022). Raw files
(this directory): ``NPP Dataset.csv`` (latin-1, 1,121 data rows, 80 columns of which 44 are empty ``Unnamed`` columns),
``NPP dataset Sci Data code.R`` (not read), ``References from database.pdf`` (261 reference entries).

D1 (harmonised value). ONE FACT PER ROW: ``pessarrodona_npp`` (B6 ``seagrass_seaweed_npp``) = the source's harmonised
``Avg_NPP_kg_C_m2_y`` in ``kg C m-2 y-1`` (the header's unit; all 1,121 rows have a value), ``sd`` = ``stdev_NPP_kg_C_m2_y``
(blank and ``NA`` -> NULL). The ORIGINAL value (``Avg_NPP`` and ``sted_NPP`` with ``NPP_units``), ``Matter_Unit`` (DW, C,
FW, O), the FW:DW and DW:C conversion factors, and the rest of the row's method information sit in a per-source-row
``method_dim`` row (method_key = ``row<i>``) as ``params``; ``conversion_applied`` states original -> harmonised where they
differ (flag ``converted_by_source``). A conversion factor the file does not hold cannot be told apart from none. Not
loaded: ``Standing biomass_kg_DW_m2`` (11 values; not in the plan, reported) and the empty ``Unnamed`` columns; the
stray non-empty cells in columns 73, 77, 78, 79 (rows 1083 and 1084) are asserted to be exactly the known ones.

Locations: ``location_key`` = Site | Site_ID_within_study | lat | lon | habitat label | Level | Originalcoordinates |
Depth_min_m | Depth_max_m. ``habitat_type`` = ``<Vegetation_category> | <Substrate_category>`` (composite, approved in the
plan; every combination is in the label map and an unlisted one raises). ``tidal_position`` = ``Level`` (Intertidal /
Subtidal; ``NA`` -> NULL). ``position_method``: ``Originalcoordinates`` YES -> ``reported``, NO -> ``approximated by
compiler`` (also flag ``coords_approximate``); 9 rows have no coordinates (flag ``coords_missing``).
Depth: ``Depth_min_m`` / ``Depth_max_m`` (``NA`` both on 61 rows). Rule: ``location.water_depth_m`` is set ONLY when min = max
AND the value is >= 0; every other case (a range, or a negative value) is text in the sample's ``depth_notes``. The
negative values occur almost only on intertidal rows (192 of 195 rows with a negative bound), which reads as
elevation above the water level, but the source states no sign convention, so no sign is interpreted: nothing negative
is written to the numeric slot.

Samples: one per source row (``row<i>``): ``sampling_year`` = ``Start_year`` when it equals ``End_year`` (NULL for a
multi-year range, kept as text in ``depth_notes``; flag ``year_missing`` only where Start_year is ``NA``, 52 rows);
``taxon`` = ``Species`` (the source's label, e.g. ``Turf assemblage``), ``taxon_rank`` = ``multiple`` where
``Multispecies`` = YES else ``species``. Phylum, order, family, genus, seasons and sampling frequency are in the params.

Methods: ``technique`` = ``Prod_method_group`` (``Unknown`` on 8 rows -> NULL technique, status ``method_id_null``);
``Prod_method_general``, ``Description``, ``Data_mining_method`` (flag ``digitized_from_figure`` where it contains
``Graph digitizer``), ``Aggregation_required``, ``Person_entering_data`` are params.

References: ``Reference`` strings (author-year text) -> ``ref:<string>`` (whitespace normalised). Full text and DOIs come
from the PDF's entries by an explicit rule: first-author surname (ASCII-folded, from the entry; ``———`` repeats the
previous author) and the year with its suffix letter must match exactly ONE entry, and for ``A & B`` strings B must be in
that entry. Anything else (typos, suffix letters the PDF lacks, secondary citations ``X in Y``, theses, unpublished work)
is NOT guessed: it keeps a text-only citation and is listed in the report. Strings of the form ``X in Y`` / ``cited in``
(the source marks the citation as secondary) carry flag ``cited_secondary``.

Flags: negative_value (harmonised value < 0), zero_value, coords_missing, coords_approximate, year_missing,
digitized_from_figure, converted_by_source, cited_secondary, duplicate_source_row (none occur; asserted).

Provenance: ``source_row_ref`` = ``NPP Dataset.csv#data_row=<0-based data row>:pessarrodona_npp``.

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
from _g3_common import count_flags, method_row, norm_ws  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "pessarrodona-seaweed"
SOURCE_NAME = "A global dataset of seaweed net primary productivity (Pessarrodona et al.)"
SOURCE_DOI = "10.6084/m9.figshare.14882322.v5"
SOURCE_VERSION = "figshare v5 (2022-08-04)"  # figshare record, read by the integration plan
SOURCE_LICENSE = "CC BY 4.0"  # figshare record
DATA_FILE, REFS_FILE = "NPP Dataset.csv", "References from database.pdf"
CITATION = (
    "Pessarrodona, A., Filbee-Dexter, K., Krumhansl, K.A., Moore, P.J., Pedersen, M.F., Wernberg, T. (2021): A global "
    "dataset of seaweed net primary productivity [Dataset]. figshare, https://doi.org/10.6084/m9.figshare.14882322.v5 "
    "(Scientific Data 2022)"
)
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()
check_skip_reasons(ALLOWED_SKIP_REASONS)

ATTR = "pessarrodona_npp"
UNIT = "kg C m-2 y-1"  # header Avg_NPP_kg_C_m2_y: kg C per m2 per year
VALUE_COL, SD_COL = "Avg_NPP_kg_C_m2_y", "stdev_NPP_kg_C_m2_y"
N_COLS, N_ROWS = 80, 1121
NAMED_COLUMNS = [
    "Vegetation_category", "Substrate_category", "Level", "Species", "Phyla", "Order", "Family", "Genus",
    "Multispecies", "Aggregation_required", "Site", "Site_ID_within_study", "Latitude_decimal_degrees",
    "Longitude_decimal_degrees", "Originalcoordinates", "Depth_min_m", "Depth_max_m", "Start_year", "End_year",
    "Ann_sampling_freq", "Seasons", "Data_mining_method", "Description", "Reference", "Prod_method_group",
    "Prod_method_general", "Standing biomass_kg_DW_m2", "Avg_NPP", "sted_NPP", "NPP_units", "Matter_Unit",
    "FW_DW_Conversion_factor", "DW_Carbon_Conversion_factor", VALUE_COL, SD_COL, "Person_entering_data",
]
# the only non-empty cells in the unnamed columns: (data row, column) -> text
STRAY_CELLS = {
    (1083, "Unnamed: 77"): "NO", (1083, "Unnamed: 78"): "NO", (1083, "Unnamed: 79"): "AP",
    (1084, "Unnamed: 73"): "1", (1084, "Unnamed: 77"): "NO", (1084, "Unnamed: 78"): "YES", (1084, "Unnamed: 79"): "AP",
}
YESNO = {"YES", "NO"}
LEVELS = {"Intertidal", "Subtidal", "NA"}
MATTER = {"DW", "C", "FW", "O"}
_NUM = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")
_DOI = re.compile(r"10\.\d{4,9}/[^\s]+")
_YEAR = re.compile(r"\b((?:1[89]|20)\d\d[a-z]?)\b")
_SECONDARY = re.compile(r"\bcited in\b|\bin\b(?=\s+[A-Z])")


def blank(v: str) -> bool:
    return v.strip() == ""


def num(v: str, what: str) -> float | None:
    """Blank or 'NA' -> None; a number -> float; anything else raises."""
    v = v.strip()
    if v in ("", "NA"):
        return None
    if not _NUM.fullmatch(v):
        raise ContractError(f"{what}: unrecognised value {v!r}")
    return float(v)


def txt(v: str) -> str | None:
    v = norm_ws(v)
    return None if v in ("", "NA") else v


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s.replace("ø", "o").replace("Ø", "O")).encode("ascii", "ignore").decode().lower()


# ---------------------------------------------------------------- references


def read_reference_entries(path: Path) -> list[dict]:
    """Entries of the reference PDF (hanging indent marks an entry start; ``———`` repeats the previous author)."""
    import pypdf  # declared project dependency

    entries: list[list[str]] = []
    for page in pypdf.PdfReader(str(path)).pages:
        lines = [ln for ln in page.extract_text(extraction_mode="layout").split("\n") if ln.strip()]
        ind = [len(ln) - len(ln.lstrip()) for ln in lines]
        if not lines:
            raise ContractError(f"{path.name}: an empty page")
        base = min(ind)
        for ln, i in zip(lines, ind):
            if i == base:
                entries.append([ln.strip()])
            elif entries:
                entries[-1].append(ln.strip())
            else:
                raise ContractError(f"{path.name}: a continuation line before the first entry")
    out, last_author = [], None
    for e in entries:
        t = re.sub(r"([–-])\s+(?=\d)", r"\1", norm_ws(" ".join(e)))
        if t.startswith("———"):
            if last_author is None:
                raise ContractError(f"{path.name}: '———' before any author")
            author, rest = last_author, t.lstrip("—").lstrip(". ").strip()
        else:
            m = re.match(r"(.+?)\.?\s+((?:1[89]|20)\d\d[a-z]?)\.\s", t)
            if not m:
                raise ContractError(f"{path.name}: cannot read author and year from {t[:80]!r}")
            author, rest = m.group(1), t[m.start(2):]
            last_author = author
        ym = re.match(r"((?:1[89]|20)\d\d[a-z]?)\.", rest)
        if not ym:
            raise ContractError(f"{path.name}: no year after the author in {t[:80]!r}")
        out.append({"text": t, "author": author, "year": ym.group(1), "surname": fold(author.split(",")[0]).strip()})
    return out


def _string_key(s: str) -> tuple[str | None, str | None, str | None]:
    """(first-author surname, year with suffix, second author) read from a Reference string; None where not readable."""
    f = fold(norm_ws(s))
    ys = _YEAR.findall(f)
    m = re.match(r"[a-z][a-z' .-]*?(?=\s+et\.? al|\s+and\s|\s*&|,|\s+\(?\d{4})", f)
    sur = m.group(0).strip() if m else None
    second = None
    m2 = re.match(r"[a-z][a-z' .-]*?\s*&\s*([a-z][a-z' -]*?)\s*[,(]?\s*\(?\d{4}", f)
    if m2:
        second = m2.group(1).strip()
    return sur, ys[0] if ys else None, second


def _entry_author_count(author: str) -> int:
    """Authors in an entry's author text ("Surname, Given, and Name Surname"): the number of commas; 3 for 'et al'."""
    if re.search(r"\bet al\b", author):
        return 3
    return author.count(",")


def _string_kind(ref: str) -> str | None:
    """'single', 'pair' or 'many' from the shape of a Reference string; None where it has a comma or is unclear."""
    f = norm_ws(ref)
    if "," in f.split("(")[0]:
        return None
    if re.search(r"\bet\.? al\b", f, flags=re.I):
        return "many"
    if re.search(r"\s(&|and)\s", f):
        return "pair"
    return "single"


def resolve_reference(ref: str, entries: list[dict]) -> tuple[dict | None, str]:
    sur, year, second = _string_key(ref)
    if sur is None or year is None:
        return None, "no first author and year can be read from the string"
    cand = [e for e in entries if e["surname"] == sur and e["year"] == year]
    if second:
        cand = [e for e in cand if second.split()[-1] in fold(e["author"])]
    how = "unique surname and year"
    n_before = len(cand)
    kind = _string_kind(ref)
    if len(cand) > 1 and kind:  # several entries share author and year: the shape of the string (one, two, many authors) may settle it
        n = {"single": lambda k: k == 1, "pair": lambda k: k == 2, "many": lambda k: k >= 3}[kind]
        cand = [e for e in cand if n(_entry_author_count(e["author"]))]
        how = "surname and year, settled by author count"
    if len(cand) == 1:
        return cand[0], how
    if not cand:
        if n_before:
            return None, f"{n_before} PDF entries match this first author and year, none with the string's author count"
        return None, "no PDF entry with this first author and year"
    return None, f"{len(cand)} PDF entries match this first author and year"


# ---------------------------------------------------------------- main build


def read_raw(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[], encoding="latin-1")


def build_tables(raw: pd.DataFrame, entries: list[dict], access_date: str,
                expected_stray: dict[tuple[int, str], str] = STRAY_CELLS) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if raw.shape[1] != N_COLS:
        raise ContractError(f"{DATA_FILE}: {raw.shape[1]} columns, expected {N_COLS}")
    if list(raw.columns[:36]) != NAMED_COLUMNS or any(not c.startswith("Unnamed") for c in raw.columns[36:]):
        raise ContractError(f"{DATA_FILE}: unexpected columns {list(raw.columns)}")
    n_rows = len(raw)
    unnamed = [c for c in raw.columns[36:]]
    stray = {(i, c): v for c in unnamed for i, v in enumerate(raw[c]) if not blank(v)}
    if stray != expected_stray:
        raise ContractError(f"unexpected non-empty cells in the unnamed columns: {sorted(set(stray.items()) ^ set(expected_stray.items()))[:6]}")
    if raw[NAMED_COLUMNS].astype(str).duplicated().any():
        raise ContractError("duplicate source rows: this extract expects none")

    c = {k: list(raw[k]) for k in NAMED_COLUMNS}
    veg, subs = [norm_ws(v) for v in c["Vegetation_category"]], [norm_ws(v) for v in c["Substrate_category"]]
    if any(not v for v in veg + subs):
        raise ContractError("blank habitat label")
    hab = [f"{a} | {b}" for a, b in zip(veg, subs)]
    level = [norm_ws(v) for v in c["Level"]]
    if set(level) - LEVELS:
        raise ContractError(f"unknown Level {set(level) - LEVELS}")
    orig = [norm_ws(v) for v in c["Originalcoordinates"]]
    if set(orig) - YESNO or set(c["Multispecies"]) - YESNO or set(c["Aggregation_required"]) - YESNO:
        raise ContractError("a YES/NO column has another value")
    if set(c["Matter_Unit"]) - MATTER:
        raise ContractError(f"unknown Matter_Unit {set(c['Matter_Unit']) - MATTER}")
    lat = [num(v, "latitude") for v in c["Latitude_decimal_degrees"]]
    lon = [num(v, "longitude") for v in c["Longitude_decimal_degrees"]]
    if any((a is None) != (b is None) for a, b in zip(lat, lon)):
        raise ContractError("latitude and longitude are not missing together")
    if any(a is not None and not (-90 <= a <= 90) for a in lat) or any(b is not None and not (-180 <= b <= 180) for b in lon):
        raise ContractError("coordinates outside the valid range")
    dmin = [num(v, "Depth_min_m") for v in c["Depth_min_m"]]
    dmax = [num(v, "Depth_max_m") for v in c["Depth_max_m"]]
    if any((a is None) != (b is None) for a, b in zip(dmin, dmax)):
        raise ContractError("Depth_min_m and Depth_max_m are not missing together")
    y0 = [None if (x := num(v, "Start_year")) is None else int(x) for v in c["Start_year"]]
    y1 = [None if (x := num(v, "End_year")) is None else int(x) for v in c["End_year"]]
    if any((a is None) != (b is None) for a, b in zip(y0, y1)) or any(a is not None and a > b for a, b in zip(y0, y1)):  # type: ignore[operator]
        raise ContractError("Start_year / End_year missing separately or inverted")
    value = [num(v, VALUE_COL) for v in c[VALUE_COL]]
    if any(x is None for x in value):
        raise ContractError(f"a row without {VALUE_COL}")
    sd = [num(v, SD_COL) for v in c[SD_COL]]
    if any(x is not None and x < 0 for x in sd):
        raise ContractError("a negative SD")
    avg_orig = [num(v, "Avg_NPP") for v in c["Avg_NPP"]]
    if any(x is None for x in avg_orig):
        raise ContractError("a row without Avg_NPP")
    units_orig = [norm_ws(v) for v in c["NPP_units"]]
    if any(not u for u in units_orig):
        raise ContractError("a row without NPP_units")
    standing = [num(v, "Standing biomass_kg_DW_m2") for v in c["Standing biomass_kg_DW_m2"]]
    species = [txt(v) for v in c["Species"]]
    site = [txt(v) for v in c["Site"]]
    site_id = [txt(v) for v in c["Site_ID_within_study"]]
    refs_raw = [norm_ws(v) for v in c["Reference"]]
    if any(not r for r in refs_raw):
        raise ContractError("a blank Reference")
    if any(s is None for s in species + site):
        raise ContractError("a blank Species or Site")
    method_group = [txt(v) for v in c["Prod_method_group"]]
    mining = [norm_ws(v) for v in c["Data_mining_method"]]
    if set(mining) - {"Text", "Graph digitizer", "Raw data", "Text & Graph digitizer"}:
        raise ContractError(f"unknown Data_mining_method {set(mining)}")

    # ---- locations
    def f(x: float | None) -> str:
        return "" if x is None else repr(float(x))

    def depth_rule(i: int) -> float | None:
        a, b = dmin[i], dmax[i]
        return a if a is not None and a == b and a >= 0 else None

    loc_key = [
        "|".join([site[i], site_id[i] or "", f(lat[i]), f(lon[i]), hab[i], level[i], orig[i],  # type: ignore[list-item]
                  c["Depth_min_m"][i].strip(), c["Depth_max_m"][i].strip()]) for i in range(n_rows)
    ]
    first_loc: dict[str, int] = {}
    for i, k in enumerate(loc_key):
        first_loc.setdefault(k, i)
    lkeys = sorted(first_loc)
    li = [first_loc[k] for k in lkeys]
    n = len(lkeys)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": [site[i] for i in li],
            "source_site_id": [site_id[i] for i in li], "source_core_id": lkeys,
            "latitude": [np.nan if lat[i] is None else lat[i] for i in li],
            "longitude": [np.nan if lon[i] is None else lon[i] for i in li],
            "position_accuracy": np.nan, "position_accuracy_units": None,
            "position_method": ["reported" if orig[i] == "YES" else "approximated by compiler" for i in li],
            "country": None, "admin_division": None, "water_body": None, "habitat_type": [hab[i] for i in li],
            "geomorphic_setting": None, "bioregion": None, "marine_ecoregion": None, "salinity_class": None,
            "salinity_class_level": None, "vegetation_class": None, "vegetation_class_level": None,
            "tidal_position": [None if level[i] == "NA" else level[i] for i in li], "tidal_position_level": None,
            "modification_status": [[] for _ in range(n)], "dominant_species": [[] for _ in range(n)],
            "elevation": np.nan, "elevation_units": None, "elevation_datum": None, "elevation_method": None,
            "water_depth_m": [np.nan if depth_rule(i) is None else depth_rule(i) for i in li],
            "distance_to_shore_km": np.nan,
        }
    )

    # ---- samples (one per row)
    def notes(i: int) -> str | None:
        parts = []
        if dmin[i] is not None and depth_rule(i) is None:
            parts.append(f"depth_min_m={c['Depth_min_m'][i].strip()}; depth_max_m={c['Depth_max_m'][i].strip()}")
        if y0[i] is not None and y0[i] != y1[i]:
            parts.append(f"years={y0[i]}-{y1[i]}")
        return "; ".join(parts) or None

    sample_key = [f"row{i}" for i in range(n_rows)]
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY, "sample_key": sample_key, "source_sample_id": None,
            "depth_min_cm": np.nan, "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan, "depth_notes": [notes(i) for i in range(n_rows)],
            "sampling_year": pd.array([y0[i] if y0[i] is not None and y0[i] == y1[i] else None for i in range(n_rows)], dtype="Int64"),
            "sampling_month": pd.array([None] * n_rows, dtype="Int64"), "taxon": species,
            "taxon_rank": ["multiple" if c["Multispecies"][i] == "YES" else "species" for i in range(n_rows)],
            "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None, "photosynthetic_sediment": None,
            "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan,
            "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
        }
    )

    # ---- per-row methods (D1)
    converted = [not (units_orig[i] == "kg C m-2 yr-1" and avg_orig[i] == value[i]) for i in range(n_rows)]
    method_rows = []
    for i in range(n_rows):
        params = {
            "avg_npp_original": c["Avg_NPP"][i].strip(), "sd_original": txt(c["sted_NPP"][i]),
            "npp_units_original": units_orig[i], "matter_unit": c["Matter_Unit"][i],
            "fw_dw_conversion_factor": txt(c["FW_DW_Conversion_factor"][i]),
            "dw_carbon_conversion_factor": txt(c["DW_Carbon_Conversion_factor"][i]),
            "prod_method_general": txt(c["Prod_method_general"][i]), "description": txt(c["Description"][i]),
            "data_mining_method": mining[i], "aggregation_required": c["Aggregation_required"][i],
            "multispecies": c["Multispecies"][i], "phyla": txt(c["Phyla"][i]), "order": txt(c["Order"][i]),
            "family": txt(c["Family"][i]), "genus": txt(c["Genus"][i]), "seasons": txt(c["Seasons"][i]),
            "annual_sampling_frequency": txt(c["Ann_sampling_freq"][i]), "start_year": y0[i], "end_year": y1[i],
            "person_entering_data": txt(c["Person_entering_data"][i]), "originalcoordinates": orig[i],
            "level": level[i],
        }
        unknown = method_group[i] == "Unknown"
        method_rows.append(method_row(
            SOURCE_KEY, f"row{i}", ATTR, status="method_id_null" if unknown else "matched",
            technique=None if unknown else method_group[i],
            conversion_applied=(f"source harmonised: {c['Avg_NPP'][i].strip()} {units_orig[i]} (matter {c['Matter_Unit'][i]})"
                                f" -> {c[VALUE_COL][i].strip()} {UNIT}") if converted[i] else None,
            params=json.dumps(params, sort_keys=True),
        ))
    method = pd.DataFrame(method_rows)

    # ---- references
    ref_strings = sorted(set(refs_raw))
    resolved = {r: resolve_reference(r, entries) for r in ref_strings}
    ref_key = {r: f"ref:{r}" for r in ref_strings}
    secondary = {r: bool(_SECONDARY.search(r)) for r in ref_strings}
    reference_df = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": [ref_key[r] for r in ref_strings]})
    cit = []
    for r in ref_strings:
        e, _how = resolved[r]
        ys = _YEAR.findall(r)
        year = int(re.match(r"\d{4}", e["year"]).group(0)) if e else (int(ys[0][:4]) if ys else None)  # type: ignore[union-attr]
        doi = None
        url = None
        if e:
            m = _DOI.search(e["text"])
            doi = m.group(0).rstrip(".,;)").lower() if m else None
            mu = re.search(r"https?://[^\s]+", e["text"])
            url = None if doi else (mu.group(0).rstrip(".,;)") if mu else None)
        cit.append({
            "source_key": SOURCE_KEY, "citation_key": f"{ref_key[r]}|0", "reference_key": ref_key[r],
            "bibliography_id": r, "publication_type": None, "doi": doi, "title": e["text"] if e else None,
            "authors": None, "year": year, "url": url, "openalex_id": None, "doi_resolved": None,
            "doi_resolution_source": None,
        })
    citation = pd.DataFrame(cit)
    citation["year"] = pd.array(list(citation["year"]), dtype="Int64")

    # ---- facts
    rows = []
    for i in range(n_rows):
        fl = []
        if value[i] < 0:  # type: ignore[operator]
            fl.append("negative_value")
        if value[i] == 0:
            fl.append("zero_value")
        if lat[i] is None:
            fl.append("coords_missing")
        elif orig[i] == "NO":
            fl.append("coords_approximate")
        if y0[i] is None:
            fl.append("year_missing")
        if "Graph digitizer" in mining[i]:
            fl.append("digitized_from_figure")
        if converted[i]:
            fl.append("converted_by_source")
        if secondary[refs_raw[i]]:
            fl.append("cited_secondary")
        decl = ATTRIBUTE_DECL[ATTR]
        (sub_f,) = decl.subfields
        rows.append(
            {
                "source_key": SOURCE_KEY, "fact_key": f"row{i}:{ATTR}", "reference_key": ref_key[refs_raw[i]],
                "location_key": loc_key[i], "sample_key": sample_key[i], "method_key": f"row{i}", "attribute": ATTR,
                "measurement_type": decl.measurement_type, "subfield": sub_f, "value": float(value[i]),  # type: ignore[arg-type]
                "units_raw": UNIT, "sd": np.nan if sd[i] is None else sd[i], "se": np.nan, "n": None,
                "flags": sorted(fl), "source_row_ref": f"{DATA_FILE}#data_row={i}:{ATTR}",
            }
        )
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    if UNIT not in ATTRIBUTE_DECL[ATTR].units:
        raise ContractError("unit not declared")

    source = pd.DataFrame(
        [{"source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
          "access_date": access_date, "license": SOURCE_LICENSE,
          "notes": CITATION + " Version and license are from the figshare record (the CSV states neither)."}]
    )
    tables = {
        "source": source, "reference": reference_df, "citation": citation,
        "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
        "method": method, "fact": fact,
    }

    # ---- accounting: one value cell per row = one fact; no skips
    if len(fact) != n_rows:
        raise ContractError("facts != rows")
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference_df.reference_key),
        ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    used_entries = {e["text"] for e, _ in resolved.values() if e}
    report = {
        "source_rows": n_rows, "facts": len(fact), "skipped": 0,
        "rows_with_sd": int(fact.sd.notna().sum()), "rows_with_sd_zero": int((fact.sd == 0).sum()),
        "value_min": float(fact.value.min()), "value_max": float(fact.value.max()),
        "rows_negative_value": int((fact.value < 0).sum()),
        "rows_with_original_sd": int(sum(1 for v in c["sted_NPP"] if num(v, "sted_NPP") is not None)),
        "standing_biomass_cells_not_loaded": int(sum(1 for x in standing if x is not None)),
        "stray_cells_in_unnamed_columns": len(stray),
        "samples": len(sample), "locations": len(loc), "methods": len(method),
        "methods_unknown_technique": int((method.method_status == "method_id_null").sum()),
        "references": len(reference_df), "reference_strings": len(ref_strings),
        "pdf_entries": len(entries), "pdf_entries_with_doi": int(sum(1 for e in entries if _DOI.search(e["text"]))),
        "pdf_entries_used": len(used_entries), "pdf_entries_not_cited": len(entries) - len(used_entries),
        "citations_with_entry": int(citation.title.notna().sum()), "citations_with_doi": int(citation.doi.notna().sum()),
        "citations_unresolved": sorted(f"{r}: {w}" for r, (e, w) in resolved.items() if e is None),
        "strings_marked_secondary": sorted(r for r, v in secondary.items() if v),
        "flag_counts": count_flags(fact["flags"]),
        "rows_by_original_coordinates": pd.Series(orig).value_counts().to_dict(),
        "rows_by_level": pd.Series(level).value_counts().to_dict(),
        "rows_by_data_mining_method": pd.Series(mining).value_counts().to_dict(),
        "rows_by_matter_unit": pd.Series(c["Matter_Unit"]).value_counts().to_dict(),
        "rows_with_water_depth_slot": int(loc_rows_with_depth(fact, loc)),
        "rows_with_negative_depth_bound": int(sum(1 for a, b in zip(dmin, dmax) if (a is not None and a < 0) or (b is not None and b < 0))),
        "rows_with_depth_notes": int(sample.depth_notes.notna().sum()),
        "rows_multi_year": int(sum(1 for a, b in zip(y0, y1) if a is not None and a != b)),
        "rows_converted_by_source": int(sum(converted)),
        "rows_taxon_rank_multiple": int((sample.taxon_rank == "multiple").sum()),
    }
    return tables, [], report


def loc_rows_with_depth(fact: pd.DataFrame, loc: pd.DataFrame) -> int:
    depth = loc.set_index("location_key").water_depth_m
    return int(fact.location_key.map(depth).notna().sum())


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
    entries = read_reference_entries(args.raw_dir / REFS_FILE)
    tables, skips, report = build_tables(raw, entries, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
