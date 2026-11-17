#!/usr/bin/env python3
"""Mouw et al. global ocean POC flux compilation (sediment traps and 234Th) -> compilation-contract tables.

Mouw, C.B., Barnett, A., McKinley, G.A., Gloege, L., Pilcher, D. (2016): Global ocean particulate organic carbon flux merged with satellite
parameters. PANGAEA, doi:10.1594/PANGAEA.855600; the flux dataset is doi:10.1594/PANGAEA.855594 (supplement to ESSD 8(2), 531-541,
doi:10.5194/essd-8-531-2016). CC-BY-3.0. Raw files (this directory): ``datasets/GO_flux.tab`` (a PANGAEA tab file: a comment header ending in
``*/``, then one tab-separated table) and ``summary.txt``. The other five ``datasets/*.tab`` files (chlorophyll, microplankton, mixed layer,
SST) are satellite and climatology products matched to the trap locations. They are never loaded and not even opened (INTEGRATION_PLAN 3.5:
context only from values measured beside the fact).

Grain: one fact per non-blank numeric flux cell of the seven columns below (every value is a number, and any non-numeric text raises). One
sample per source row (a trap deployment or 234Th station). Values are as in the file.

Column -> attribute (type/subfield), all ``mg/m**2/day`` (the header unit, asserted), with ``sd`` from the paired std-dev column:
  Flux tot            mouw_total_mass_flux   C6 total_mass_flux                     sd = ``Flux std dev``
  POC flux            mouw_poc_flux          C6 poc_flux                            sd = ``POC flux std dev``
  PON flux            mouw_pon_flux          C6 pon_flux                            sd = ``PON flux std dev``
  CaCO3 flux          mouw_caco3_flux        C6 biogenic_silica_carbonate_flux      sd = ``CaCO3 flux std dev``
  PSi flux            mouw_psi_flux          C6 biogenic_silica_carbonate_flux      sd = ``PSi flux std dev``
  PSiO2 flux          mouw_psio2_flux        C6 biogenic_silica_carbonate_flux
  PSi(OH)4 flux       mouw_psioh4_flux       C6 biogenic_silica_carbonate_flux
Not loaded (phase 2 ``other`` analytes, or no plan decision; counted per column in the report): TC flux, PIC, POP, PAl, Chl, Pheop, Fe,
Mn, Ba, Detrital and Ti, with their std-dev columns. A std-dev without a flux value never occurs (asserted). The derived ``Bathy depth``
(ETOPO1 gridded bathymetry) is not loaded.

Context:
  * ``Depth water`` (trap deployment depth) goes to ``sample.sample_depth_m``. ``Elevation`` (total water depth, negative) goes to
    ``location.water_depth_m`` with the sign flipped (positive down; the raw value is recoverable via source_row_ref), and is blank on rows
    without it. location_key = latitude | longitude | elevation, as written. The file's own ``unique location identifier`` is not constant
    in its coordinates and depth, so it is not used.
  * ``Date/Time (Deployed)`` gives sampling_year and sampling_month (rows without one get ``year_missing``). The deployment duration varies
    per row and method rows are not per row, so it goes to ``sample.depth_notes`` as text ``duration_days=<as written>;
    retrieved=<as written>`` (zero durations occur and are kept).
  * Longitudes are within [-180, 180] (asserted; 180.0 is valid).
  * Habitat is Water column from the dataset's scope (open-ocean traps and 234Th), with ``habitat_from_scope`` on every fact.
  * Method: one row per (attribute, ``Type``, ``Sediment trap type``). technique is ``<Type>; <trap type>`` in the file's spelling
    (``sediment_trap`` or ``th234``; trap type ``unknown`` kept).

References (INTEGRATION_PLAN 1.3 and 5.14): reference_key is ``mouw_id:<ID>`` (the file's ``ID (Reference identifier)``, 43 values). The
``Reference`` text is on every row of an ID, EXCEPT IDs 2 and 14. One citation row per cited paper: ID 7 cites two (an explicit split,
asserted), and strings are carried as written (``bibliography_id``). A string like ``Surname et al., YYYY`` or ``Surname and Surname, YYYY``
gets a DOI and URL only where the header's ``Related to:`` list holds exactly one citation with that first-author surname and year (and
second author). Strings naming a data archive or a personal communication are never matched, and unmatched strings are listed in the report.
ID 14 is BATS by inference (one location, 31.613 N 64.314 W, 1988-12-16 to 2011-12-07, which the extract asserts, and Table 2 of the ESSD
paper gives http://bats.bios.edu). The citation is that web page and the facts carry ``reference_inferred``. ID 2 has no resolvable
reference: a ``reference_dim`` row only, no citation, and facts carry ``reference_unresolved``. Facts whose reference string says
``pers. comm.`` carry ``pers_comm``.

Flags: habitat_from_scope (all), reference_inferred, reference_unresolved, pers_comm, year_missing, zero_value, negative_value.

Provenance: ``source_row_ref`` = ``datasets/GO_flux.tab#data_row=<0-based row>:<attribute>`` (the row of the table below the ``*/`` line,
header excluded).

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from _g4_common import is_blank, norm_ws  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "mouw-carbon"
SOURCE_NAME = "Mouw et al. global ocean particulate organic carbon flux (sediment traps and 234Th)"
SOURCE_DOI = "10.1594/PANGAEA.855594"
SOURCE_VERSION = "PANGAEA 855594 (DOI registered 2016-01-11; no version number)"
SOURCE_LICENSE = "CC-BY-3.0"
DATA_REL = "datasets/GO_flux.tab"
CITATION = (
    "Mouw, C.B., Barnett, A., McKinley, G.A., Gloege, L., Pilcher, D. (2016): Global ocean particulate organic carbon flux "
    "merged with satellite parameters [dataset publication series]. PANGAEA, https://doi.org/10.1594/PANGAEA.855600; flux "
    "dataset https://doi.org/10.1594/PANGAEA.855594 (supplement to Earth System Science Data 8(2), 531-541, "
    "https://doi.org/10.5194/essd-8-531-2016)"
)
UNIT = "mg/m**2/day"
HEADER_COLUMNS = [
    "ID (Reference identifier)", "ID (Unique location identifier)", "Type (Data type)", "Latitude", "Longitude",
    "Type (Sediment trap type)", "Elevation [m a.s.l.] (Total water depth)", "Bathy depth [m] (ETOPO1 bathymetry)",
    "Depth water [m] (Sediment trap deployment depth)", "Date/Time (Deployed)", "Date/time end (Retrieved)",
    "Duration [days]", "Area [m**2] (Surface area of trap)", "Flux tot [mg/m**2/day]", "Flux std dev [±]",
    "TC flux [mg/m**2/day]", "TC flux std dev [±]", "POC flux [mg/m**2/day]", "POC flux std dev [±]",
    "PIC flux [mg/m**2/day]", "PIC flux std dev [±]", "PON flux [mg/m**2/day]", "PON flux std dev [±]",
    "POP flux [mg/m**2/day]", "POP flux std dev [±]", "PSi flux [mg/m**2/day]", "PSi flux std dev [±]",
    "PSiO2 flux [mg/m**2/day]", "PSi(OH)4 flux [mg/m**2/day]", "PAl [mg/m**2/day]", "PAl std dev [±]",
    "Chl flux [mg/m**2/day]", "Pheop flux [µg/m**2/day]", "CaCO3 flux [mg/m**2/day]", "CaCO3 flux std dev [±]",
    "Fe flux [mg/m**2/day]", "Mn flux [µg/m**2/day]", "Ba flux [µg/m**2/day]", "Detrital flux [mg/m**2/day]",
    "Ti flux [µg/m**2/day]", "Reference",
]
C_REF, C_LOC, C_TYPE, C_LAT, C_LON, C_TRAP, C_ELEV, C_BATHY, C_DEPTH, C_START, C_END, C_DUR, C_AREA = HEADER_COLUMNS[:13]
C_REFTEXT = HEADER_COLUMNS[-1]
# value column -> (attribute, sd column or None)
FACT_COLUMNS: dict[str, tuple[str, str | None]] = {
    "Flux tot [mg/m**2/day]": ("mouw_total_mass_flux", "Flux std dev [±]"),
    "POC flux [mg/m**2/day]": ("mouw_poc_flux", "POC flux std dev [±]"),
    "PON flux [mg/m**2/day]": ("mouw_pon_flux", "PON flux std dev [±]"),
    "CaCO3 flux [mg/m**2/day]": ("mouw_caco3_flux", "CaCO3 flux std dev [±]"),
    "PSi flux [mg/m**2/day]": ("mouw_psi_flux", "PSi flux std dev [±]"),
    "PSiO2 flux [mg/m**2/day]": ("mouw_psio2_flux", None),
    "PSi(OH)4 flux [mg/m**2/day]": ("mouw_psioh4_flux", None),
}
SD_COLUMNS = {sd for _, sd in FACT_COLUMNS.values() if sd}
NOT_LOADED = [c for c in HEADER_COLUMNS[13:-1] if c not in FACT_COLUMNS and c not in SD_COLUMNS]
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}:\d{2}")

# the one reference string that cites two papers (INTEGRATION_PLAN 3.4: the split must reproduce the number cited)
REF_SPLIT = {7: ["Collier and Dymond, 1994, BCO-DMO", "Honjo and Dymond, 1994, BCO-DMO"]}
REF_SPLIT_RAW = {7: "Collier and Dymond, 1994, BCO-DMO, Honjo and Dymond, 1994, BCO-DMO,"}
UNRESOLVED_ID = 2  # no Reference text, no Table 2 entry
BATS_ID = 14  # no Reference text; BATS by inference (location + dates), INTEGRATION_PLAN 1.3
BATS_CITATION = "The Bermuda Atlantic Time-series Study (BATS), http://bats.bios.edu (Table 2 of Mouw et al. 2016)"
BATS_URL = "http://bats.bios.edu"
BATS_LOCATION = ("31.6130", "-64.3140")
BATS_DATES = ("1988-12-16", "2011-12-07")
_ARCHIVE_WORDS = ("BCO-DMO", "NOAA", "JAMSTEC", "JGOFS", "CARIACO", "pers. comm")
_REF_SIMPLE = re.compile(r"^(?P<s1>[A-Za-z\-]+)(?: et all?\.?| and (?P<s2>[A-Za-z]+))?,? (?P<y>\d{4})$")
_HDR_CIT = re.compile(r"^\t?(?P<authors>[^()]+?) \((?P<y>\d{4})\): .*?(?P<doi>https://doi\.org/\S+?)(?:,\s*)?$")


def _ascii(s: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold()


def read_tab(path: Path) -> tuple[list[str], pd.DataFrame]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    ends = [i for i, ln in enumerate(lines) if ln.startswith("*/")]
    if len(ends) != 1:
        raise ContractError(f"{path.name}: expected one '*/' header end, found {len(ends)}")
    header = lines[: ends[0]]
    body = "\n".join(lines[ends[0] + 1 :])
    df = pd.read_csv(io.StringIO(body), sep="\t", dtype=object, keep_default_na=False, na_values=[], quoting=3)
    if list(df.columns) != HEADER_COLUMNS:
        raise ContractError(f"{path.name}: unexpected columns {list(df.columns)}")
    return header, df


def header_citations(header: list[str]) -> dict[tuple[str, str], list[dict]]:
    """(first-author surname, year) -> header 'Related to:' citations with a DOI."""
    start = next(i for i, ln in enumerate(header) if ln.startswith("Related to:"))
    end = next(i for i, ln in enumerate(header) if ln.startswith("Further details:") or ln.startswith("Coverage:"))
    out: dict[tuple[str, str], list[dict]] = {}
    for ln in header[start:end]:
        ln = ln.removeprefix("Related to:")
        m = _HDR_CIT.match(ln)
        if not m:
            continue
        authors = [a.strip() for a in m.group("authors").split(";")]
        sur = _ascii(authors[0].split(",")[0])
        sur2 = _ascii(authors[1].split(",")[0]) if len(authors) > 1 else None
        out.setdefault((sur, m.group("y")), []).append({"surname2": sur2, "doi": m.group("doi").removeprefix("https://doi.org/"),
                                                         "url": m.group("doi")})
    return out


def match_doi(text: str, hdr: dict[tuple[str, str], list[dict]]) -> dict | None:
    if any(w in text for w in _ARCHIVE_WORDS):
        return None
    m = _REF_SIMPLE.match(text)
    if not m:
        return None
    cand = hdr.get((_ascii(m.group("s1")), m.group("y")), [])
    s2 = _ascii(m.group("s2")) if m.group("s2") else None
    cand = [c for c in cand if s2 is None or c["surname2"] == s2]
    if len(cand) > 1:
        raise ContractError(f"reference {text!r} matches {len(cand)} header citations")
    return cand[0] if cand else None


def _num(v: str, what: str) -> float:
    if not _NUM_RE.fullmatch(v):
        raise ContractError(f"{what}: non-numeric value {v!r}")
    x = float(v)
    if not np.isfinite(x):
        raise ContractError(f"{what}: non-finite value {v!r}")
    return x


def build_tables(header: list[str], raw: pd.DataFrame, access_date: str) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    if list(raw.columns) != HEADER_COLUMNS:
        raise ContractError("unexpected columns")
    n_rows = len(raw)
    for c, (attr, _sd) in FACT_COLUMNS.items():
        m = re.search(r"\[(.+)\]", c)
        if not m or m.group(1) != UNIT or UNIT not in ATTRIBUTE_DECL[attr].units:
            raise ContractError(f"{c}: unit")
    ids = [int(_num(v, "reference id")) for v in raw[C_REF]]
    refs_text = [norm_ws(v) for v in raw[C_REFTEXT]]
    # reference text: constant per ID, blank only for the two named IDs
    ref_of: dict[int, str] = {}
    for i, t in zip(ids, refs_text):
        if i in ref_of and ref_of[i] != t:
            raise ContractError(f"reference id {i}: two different reference texts")
        ref_of[i] = t
    blank_ids = {i for i, t in ref_of.items() if t == ""}
    if blank_ids != {UNRESOLVED_ID, BATS_ID}:
        raise ContractError(f"IDs without a Reference text {sorted(blank_ids)} differ from {[UNRESOLVED_ID, BATS_ID]}")
    # the BATS inference premise
    bats = raw[[i == BATS_ID for i in ids]]
    if set(zip(bats[C_LAT], bats[C_LON])) != {BATS_LOCATION}:
        raise ContractError("ID 14 is not a single location 31.613 N 64.314 W")
    starts = [_DATE_RE.fullmatch(v) for v in bats[C_START]]
    if any(m is None for m in starts):
        raise ContractError("ID 14: a deployment date is blank or malformed")
    ds = sorted("-".join(m.groups()) for m in starts)  # type: ignore[union-attr]
    if not (BATS_DATES[0] == ds[0] and ds[-1] <= BATS_DATES[1]):
        raise ContractError(f"ID 14 dates {ds[0]}..{ds[-1]} do not fit Table 2's {BATS_DATES}")

    lat = [_num(v, "latitude") for v in raw[C_LAT]]
    lon = [_num(v, "longitude") for v in raw[C_LON]]
    if any(not -90 <= a <= 90 for a in lat) or any(not -180 <= b <= 180 for b in lon):
        raise ContractError("coordinates outside the valid range")
    elev = [None if is_blank(v) else _num(v, "elevation") for v in raw[C_ELEV]]
    if any(e is not None and e >= 0 for e in elev):
        raise ContractError("an elevation that is not below the surface")
    tdepth = [_num(v, "trap depth") for v in raw[C_DEPTH]]
    if any(d < 0 for d in tdepth):
        raise ContractError("negative trap depth")
    year, month = [], []
    for v in raw[C_START]:
        if v == "":
            year.append(None), month.append(None)
            continue
        m = _DATE_RE.fullmatch(v)
        if not m:
            raise ContractError(f"deployment date {v!r}")
        year.append(int(m.group(1))), month.append(int(m.group(2)))
    for v in raw[C_END]:
        if not _DATE_RE.fullmatch(v):
            raise ContractError(f"retrieval date {v!r}")
    for v in raw[C_DUR]:
        _num(v, "duration")
    trap_types = [norm_ws(v) for v in raw[C_TRAP]]
    types = [norm_ws(v) for v in raw[C_TYPE]]
    if set(types) - {"sediment_trap", "th234"}:
        raise ContractError(f"unknown data type {set(types)}")
    if any(t == "" for t in trap_types):
        raise ContractError("blank trap type")

    # ---- facts
    methods: dict[str, dict] = {}
    rows: list[dict] = []
    n_cells = {a: 0 for a, _ in FACT_COLUMNS.values()}
    sd_without_value = 0
    for col, (attr, sdcol) in FACT_COLUMNS.items():
        decl = ATTRIBUTE_DECL[attr]
        for i in range(n_rows):
            v = raw.iat[i, HEADER_COLUMNS.index(col)]
            sdv = raw.iat[i, HEADER_COLUMNS.index(sdcol)] if sdcol else ""
            if v == "":
                if sdv != "":
                    sd_without_value += 1
                continue
            x = _num(v, f"{col} row {i}")
            n_cells[attr] += 1
            fl = ["habitat_from_scope"]
            if year[i] is None:
                fl.append("year_missing")
            if x == 0:
                fl.append("zero_value")
            if x < 0:
                fl.append("negative_value")
            if ids[i] == BATS_ID:
                fl.append("reference_inferred")
            if ids[i] == UNRESOLVED_ID:
                fl.append("reference_unresolved")
            if "pers. comm" in refs_text[i]:
                fl.append("pers_comm")
            mk = f"{attr}|{types[i]}|{trap_types[i]}"
            if mk not in methods:
                methods[mk] = {
                    "source_key": SOURCE_KEY, "method_key": mk, "source_method_id": None, "attribute": attr,
                    "method_status": "matched", "technique": f"{types[i]}; {trap_types[i]}", "coring_method": None,
                    "carbon_type": None, "carbon_measured_or_modeled": None, "fraction_carbon_method": None,
                    "carbonates_removed": None, "carbonate_removal_method": None, "conversion_applied": None,
                    "in_situ_ex_situ": None, "light_dark": None, "incubation_type": None, "experimental_status": None,
                    "params": json.dumps({"data_type": types[i], "sediment_trap_type": trap_types[i]}, sort_keys=True),
                }
            rows.append({
                "source_key": SOURCE_KEY, "fact_key": f"row{i}:{attr}", "reference_key": f"mouw_id:{ids[i]}",
                "location_key": f"{raw.iat[i, 3]}|{raw.iat[i, 4]}|{raw.iat[i, 6]}", "sample_key": f"row{i}",
                "method_key": mk, "attribute": attr, "measurement_type": decl.measurement_type,
                "subfield": next(iter(decl.subfields)), "value": x, "units_raw": UNIT,
                "sd": np.nan if sdv == "" else _num(sdv, f"{sdcol} row {i}"), "se": np.nan, "n": None,
                "flags": sorted(fl), "source_row_ref": f"{DATA_REL}#data_row={i}:{attr}",
            })
    if sd_without_value:
        raise ContractError(f"{sd_without_value} std-dev cells without a flux value")
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array([None] * len(fact), dtype="Int64")
    if fact.fact_key.duplicated().any():
        raise ContractError("duplicate fact_key")
    n_numeric = sum(n_cells.values())
    if len(fact) != n_numeric:
        raise ContractError(f"facts {len(fact)} != numeric cells {n_numeric}")

    # ---- samples / locations (only those facts use)
    used_rows = sorted({int(k[3:].split(":")[0]) for k in fact.fact_key})
    sample = pd.DataFrame({
        "source_key": SOURCE_KEY, "sample_key": [f"row{i}" for i in used_rows], "source_sample_id": None,
        "depth_min_cm": np.nan, "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan,
        "representative_depth_max_cm": np.nan,
        "depth_notes": [f"duration_days={raw.iat[i, 11]}; retrieved={raw.iat[i, 10]}" for i in used_rows],
        "sampling_year": pd.array([year[i] for i in used_rows], dtype="Int64"),
        "sampling_month": pd.array([month[i] for i in used_rows], dtype="Int64"),
        "taxon": None, "taxon_rank": None, "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None,
        "photosynthetic_sediment": None, "sample_depth_m": [tdepth[i] for i in used_rows], "water_temperature_c": np.nan,
        "water_salinity": np.nan, "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
    })
    first: dict[str, int] = {}
    for i in used_rows:
        first.setdefault(f"{raw.iat[i, 3]}|{raw.iat[i, 4]}|{raw.iat[i, 6]}", i)
    lkeys = sorted(first)
    n = len(lkeys)
    loc = pd.DataFrame({
        "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": None, "source_site_id": None,
        "source_core_id": lkeys, "latitude": [lat[first[k]] for k in lkeys], "longitude": [lon[first[k]] for k in lkeys],
        "position_accuracy": np.nan, "position_accuracy_units": None, "position_method": None, "country": None,
        "admin_division": None, "water_body": None, "habitat_type": "water column", "geomorphic_setting": None,
        "bioregion": None, "marine_ecoregion": None, "salinity_class": None, "salinity_class_level": None,
        "vegetation_class": None, "vegetation_class_level": None, "tidal_position": None, "tidal_position_level": None,
        "modification_status": [[] for _ in range(n)], "dominant_species": [[] for _ in range(n)],
        "elevation": np.nan, "elevation_units": None, "elevation_datum": None, "elevation_method": None,
        "water_depth_m": [np.nan if elev[first[k]] is None else -elev[first[k]] for k in lkeys],  # type: ignore[operator]
        "distance_to_shore_km": np.nan,
    })

    # ---- references and citations
    used_ids = sorted({int(k.split(":")[1]) for k in fact.reference_key})
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": [f"mouw_id:{i}" for i in used_ids]})
    hdr = header_citations(header)
    cit_rows = []
    unmatched: list[str] = []
    for i in used_ids:
        if i == UNRESOLVED_ID:
            continue
        if i == BATS_ID:
            cit_rows.append((i, 0, BATS_CITATION, None, BATS_URL, None))
            continue
        text = ref_of[i]
        if i in REF_SPLIT:
            if text != REF_SPLIT_RAW[i]:
                raise ContractError(f"ID {i}: reference text {text!r} is not the expected two-paper string")
            parts = REF_SPLIT[i]
        else:
            parts = [text]
        for k, p in enumerate(parts):
            hit = match_doi(p, hdr)
            if hit is None:
                unmatched.append(p)
            ys = re.findall(r"(?:19|20)\d\d", p)
            cit_rows.append((i, k, p, int(ys[0]) if ys else None, hit["url"] if hit else None, hit["doi"] if hit else None))
    citation = pd.DataFrame({
        "source_key": SOURCE_KEY, "citation_key": [f"mouw_id:{i}|{k}" for i, k, *_ in cit_rows],
        "reference_key": [f"mouw_id:{i}" for i, *_ in cit_rows], "bibliography_id": [c[2] for c in cit_rows],
        "publication_type": None, "doi": [c[5] for c in cit_rows], "title": None, "authors": None,
        "year": pd.array([c[3] for c in cit_rows], dtype="Int64"), "url": [c[4] for c in cit_rows],
        "openalex_id": None, "doi_resolved": None, "doi_resolution_source": None,
    })
    method = pd.DataFrame(sorted(methods.values(), key=lambda m: m["method_key"]))
    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + " Version and license are from the PANGAEA pages (the file states the license, no version).",
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
    nl = {c: int(sum(1 for v in raw[c] if v != "")) for c in NOT_LOADED}
    report = {
        "source_rows": n_rows,
        "source_rows_with_a_fact": len(used_rows),
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"]).size().items()
        },
        "numeric_cells_by_attribute": n_cells,
        "facts_with_sd": int(fact.sd.notna().sum()),
        "skipped": 0,
        "samples": len(sample),
        "locations": len(loc),
        "locations_without_water_depth": int(loc.water_depth_m.isna().sum()),
        "methods": len(method),
        "references": len(reference),
        "citations": len(citation),
        "citations_with_doi": int(citation.doi.notna().sum()),
        "unmatched_reference_strings": sorted(set(unmatched)),
        "rows_by_reference_id": {int(k): int(v) for k, v in pd.Series(ids).value_counts().sort_index().items()},
        "flag_counts": flag_counts,
        "samples_without_year": int(sample.sampling_year.isna().sum()),
        "durations_le_zero_rows": int(sum(1 for v in raw[C_DUR] if float(v) <= 0)),
        "not_loaded_columns_nonblank_cells": nl,
        "bathy_depth_nonblank_cells_not_loaded": int(sum(1 for v in raw[C_BATHY] if v != "")),
        "area_nonblank_cells_not_loaded": int(sum(1 for v in raw[C_AREA] if v != "")),
        "data_types": dict(pd.Series(types).value_counts()),
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
    header, raw = read_tab(args.raw_dir / DATA_REL)
    tables, skips, report = build_tables(header, raw, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
