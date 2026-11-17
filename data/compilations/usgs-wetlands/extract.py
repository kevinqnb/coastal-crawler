#!/usr/bin/env python3
"""USGS tidal saline wetland ecosystem properties (Feher et al.) -> compilation-contract tables.

Feher, L.C., Osland, M.J., Grace, J.B., Buffington, K.J., Carr, J.A., Guntenspergen, G.R., Martinez, M., Neville, J.A., Rankin, L.L.,
Thorne, K.M. (2025): North American dataset of ecosystem properties in tidal saline wetlands: canopy height, aboveground biomass,
productivity, soil carbon density, and soil carbon accumulation. U.S. Geological Survey data release, doi:10.5066/P133WKRO. Raw
files (this directory): ``ecosystem_attribute_data_2023.xlsx`` (five data sheets and ``Citations``) and its FGDC metadata
``ecosystem_attributes_2023.xml``.

Grain: one fact per data row (every row of the five data sheets has exactly one value, asserted), in the unit of the sheet's
``units`` column (asserted equal to the declared unit). Nothing is converted or capped.

Sheet -> attribute (type/subfield, unit):
  Height               usgs_canopy_height        B3 canopy_height                       m
  Biomass              usgs_aboveground_biomass  B2 above_ground_biomass                t/ha
  Productivity         usgs_productivity         B6 litterfall | above_ground_npp | NULL  t/ha/yr
  soil_C_density       usgs_soil_c_density       C1 soil_carbon_density (extension)     g/cm3
  soil_C_accumulation  usgs_soil_c_accum         G2 carbon_burial                       g/m2/yr

Productivity subfield (an exhaustive table over the 14 ``method`` strings; an unknown string raises). The xml defines the whole sheet
as aboveground productivity, so every named method maps to ``above_ground_npp`` except the litterfall one. ``not reported`` leaves
the subfield NULL, since the method is unknown:
  litterfall, recorded monthly over at least 1 year -> litterfall
  peak standing crop | Smalley 1959 | Wiegert and Evans 1964 | Milner and Hughes 1968 | summation method for NPP (litterfall + wood
  growth) | non-destructive | Valiela et al. 1975 | Rey's method | Shew et al. 1981 | Lomnicki et al. 1968 | Wiegert and McGinnis
  1975 | Pierce 1983; Dickerman et al. 1986 -> above_ground_npp
  not reported -> NULL

Locations: location_key = lat | lon | type | veg_type | location, so rows at one coordinate with different labels stay separate.
site_name is ``location`` (stripped). habitat_type is ``type`` raw (salt marsh, mangrove, salt flat, salt marsh mangrove; the label map
maps the first two and leaves the others blank). vegetation_class is ``veg_type`` raw. Some sites were placed approximately from the
text (Google Earth, xml), which is not marked per row, so there is no per-fact flag. ``coords_integer_degree`` marks both coordinates
being whole degrees. No country is derived.

Samples: one per data row (keyed sheet:row). Only soil_C_density has a depth: ``core_depth_cm`` is parsed strictly as ``0-N`` into
depth_min and depth_max, and ``not reported`` gives NULL depth and ``depth_missing``. The other sheets are site averages with no
sediment depth interval. The source has no year.

Methods: one row per distinct (attribute, method string, range, source_review, included_Feher_2017). technique is the sheet's
``method`` string (Biomass and Productivity only). ``not reported`` gives a NULL technique, method_id_null and ``method_missing``.
The ``*_range`` text, ``source_review`` and ``included_Feher_2017`` go to ``params``. The range is NOT an sd, and sd, se and n are NULL.
Sheets without a method column get method_id_null and no ``method_missing``.

References: ``source_primary`` is the study. Citation strings (data cells and the Citations tab) are whitespace-normalised first (the
tab has NBSP characters). Each ``;``-separated part is looked up in the tab by an accent- and case-insensitive match. A part with a
valid DOI url (``10.NNNN/...``; three ``doi.org`` urls are not DOIs and stay plain urls) gets ``doi:<lower-case doi>``, else
``ref:<string>``. A composite string gets the part keys joined by ``+``, one citation row per part, and the part count must equal the
citation row count. A part with no tab row must be in a declared set: ``UNPUBLISHED`` (the source's own "unpublished data") or
``UNRESOLVED_NEAR_MISS`` (a different-year entry exists but the data side has no title to check, so no match). The unmatched parts must
equal their union exactly. ``COMMA_SPLITS`` declares the one comma-joined composite. Citation ``title`` keeps the tab's
``full_citation`` text, unparsed. ``source_review`` (the secondary compilation a value was taken from) is a string in ``params`` with
the flag ``secondary_source``.

Flags (nothing is changed or dropped): zero_value; assigned_by_source (the xml says salt-flat height and biomass were "estimated as 0",
asserted to be exactly the salt-flat rows of those two sheets); secondary_source; method_missing; depth_missing; duplicate_source_row
(rows identical in all columns of their sheet); coords_integer_degree; negative_value.

Provenance: ``source_row_ref`` = ``ecosystem_attribute_data_2023.xlsx[<sheet>]#data_row=<0-based row after the header>:<attribute>``.

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "usgs-wetlands"
SOURCE_NAME = "USGS North American tidal saline wetland ecosystem properties (Feher et al.)"
SOURCE_DOI = "10.5066/P133WKRO"
DATA_FILE = "ecosystem_attribute_data_2023.xlsx"
XML_FILE = "ecosystem_attributes_2023.xml"
CITATION = (
    "Feher, L.C., Osland, M.J., Grace, J.B., Buffington, K.J., Carr, J.A., Guntenspergen, G.R., "
    "Martinez, M., Neville, J.A., Rankin, L.L., Thorne, K.M. (2025): North American dataset of ecosystem "
    "properties in tidal saline wetlands -- Canopy height, aboveground biomass, productivity, soil carbon "
    "density, and soil carbon accumulation. U.S. Geological Survey data release, "
    "https://doi.org/10.5066/P133WKRO"
)
XML_ORIGINS = [
    "Laura C. Feher", "Michael J. Osland", "James B. Grace", "Kevin J. Buffington", "Joel A. Carr",
    "Glenn R. Guntenspergen", "Melinda Martinez", "Justine A. Neville", "Lyndsay L. Rankin", "Karen M. Thorne",
]
USECONST_START = "None."  # the FGDC use constraint begins with this; asserted, then recorded verbatim
CITATIONS_SHEET = "Citations"
CITATION_COLS = ["citation", "url", "full_citation"]
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()  # every row is a fact
check_skip_reasons(ALLOWED_SKIP_REASONS)

COMMON = ["type", "veg_type", "lat", "long", "location"]
TYPES = frozenset({"salt marsh", "mangrove", "salt flat", "salt marsh mangrove"})
VEG_TYPES = frozenset({"G", "M", "F", "S", "G/M"})


@dataclass(frozen=True)
class Sheet:
    name: str
    attribute: str
    value_col: str
    units: str
    n_rows: int  # asserted: the number of data rows in the release
    columns: tuple[str, ...]
    range_col: str | None = None
    method_col: str | None = None
    depth_col: str | None = None


SHEETS: tuple[Sheet, ...] = (
    Sheet("Height", "usgs_canopy_height", "height", "m", 258,
          ("type", "veg_type", "lat", "long", "location", "height", "units", "source_review",
           "source_primary", "height_range", "included_Feher_2017"),
          range_col="height_range"),
    Sheet("Biomass", "usgs_aboveground_biomass", "biomass", "t/ha", 225,
          ("type", "veg_type", "lat", "long", "location", "biomass", "units", "source_review",
           "source_primary", "method", "biomass_range", "included_Feher_2017"),
          range_col="biomass_range", method_col="method"),
    Sheet("Productivity", "usgs_productivity", "productivity", "t/ha/yr", 191,
          ("type", "veg_type", "lat", "long", "location", "productivity", "units", "source_review",
           "source_primary", "method", "productivity_range", "included_Feher_2017"),
          range_col="productivity_range", method_col="method"),
    Sheet("soil_C_density", "usgs_soil_c_density", "soil_c_density", "g/cm3", 532,
          ("type", "veg_type", "lat", "long", "location", "soil_c_density", "units", "source_review",
           "source_primary", "core_depth_cm", "included_Feher_2017"),
          depth_col="core_depth_cm"),
    Sheet("soil_C_accumulation", "usgs_soil_c_accum", "soil_c_accum_rate", "g/m2/yr", 279,
          ("type", "veg_type", "lat", "long", "location", "soil_c_accum_rate", "units", "source_review",
           "source_primary", "included_Feher_2017"),
          ),
)
SHEET_BY_NAME = {s.name: s for s in SHEETS}

NOT_REPORTED = "not reported"
# productivity: exhaustive over the 14 method strings of the release; unknown strings raise
PRODUCTIVITY_SUBFIELD: dict[str, str | None] = {
    "litterfall, recorded monthly over at least 1 year": "litterfall",
    "peak standing crop": "above_ground_npp",
    "Smalley 1959": "above_ground_npp",
    "Wiegert and Evans 1964": "above_ground_npp",
    "Milner and Hughes 1968": "above_ground_npp",
    "summation method for NPP (litterfall + wood growth)": "above_ground_npp",
    "non-destructive": "above_ground_npp",
    "Valiela et al. 1975": "above_ground_npp",
    "Rey's method": "above_ground_npp",
    "Shew et al. 1981": "above_ground_npp",
    "Lomnicki et al. 1968": "above_ground_npp",
    "Wiegert and McGinnis 1975": "above_ground_npp",
    "Pierce 1983; Dickerman et al. 1986": "above_ground_npp",
    NOT_REPORTED: None,
}
BIOMASS_METHODS = frozenset({
    "end of growing season live biomass", "allometric equation", NOT_REPORTED,
    "peak live + dead biomass", "end of growing season live + dead biomass", "peak live biomass",
})
YN = frozenset({"y", "n"})
SALT_FLAT_ZERO_SHEETS = frozenset({"Height", "Biomass"})  # xml: salt-flat value "estimated as 0"

_DOI_URL = re.compile(r"https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/\S+)", re.IGNORECASE)
_YEAR = re.compile(r"(?:19|20)\d{2}")
_DEPTH = re.compile(r"0-(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Declared:
    """Hand-declared, asserted reference cases of the real release (tests pass their own)."""

    unpublished: frozenset[str] = frozenset()  # the source's own "unpublished data" parts
    unresolved_near_miss: frozenset[str] = frozenset()  # no entry; a different-year entry exists
    comma_splits: dict[str, tuple[str, ...]] = field(default_factory=dict)  # comma-joined composites


DECLARED = Declared(
    unpublished=frozenset({
        "Josselyn unpublished data",
        "Meling-López et al. unpublished data",
        "Cahoon and Lynch 1993 unpublished data",
        "Cahoon 1993 unpublished data",
        "Chmura 1997 unpublished data",
        "Cahoon and Lynch 1994 unpublished data",
        "Anisfeld 1995 unpublished data",
    }),
    # Lagomasino 2020 vs the tab's 2013, Luk 2020 vs 2021, Kemp 2020 vs 2012: different years, the data side
    # has no title to confirm they are one paper, so they are not matched. Breithaupt 2020 has no near entry.
    unresolved_near_miss=frozenset({
        "Lagomasino et al. 2020", "Luk et al. 2020", "Kemp et al. 2020", "Breithaupt et al. 2020",
    }),
    comma_splits={
        "Mahall and Park 1976, Balling and Resh 1983": ("Mahall and Park 1976", "Balling and Resh 1983"),
    },
)


def norm_ws(s: object) -> str:
    """NBSP and runs of whitespace -> one space, stripped."""
    if not isinstance(s, str):
        raise ContractError(f"expected a string, got {s!r}")
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def match_key(s: str) -> str:
    """Accent- and case-insensitive form of a normalised citation string (exact otherwise)."""
    t = unicodedata.normalize("NFKD", s)
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


def _null(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v.strip() == "")


def _text(v: object, what: str) -> str | None:
    """Null -> None; a string -> stripped; a literal NA-style string in a text cell raises."""
    if _null(v):
        return None
    if not isinstance(v, str):
        raise ContractError(f"{what}: expected text, got {v!r}")
    t = v.strip()
    if t.upper() in {"NA", "N/A", "NAN", "NULL", "NONE"}:
        raise ContractError(f"{what}: literal missing-value string {v!r}")
    return t


def _na_text(v: object, what: str) -> str | None:
    """Like ``_text`` but for the columns the xml documents as ``NA`` when absent (``*_range`` and
    ``source_review``): ``NA`` (and a blank) -> None."""
    if isinstance(v, str) and v.strip() == "NA":
        return None
    return _text(v, what)


def _number(v: object, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        raise ContractError(f"{what}: non-numeric value {v!r}")
    x = float(v)
    if not math.isfinite(x):
        raise ContractError(f"{what}: non-finite value {v!r}")
    return x


# ---------------------------------------------------------------- raw reading


def read_xml_meta(path: Path) -> dict:
    """Title, publication date, DOI, authors and use constraint of the release (FGDC xml)."""
    root = ET.parse(path).getroot()
    ci = root.find("idinfo/citation/citeinfo")
    if ci is None:
        raise ContractError(f"{path.name}: no idinfo/citation/citeinfo")

    def one(el: ET.Element, tag: str) -> str:
        found = el.findall(tag)
        if len(found) != 1 or found[0].text is None:
            raise ContractError(f"{path.name}: expected one <{tag}>, found {len(found)}")
        return found[0].text.strip()

    use = root.findall("idinfo/useconst")
    if len(use) != 1 or use[0].text is None:
        raise ContractError(f"{path.name}: expected one <useconst>")
    onlinks = [e.text.strip() for e in ci.findall("onlink") if e.text]
    return {
        "title": one(ci, "title"),
        "pubdate": one(ci, "pubdate"),
        "origins": [e.text.strip() for e in ci.findall("origin") if e.text],
        "onlink": onlinks,
        "useconst": norm_ws(use[0].text),
    }


def read_raw(path: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """The five data sheets and the Citations sheet as objects; nothing is auto-NA'd."""
    xl = pd.ExcelFile(path)
    want = [s.name for s in SHEETS] + [CITATIONS_SHEET]
    if xl.sheet_names != want:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names} != {want}")

    def sheet(name: str) -> pd.DataFrame:
        return xl.parse(name, dtype=object, keep_default_na=False, na_values=[])

    return {s.name: sheet(s.name) for s in SHEETS}, sheet(CITATIONS_SHEET)


# ---------------------------------------------------------------- citations


def _doi_of(url: str | None) -> str | None:
    if url is None:
        return None
    m = _DOI_URL.fullmatch(url.strip())
    return m.group(1).lower() if m else None


def read_citations(cit: pd.DataFrame) -> dict[str, dict]:
    """Normalised citation string -> {variants: [{full_citation, url}...], doi, key}.

    The tab holds NBSP variants of the same string (281 rows -> 278 strings): rows that are identical after
    normalisation collapse; rows with the same string but different full_citation text are KEPT as separate
    variants (one citation row each); the DOI of all variants must agree.
    """
    if list(cit.columns) != CITATION_COLS:
        raise ContractError(f"{CITATIONS_SHEET}: unexpected columns {list(cit.columns)}")
    out: dict[str, dict] = {}
    for r in cit.itertuples(index=False):
        c = _text(r.citation, "citation")
        full = _text(r.full_citation, "full_citation")
        if c is None or full is None:
            raise ContractError(f"{CITATIONS_SHEET}: blank citation or full_citation")
        c, full = norm_ws(c), norm_ws(full)
        # the Citations tab writes the literal NA where an entry has no url (the only NA-coded column)
        url = None if isinstance(r.url, str) and r.url.strip() == "NA" else _text(r.url, "url")
        e = out.setdefault(c, {"variants": [], "dois": set()})
        v = {"full_citation": full, "url": url}
        if v not in e["variants"]:
            e["variants"].append(v)
        e["dois"].add(_doi_of(url))
    for c, e in out.items():
        if len(e["dois"]) != 1:
            raise ContractError(f"{CITATIONS_SHEET}: {c!r} has variants with different DOIs {e['dois']}")
        e["doi"] = next(iter(e["dois"]))
        e["key"] = f"doi:{e['doi']}" if e["doi"] else f"ref:{c}"
    return out


def split_primary(p: str, declared: Declared) -> list[str]:
    """The cited papers in one ``source_primary`` string (``;`` split, or a declared comma split)."""
    if p in declared.comma_splits:
        return list(declared.comma_splits[p])
    parts = [norm_ws(x) for x in p.split(";")]
    if any(x == "" for x in parts):
        raise ContractError(f"source_primary {p!r}: empty part")
    return parts


# ---------------------------------------------------------------- main build


def build_tables(
    sheets: dict[str, pd.DataFrame],
    cit_df: pd.DataFrame,
    meta: dict,
    access_date: str,
    declared: Declared = DECLARED,
) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    # ---- release metadata (version, DOI, authors, use constraint are read, not hardcoded)
    if not re.fullmatch(r"\d{8}", meta["pubdate"]):
        raise ContractError(f"xml pubdate {meta['pubdate']!r} is not YYYYMMDD")
    version = f"{meta['pubdate'][:4]}-{meta['pubdate'][4:6]}-{meta['pubdate'][6:]}"
    if f"https://doi.org/{SOURCE_DOI}" not in meta["onlink"]:
        raise ContractError(f"xml onlink list does not contain https://doi.org/{SOURCE_DOI}")
    if meta["origins"][: len(XML_ORIGINS)] != XML_ORIGINS:
        raise ContractError(f"xml authors differ from the expected list: {meta['origins'][:10]}")
    if not meta["useconst"].startswith(USECONST_START):
        raise ContractError(f"xml useconst does not start with {USECONST_START!r}: {meta['useconst'][:60]!r}")

    cits = read_citations(cit_df)

    # ---- validate and clean each sheet
    frames: dict[str, pd.DataFrame] = {}
    n_source_rows = 0
    for sh in SHEETS:
        raw = sheets[sh.name]
        if list(raw.columns) != list(sh.columns):
            raise ContractError(f"{sh.name}: unexpected columns {list(raw.columns)}")
        if len(raw) != sh.n_rows:
            raise ContractError(f"{sh.name}: {len(raw)} data rows, expected {sh.n_rows}")
        d = pd.DataFrame({"row_idx": np.arange(len(raw))})
        dup = raw.astype(str).duplicated(keep=False).to_numpy()  # on the raw cells, before cleaning
        d["dup"] = dup
        d["type"] = [_text(v, f"{sh.name}.type") for v in raw["type"]]
        d["veg_type"] = [_text(v, f"{sh.name}.veg_type") for v in raw["veg_type"]]
        d["location"] = [_text(v, f"{sh.name}.location") for v in raw["location"]]
        for c in ("type", "veg_type", "location"):
            if d[c].isna().any():
                raise ContractError(f"{sh.name}.{c}: blank in rows {d.row_idx[d[c].isna()].tolist()[:5]}")
        if set(d["type"]) - TYPES:
            raise ContractError(f"{sh.name}: unknown type values {set(d['type']) - TYPES}")
        if set(d["veg_type"]) - VEG_TYPES:
            raise ContractError(f"{sh.name}: unknown veg_type values {set(d['veg_type']) - VEG_TYPES}")
        d["lat_v"] = [_number(v, f"{sh.name}.lat") for v in raw["lat"]]
        d["lon_v"] = [_number(v, f"{sh.name}.long") for v in raw["long"]]
        if not (d.lat_v.between(-90, 90).all() and d.lon_v.between(-180, 180).all()):
            raise ContractError(f"{sh.name}: coordinates outside the valid range")
        d["value"] = [_number(v, f"{sh.name}.{sh.value_col}") for v in raw[sh.value_col]]
        units = {_text(v, f"{sh.name}.units") for v in raw["units"]}
        if units != {sh.units}:
            raise ContractError(f"{sh.name}: units column {units} != declared {sh.units!r}")
        d["review"] = [
            None if (t := _na_text(v, f"{sh.name}.source_review")) is None else norm_ws(t)
            for v in raw["source_review"]
        ]
        d["primary"] = [norm_ws(v) for v in raw["source_primary"]]
        if any(p == "" for p in d["primary"]):
            raise ContractError(f"{sh.name}: blank source_primary")
        d["feher"] = [_text(v, f"{sh.name}.included_Feher_2017") for v in raw["included_Feher_2017"]]
        if set(d["feher"]) - YN:
            raise ContractError(f"{sh.name}: included_Feher_2017 values {set(d['feher']) - YN}")
        d["range"] = ([_na_text(v, f"{sh.name}.{sh.range_col}") for v in raw[sh.range_col]]
                      if sh.range_col else [None] * len(d))
        d["method"] = ([_text(v, f"{sh.name}.method") for v in raw[sh.method_col]]
                       if sh.method_col else [None] * len(d))
        if sh.method_col and d["method"].isna().any():
            raise ContractError(f"{sh.name}: blank method (the source writes 'not reported')")
        if sh.name == "Biomass" and set(d["method"]) - BIOMASS_METHODS:
            raise ContractError(f"Biomass: unknown method strings {set(d['method']) - BIOMASS_METHODS}")
        if sh.name == "Productivity" and set(d["method"]) - set(PRODUCTIVITY_SUBFIELD):
            raise ContractError(f"Productivity: unknown method strings {set(d['method']) - set(PRODUCTIVITY_SUBFIELD)}")
        depth_list = ([_text(v, f"{sh.name}.core_depth_cm") for v in raw[sh.depth_col]]
                      if sh.depth_col else [None] * len(d))
        d["depth_raw"] = depth_list
        dmin, dmax = [], []
        for v in depth_list:  # the list, not the column: pandas turns None into NaN
            if sh.depth_col is None:
                dmin.append(np.nan), dmax.append(np.nan)
            elif v is None:
                raise ContractError(f"{sh.name}: blank core_depth_cm")
            elif v == NOT_REPORTED:
                dmin.append(np.nan), dmax.append(np.nan)
            else:
                m = _DEPTH.fullmatch(v)
                if not m:
                    raise ContractError(f"{sh.name}: core_depth_cm {v!r} is not 'not reported' or '0-N'")
                dmin.append(0.0), dmax.append(float(m.group(1)))
        d["dmin"], d["dmax"] = dmin, dmax
        d["sheet"] = sh.name
        d["attribute"] = sh.attribute
        frames[sh.name] = d
        n_source_rows += len(d)

    # ---- salt-flat rule of the xml: height / biomass of salt flats were "estimated as 0"
    for name in SALT_FLAT_ZERO_SHEETS:
        d = frames[name]
        flat, zero = d["type"] == "salt flat", d["value"] == 0
        if not (flat == zero).all():
            raise ContractError(f"{name}: salt-flat rows and zero values do not coincide")
    for sh in SHEETS:
        if sh.name not in SALT_FLAT_ZERO_SHEETS and (frames[sh.name]["value"] == 0).any():
            raise ContractError(f"{sh.name}: unexpected zero values")

    allrows = pd.concat([frames[s.name] for s in SHEETS], ignore_index=True)
    for c in ("method", "range", "review", "depth_raw"):  # concat turns None into NaN; keep None
        allrows[c] = allrows[c].astype(object).where(allrows[c].notna(), None)

    # ---- references: resolve every part of every source_primary string
    primaries = sorted(set(allrows["primary"]))
    part_info: dict[str, dict] = {}  # normalised part string -> info
    unmatched_parts: set[str] = set()
    accent_matches: dict[str, str] = {}
    folded: dict[str, list[str]] = {}
    for c in cits:
        folded.setdefault(match_key(c), []).append(c)

    def find(x: str) -> str | None:
        """Exact (normalised) string first; else the one entry equal after accent/case folding."""
        if x in cits:
            return x
        cands = folded.get(match_key(x), [])
        if len(cands) > 1:
            raise ContractError(f"{x!r} matches several Citations entries after folding: {cands}")
        return cands[0] if cands else None

    for p in primaries:
        for part in split_primary(p, declared):
            if part in part_info:
                continue
            hit = find(part)
            if hit is not None:
                if hit != part:
                    accent_matches[part] = hit
                part_info[part] = {"status": "matched", "citation": hit, "key": cits[hit]["key"],
                                   "doi": cits[hit]["doi"], "variants": cits[hit]["variants"]}
            else:
                unmatched_parts.add(part)
                part_info[part] = {"status": "unmatched", "citation": part, "key": f"ref:{part}",
                                   "doi": None, "variants": []}
    declared_unmatched = declared.unpublished | declared.unresolved_near_miss
    if unmatched_parts != declared_unmatched:
        raise ContractError(
            f"unmatched citation parts differ from the declared sets: undeclared "
            f"{sorted(unmatched_parts - declared_unmatched)}, declared but matched "
            f"{sorted(declared_unmatched - unmatched_parts)}"
        )
    for part in unmatched_parts:
        part_info[part]["status"] = "unpublished" if part in declared.unpublished else "unresolved_near_miss"
    for p, parts in declared.comma_splits.items():
        if p not in set(primaries):
            raise ContractError(f"declared comma split {p!r} does not occur in the data")
        if any(part_info[x]["status"] != "matched" for x in parts):
            raise ContractError(f"declared comma split {p!r}: a part is not in the Citations tab")

    ref_parts: dict[str, list[str]] = {}  # reference_key -> parts
    primary_key: dict[str, str] = {}
    for p in primaries:
        parts = split_primary(p, declared)
        keys = [part_info[x]["key"] for x in parts]
        if len(set(keys)) != len(keys):
            raise ContractError(f"source_primary {p!r}: two parts resolve to one reference key")
        rk = "+".join(keys)
        if rk in ref_parts and ref_parts[rk] != parts:
            # two different primary strings with identical keys (e.g. a spelling variant sharing a DOI):
            # one reference, the union of their parts is carried by separate citation rows
            ref_parts[rk] = ref_parts[rk] + [x for x in parts if x not in ref_parts[rk]]
        else:
            ref_parts.setdefault(rk, parts)
        primary_key[p] = rk
    allrows["reference_key"] = allrows["primary"].map(primary_key)

    # citation rows: one per (reference, part, citation-tab variant)
    cit_rows = []
    for rk, parts in sorted(ref_parts.items()):
        for part in parts:
            info = part_info[part]
            variants = info["variants"] or [None]
            for vi, v in enumerate(variants):
                years = _YEAR.findall(info["citation"])
                cit_rows.append(
                    {
                        "source_key": SOURCE_KEY,
                        "citation_key": f"{rk}|{part}" + (f"#{vi}" if len(variants) > 1 else ""),
                        "reference_key": rk,
                        "bibliography_id": info["citation"],
                        "publication_type": None,
                        "doi": info["doi"],
                        "title": v["full_citation"] if v else None,
                        "authors": None,
                        "year": int(years[-1]) if years else None,
                        "url": v["url"] if v else None,
                        "openalex_id": None,
                        "doi_resolved": None,
                        "doi_resolution_source": None,
                    }
                )
    citation = pd.DataFrame(cit_rows)
    citation["year"] = pd.array(list(citation["year"]), dtype="Int64")
    # rule 3.4: the number of cited papers in a reference equals its citation parts (checked above per
    # string); every part has >= 1 citation row
    n_parts = sum(len(v) for v in ref_parts.values())
    if len(set(zip(citation.reference_key, citation.bibliography_id))) != n_parts:
        raise ContractError("citation rows do not cover every (reference, cited paper)")
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": sorted(ref_parts)})

    # ---- location (one per distinct source label+coordinate tuple)
    allrows["location_key"] = (
        allrows.lat_v.map(lambda x: repr(float(x))) + "|" + allrows.lon_v.map(lambda x: repr(float(x))) + "|" + allrows["type"] + "|"
        + allrows["veg_type"] + "|" + allrows["location"]
    )
    first = allrows.drop_duplicates("location_key").sort_values("location_key")
    n = len(first)
    loc = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "location_key": first.location_key,
            "site_name": first["location"],
            "source_site_id": None,
            "source_core_id": first.location_key,  # the source has no site/core id
            "latitude": first.lat_v,
            "longitude": first.lon_v,
            "position_accuracy": np.nan,
            "position_accuracy_units": None,
            "position_method": None,
            "country": None,
            "admin_division": None,
            "water_body": None,
            "habitat_type": first["type"],
            "geomorphic_setting": None,
            "bioregion": None,
            "marine_ecoregion": None,
            "salinity_class": None,
            "salinity_class_level": None,
            "vegetation_class": first["veg_type"],
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

    # ---- sample (one per data row of each sheet)
    allrows["sample_key"] = allrows["sheet"] + ":row" + allrows.row_idx.astype(str)
    depth_notes = []
    for r in allrows.itertuples(index=False):
        if r.sheet == "soil_C_density":
            depth_notes.append(f"core_depth_cm: {r.depth_raw}")
        elif r.sheet == "soil_C_accumulation":
            depth_notes.append("accumulation rate; core depth not given")
        else:
            depth_notes.append("site-average vegetation quantity; no sediment depth interval")
    sample = pd.DataFrame(
        {
            "source_key": SOURCE_KEY,
            "sample_key": allrows.sample_key,
            "source_sample_id": None,
            "depth_min_cm": allrows.dmin,
            "depth_max_cm": allrows.dmax,
            "representative_depth_min_cm": np.nan,
            "representative_depth_max_cm": np.nan,
            "depth_notes": depth_notes,
            "sampling_year": pd.array([None] * len(allrows), dtype="Int64"),
            "sampling_month": pd.array([None] * len(allrows), dtype="Int64"),
            "taxon": None,
            "taxon_rank": None,
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

    # ---- method rows (per distinct attribute, method string, range, review, included_Feher_2017)
    def mkey(r) -> str:
        return "|".join([r.attribute, r.method or "", r.range or "", r.review or "", r.feher])

    allrows["method_key"] = [mkey(r) for r in allrows.itertuples(index=False)]
    mfirst = allrows.drop_duplicates("method_key").sort_values("method_key")
    mrows = []
    for r in mfirst.itertuples(index=False):
        has_col = SHEET_BY_NAME[r.sheet].method_col is not None
        stated = has_col and r.method != NOT_REPORTED
        params = {"included_Feher_2017": r.feher, "source_review": r.review}
        if SHEET_BY_NAME[r.sheet].range_col:
            params["range"] = r.range
            params["range_column"] = SHEET_BY_NAME[r.sheet].range_col
        mrows.append(
            {
                "source_key": SOURCE_KEY,
                "method_key": r.method_key,
                "source_method_id": None,
                "attribute": r.attribute,
                "method_status": "matched" if stated else "method_id_null",
                "technique": r.method if stated else None,
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
                "params": json.dumps(params, sort_keys=True),
            }
        )
    method = pd.DataFrame(mrows)

    # ---- facts (one per row)
    rows = []
    for r in allrows.itertuples(index=False):
        sh = SHEET_BY_NAME[r.sheet]
        decl = ATTRIBUTE_DECL[r.attribute]
        flags: list[str] = []
        if r.value == 0:
            flags.append("zero_value")
            if r.sheet in SALT_FLAT_ZERO_SHEETS:
                flags.append("assigned_by_source")
        if r.value < 0:
            flags.append("negative_value")
        if r.review is not None:
            flags.append("secondary_source")
        if sh.method_col and r.method == NOT_REPORTED:
            flags.append("method_missing")
        if sh.depth_col and r.depth_raw == NOT_REPORTED:
            flags.append("depth_missing")
        if r.dup:
            flags.append("duplicate_source_row")
        if r.lat_v == round(r.lat_v) and r.lon_v == round(r.lon_v):
            flags.append("coords_integer_degree")
        sub: str | None
        if r.sheet == "Productivity":
            sub = PRODUCTIVITY_SUBFIELD[r.method]
        else:
            sub = next(iter(decl.subfields))
        rows.append(
            {
                "source_key": SOURCE_KEY,
                "fact_key": f"{r.sample_key}:{r.attribute}",
                "reference_key": r.reference_key,
                "location_key": r.location_key,
                "sample_key": r.sample_key,
                "method_key": r.method_key,
                "attribute": r.attribute,
                "measurement_type": decl.measurement_type,
                "subfield": sub,
                "value": float(r.value),
                "units_raw": sh.units,
                "sd": np.nan,
                "se": np.nan,
                "n": None,
                "flags": sorted(flags),
                "source_row_ref": f"{DATA_FILE}[{r.sheet}]#data_row={int(r.row_idx)}:{r.attribute}",
            }
        )
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")

    source = pd.DataFrame(
        [
            {
                "source_key": SOURCE_KEY,
                "name": SOURCE_NAME,
                "version": version,
                "doi": SOURCE_DOI,
                "access_date": access_date,
                "license": "No use constraints stated (FGDC useconst: " + json.dumps(meta["useconst"]) + ")",
                "notes": CITATION + f" Version = the FGDC publication date {meta['pubdate']}; the data release "
                "states no edition. The workbook is named 2023.",
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
    skips: list[dict] = []
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")

    # ---- accounting: every source row is a fact (or a listed exclusion); sets agree
    if len(fact) + len(skips) != n_source_rows:
        raise ContractError(f"facts {len(fact)} + skipped {len(skips)} != source rows {n_source_rows}")
    if set(fact.sample_key) != set(sample.sample_key):
        raise ContractError("sample rows and fact samples differ")
    if set(fact.reference_key) != set(reference.reference_key):
        raise ContractError("reference rows and fact references differ")
    if set(fact.method_key) != set(method.method_key):
        raise ContractError("method rows and fact methods differ")
    if set(fact.location_key) != set(loc.location_key):
        raise ContractError("location rows and fact locations differ")

    flag_counts: dict[str, int] = {}
    for f in fact["flags"]:
        for x in f:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    used_primary = set(allrows["primary"])
    used_review = {x for x in allrows["review"] if x is not None}
    used_cit = {part_info[x]["citation"] for p in used_primary for x in split_primary(p, declared)}
    used_cit |= {h for h in (find(x) for x in used_review) if h is not None}
    unused_citations = sorted(c for c in cits if c not in used_cit)
    by_status: dict[str, list[str]] = {}
    for part, info in part_info.items():
        by_status.setdefault(info["status"], []).append(part)
    near = {
        part: [c for c in cits if match_key(c).split(" ")[0] == match_key(part).split(" ")[0]]
        for part in sorted(unmatched_parts)
    }
    report = {
        "source_rows": n_source_rows,
        "source_rows_by_sheet": {s.name: len(frames[s.name]) for s in SHEETS},
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_attribute_subfield": {
            f"{a}|{s}": int(c)
            for (a, s), c in fact.assign(subfield=fact.subfield.fillna("NULL")).groupby(["attribute", "subfield"]).size().items()
        },
        "rows_excluded": len(skips),
        "samples": len(sample),
        "locations": len(loc),
        "locations_by_type": loc.habitat_type.value_counts().sort_index().to_dict(),
        "methods": len(method),
        "references": len(reference),
        "citations": len(citation),
        "source_version": version,
        "source_pubdate": meta["pubdate"],
        "useconst": meta["useconst"],
        "flag_counts": flag_counts,
        "flag_counts_by_attribute": {
            a: {fl: int(sum(fl in x for x in g["flags"])) for fl in sorted({y for x in g["flags"] for y in x})}
            for a, g in fact.groupby("attribute")
        },
        "citations_tab_rows": len(cit_df),
        "citations_tab_distinct_strings": len(cits),
        "citations_tab_strings_with_valid_doi": sum(1 for e in cits.values() if e["doi"]),
        "citations_tab_strings_with_non_doi_url_on_doi_org": sorted(
            c for c, e in cits.items()
            if not e["doi"] and any(v["url"] and "doi.org" in v["url"] for v in e["variants"])
        ),
        "citations_tab_strings_with_several_full_citation_texts": sorted(
            c for c, e in cits.items() if len(e["variants"]) > 1
        ),
        "citations_tab_strings_sharing_a_doi": sorted(
            c for c, e in cits.items()
            if e["doi"] and sum(1 for o in cits.values() if o["doi"] == e["doi"]) > 1
        ),
        "citations_tab_strings_unused_by_any_row": unused_citations,
        "citations_tab_strings_equal_after_accent_folding": sorted(
            c for c in cits if len(folded[match_key(c)]) > 1
        ),
        "distinct_source_primary_strings": len(primaries),
        "composite_source_primary_strings": sorted(p for p in primaries if len(split_primary(p, declared)) > 1),
        "cited_papers": len(part_info),
        "cited_papers_matched": len(by_status.get("matched", [])),
        "cited_papers_matched_via_accent_folding": accent_matches,
        "cited_papers_matched_with_doi": sum(
            1 for i in part_info.values() if i["status"] == "matched" and i["doi"]
        ),
        "cited_papers_unpublished": sorted(by_status.get("unpublished", [])),
        "cited_papers_unresolved_near_miss": sorted(by_status.get("unresolved_near_miss", [])),
        "near_misses_same_first_word_in_citations_tab": near,
        "source_review_strings": sorted(used_review),
        "source_review_strings_not_in_citations_tab": sorted(x for x in used_review if find(x) is None),
        "rows_with_source_review": int(allrows["review"].notna().sum()),
        "duplicate_source_rows_flagged": int(allrows["dup"].sum()),
    }
    if report["source_review_strings_not_in_citations_tab"]:
        raise ContractError(
            f"source_review strings not in the Citations tab: {report['source_review_strings_not_in_citations_tab']}"
        )
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
    sheets, cit = read_raw(args.raw_dir / DATA_FILE)
    meta = read_xml_meta(args.raw_dir / XML_FILE)
    tables, skips, report = build_tables(sheets, cit, meta, args.access_date)
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
