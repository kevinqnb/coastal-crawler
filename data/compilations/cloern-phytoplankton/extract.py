#!/usr/bin/env python3
"""Cloern et al. (2014) supplement Table S1 -> compilation-contract tables.

Cloern, J.E., Foster, S.Q., Kleckner, A.E. (2014): Phytoplankton primary production in the world's estuarine-coastal
ecosystems. Biogeosciences 11, 2477-2501, doi:10.5194/bg-11-2477-2014; supplement
doi:10.5194/bg-11-2477-2014-supplement (CC BY 3.0). Raw file (this directory): ``bg-11-2477-2014-supplement.pdf``
(19 PDF pages): page 1 front matter, pages 2-7 Table S1 (a ruled table, read with pdfplumber), pages 8-19 the
supplement's own reference list (144 paragraphs, no DOIs in most).

Grain: ONE FACT PER TABLE ROW (ecosystem x measure x method), attribute ``cloern_appp_median`` (B8
``depth_integrated_primary_production``): the median annual phytoplankton primary production (APPP) of the ``n`` annual
measurements the authors pooled for that ecosystem and measure type. ``n`` is the fact's ``n``; the fact is flagged
``source_median`` on every row (the column header says "Median APPP"; for ``n`` = 1 the median is the one measurement).
The measure (GPP gross / NPP net / U unreported) and the method (14C, O2, 13C, O2 & 14C) are the method row (``technique``
= the raw method label, ``params.measure`` the raw measure code with its meaning); they are not separate attributes.
Declared unit: ``g C m-2 yr-1`` = the column header ``g C m-2 yr-1`` with the PDF's rendering artifacts removed (soft
hyphen U+00AD and the U+2010 hyphen that the PDF emits after the hyphen-minus: ``m-<U+00AD><U+2010>2`` is ``m-2``); the
extract asserts the header cell reduces to exactly that string.

Raw-file anomalies (all asserted, none silent): (1) the table has 163 data rows, not the plan's ~250; (2) the ``n`` column
sums to 1,147, not the 1,148 of the table caption and abstract text ("computed from 1148 measurements made in 131
estuarine-coastal ecosystems"): the source is one short of its own caption, verified by two independent readings of the
page (pdfplumber table cells, and the word-position tally in ``verify_table.py``); the extract asserts 1,147 and carries
the caption's 1,148 only in the report; (3) 131 distinct ecosystem names, counting ``Chesapeake Bay 1960s`` (a separate
name in the source) apart from ``Chesapeake Bay``; (4) one negative value (Scheldt Estuary NPP, -105; the sign is printed in
the source): kept, flagged ``negative_value``.

Locations: one per ecosystem name (the source has no coordinates and none are guessed): ``site_name`` = ecosystem,
``country`` = the source's COUNTRY string (whitespace-normalised, e.g. ``DENMARK/ SWEDEN``), ``bioregion`` = the source's
Region (``Baltic``, ``Europe``...: continental labels, not Short et al.). ``habitat_type`` = ``water column`` for every row
(label map: Water column), a habitat from scope, so every fact carries ``habitat_from_scope``.

References: the cell holds in-text ``(Author, year)`` citations, some ``(A) cited in (B)`` (the first is the study, the
others are the secondary source: flag ``cited_secondary``) and some personal communications (text before/after the
citation; flag ``pers_comm``; the communication itself is not a paper, so it has no citation row). Each cited item is matched
to the supplement's own reference list by an automatic exact rule (first-author surname + year, accent- and case-folded,
plus structural checks) and, where that is not unique or not right, by the explicit ``CROSSWALK`` below; any item that
matches neither raises, and every CROSSWALK entry must be used. ``reference_key`` of a row = ``+``-joined keys of the
cited papers (one ``citation`` row per paper, ``<key>|<k>``); a paper's key is ``doi:<bare doi>`` when its list entry prints a
DOI, else ``ref:<authors part of the entry> <year>``. The list entry has no separable title: ``citation.title`` holds
the entry text after the first ``: `` (title and publication details as printed), ``citation.authors`` the text before it.
List entries no table row cites are not loaded (reported). The parsed list (``references_parsed.csv``), the crosswalk
(``reference_crosswalk.csv``) and the table rows (``table_s1_rows.csv``) are checked in beside this file for review.

Provenance: ``source_row_ref`` = ``<file>#pdf_page=<1-based PDF page>:table_row=<0-based data row on that page>:cloern_appp_median``.

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "cloern-phytoplankton"
SOURCE_NAME = "Cloern et al. 2014: annual phytoplankton primary production in estuarine-coastal ecosystems (Table S1)"
SOURCE_DOI = "10.5194/bg-11-2477-2014-supplement"
SOURCE_VERSION = "Biogeosciences 11 (2014) supplement; no version number exists"
SOURCE_LICENSE = "CC BY 3.0"  # printed on the supplement's first page
DATA_FILE = "bg-11-2477-2014-supplement.pdf"
CITATION = (
    "Cloern, J.E., Foster, S.Q., Kleckner, A.E. (2014): Phytoplankton primary production in the world's "
    "estuarine-coastal ecosystems. Biogeosciences 11, 2477-2501, doi:10.5194/bg-11-2477-2014; supplement "
    "doi:10.5194/bg-11-2477-2014-supplement"
)
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()  # every APPP cell is a fact
check_skip_reasons(ALLOWED_SKIP_REASONS)

ATTR = "cloern_appp_median"
UNITS = "g C m-2 yr-1"
UNITS_HEADER_RAW = "Median\nAPPP\ng C m-\xad‐2 yr-\xad‐1"  # exactly as pdfplumber reads the header cell
HEADER = ["Ecosystem", "Country", "Region", "Measure", "Method", UNITS_HEADER_RAW, "n", "Reference"]
TABLE_PAGES = range(1, 7)  # 0-based pdfplumber page indexes holding Table S1 (PDF pages 2-7)
REF_PAGES = range(7, 19)  # PDF pages 8-19: the reference list
PARAGRAPH_GAP_PT = 20.0  # vertical distance between line tops that starts a new reference paragraph (lines are ~14 pt)
REF_LEFT_X0 = 126  # x0 of reference-list text lines; page-number lines sit elsewhere
EXPECTED_N_PDF_PAGES = 19
EXPECTED_ROWS = 163  # data rows of Table S1, counted twice (table cells and word positions; see verify_table.py)
EXPECTED_ECOSYSTEMS = 131  # the caption says 131; distinct ecosystem names here, "Chesapeake Bay 1960s" counted apart
EXPECTED_N_SUM = 1147  # sum of the n column as printed; the caption says 1148 (source discrepancy, see docstring)
CAPTION_N_SUM = 1148
EXPECTED_REF_PARAGRAPHS = 144
MEASURES = {"GPP": "gross primary production", "NPP": "net primary production", "U": "unreported"}
METHODS = frozenset({"14C", "O2", "13C", "O2 & 14C"})
NEGATIVE_ROWS = frozenset({("Scheldt Estuary", "NPP")})  # the one printed negative APPP
HABITAT_RAW = "water column"

# in-text item (whitespace-normalised, e.g. "Wassman et al., 1986") -> the exact authors-part of the list entry plus year,
# "<authors part> <year>". Needed where the automatic first-author+year rule is not unique or not right.
CROSSWALK: dict[str, str] = {
    # in-text cites one author, the list entry has several (the only list entry with that surname and year)
    "Sandoval-Rojo, 1988": "Sandoval-Rojo, L. C., F. J. F. Verdugo, U. Zaragoza-Araujo, J. W. Day, A. Estrada-Mercado 1988",
    "Tundisi, 1973": "Tundisi, J., Tundisi, T. M., Kutner, M.B. 1973",
    "Boynton, 1982": "Boynton, W. R., W. M. Kemp, and C. W. Keefe. 1982",  # the list's only Boynton 1982 (not Boynton 1973)
    # in-text "et al." / "and" forms against three Stockner entries
    "Stockner et al., 1979": "Stockner, J. G., Cliff, D. D., and Shortreed, K. R. S. 1979",
    "Stockner and Cliff, 1979": "Stockner, J. G., and Cliff, D. D. 1979",
    "Stockner, 1977": "Stockner, J. G., Cliff, D.D., and Buchanan, D. B. 1977",
    # in-text drops the period/spacing of the entry's "Jr."
    "Randall and Day Jr, 1987": "Randall, J. M., and Day J. W. Jr. 1987",
}

# ---------------------------------------------------------------- text helpers


def clean(s: str) -> str:
    """Remove the PDF's hyphen artifacts and normalise whitespace (newlines -> spaces)."""
    s = s.replace("-\xad‐", "-").replace("\xad", "").replace("‐", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def fold(s: str) -> str:
    """Case- and accent-folded form used only for matching surnames."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("'", "").replace("-", " ")


# ---------------------------------------------------------------- raw reading


def read_table(pdf: pdfplumber.PDF) -> list[dict]:
    rows: list[dict] = []
    for pi in TABLE_PAGES:
        tabs = pdf.pages[pi].extract_tables()
        if len(tabs) != 1:
            raise ContractError(f"PDF page {pi + 1}: {len(tabs)} tables, expected 1")
        t = tabs[0]
        start = 0
        if pi == TABLE_PAGES[0]:
            if t[0] != HEADER:
                raise ContractError(f"unexpected table header {t[0]!r}")
            start = 1
        for k, r in enumerate(t[start:]):
            if len(r) != 8 or any(c is None or c.strip() == "" for c in r):
                raise ContractError(f"PDF page {pi + 1} row {k}: empty or short cell(s) {r!r}")
            if r == HEADER:
                raise ContractError(f"PDF page {pi + 1} row {k}: repeated header")
            rows.append({"pdf_page": pi + 1, "page_row": k, "cells": r})
    return rows


def parse_rows(raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        eco, country, region, measure, method, appp, n, ref = [clean(c) for c in r["cells"]]
        if measure not in MEASURES:
            raise ContractError(f"row {r['pdf_page']}/{r['page_row']}: unknown measure {measure!r}")
        if method not in METHODS:
            raise ContractError(f"row {r['pdf_page']}/{r['page_row']}: unknown method {method!r}")
        if not re.fullmatch(r"-?\d+(\.\d+)?", appp):
            raise ContractError(f"row {r['pdf_page']}/{r['page_row']}: APPP {appp!r} is not a number")
        if not re.fullmatch(r"[1-9]\d*", n):
            raise ContractError(f"row {r['pdf_page']}/{r['page_row']}: n {n!r} is not a positive integer")
        out.append({"pdf_page": r["pdf_page"], "page_row": r["page_row"], "ecosystem": eco, "country": country,
                    "region": region, "measure": measure, "method": method, "appp": float(appp), "n": int(n),
                    "reference": ref, "appp_text": appp})
    return out


def read_reference_paragraphs(pdf: pdfplumber.PDF) -> list[str]:
    paras: list[str] = []
    for pi in REF_PAGES:
        lines = pdf.pages[pi].extract_text_lines()
        prev = None
        for ln in lines:
            if round(ln["x0"]) != REF_LEFT_X0:
                prev = ln
                if not (ln["text"] == "References" or ln["text"].isdigit()):
                    raise ContractError(f"PDF page {pi + 1}: unexpected off-margin line {ln['text'][:60]!r}")
                continue
            if prev is None or ln["top"] - prev["top"] > PARAGRAPH_GAP_PT:
                paras.append(ln["text"])
            else:
                joiner = "" if paras[-1].endswith(("-\xad‐", "‐", "-")) else " "
                paras[-1] += joiner + ln["text"]
            prev = ln
    return [clean(p) for p in paras]


_ENTRY_END = re.compile(r"(?P<year>(?:1[89]|20)\d\d[a-z]?)\.?(?: DOI: ?\S+)?$")
_DOI = re.compile(r"10\.\d{4,9}/[^\s,;]+")


def parse_entries(paras: list[str]) -> list[dict]:
    """One dict per list entry; the count assertion (parsed == year-bearing paragraphs == paragraphs) lives here."""
    entries = []
    for p in paras:
        m = _ENTRY_END.search(p)
        if not m:
            raise ContractError(f"reference paragraph does not end in a year: {p[-80:]!r}")
        if ": " not in p[:260]:
            raise ContractError(f"reference paragraph has no 'authors: title' separator: {p[:80]!r}")
        authors, rest = p.split(": ", 1)
        doi = None
        d = _DOI.search(p)
        if d:
            doi = d.group(0).rstrip(".)")
            if doi.endswith(")") and "(" not in doi:
                doi = doi[:-1]
            doi = doi.lower()
        sm = re.match(r"^([^,]+?)(?:,|\s+[A-Z]\.)", authors)
        if not sm:
            raise ContractError(f"cannot read a first-author surname from {authors!r}")
        entries.append({"text": p, "authors": authors, "rest": rest, "year_tag": m.group("year"),
                        "year": int(m.group("year")[:4]), "doi": doi, "surname": sm.group(1), "label": f"{authors} {m.group('year')}"})
    year_bearing = sum(1 for p in paras if re.search(r"(?:1[89]|20)\d\d", p))
    if not (len(entries) == year_bearing == len(paras)):
        raise ContractError(f"parsed entries {len(entries)} != year-bearing paragraphs {year_bearing} != paragraphs {len(paras)}")
    if len({e["label"] for e in entries}) != len(entries):
        raise ContractError("two list entries share (authors part, year): reference keys would collide")
    return entries


# ---------------------------------------------------------------- in-text citations -> list entries

_PERS = re.compile(
    r"(?:[;,]\s*)?(?:and\s+)?(?:[A-Z]\.\s?)+(?:[A-Z][\w'-]+\s)*[A-Z][\w'-]+,?\s+(?:pers\.\s*comm\.|personal communication)[^()]*$"
)
_URL = re.compile(r",?\s*https?://\S+$")
_ITEM = re.compile(r"^(?P<who>.+?),?\s+(?P<years>\d{4}[a-z]?(?:,\s*\d{4}[a-z]?)*)$")


def split_reference_cell(cell: str) -> dict:
    """In-text items of one Reference cell: primary / secondary lists of ``(who, year_tag)``, plus pers. comm. text."""
    text = clean(cell)
    pers = None
    m = _PERS.search(text)
    if m:
        pers = re.sub(r"^and\s+", "", m.group(0).strip(" ;,"))
        text = text[: m.start()].rstrip(" ;,")
    url = None
    mu = _URL.search(text)
    if mu:
        url = mu.group(0).strip(" ,")
        text = text[: mu.start()].rstrip(" ;,")
    parts = text.split(" cited in ")
    if len(parts) > 2:
        raise ContractError(f"more than one 'cited in': {cell!r}")
    groups = []
    for part in parts:
        items = []
        # one or more parenthesised groups, separated by commas/semicolons/'and'
        inner = re.findall(r"\(([^()]*)\)", part)
        if not inner or re.sub(r"\([^()]*\)|[\s,;]|and", "", part) != "":
            raise ContractError(f"reference cell part not made of parenthesised citations: {part!r} in {cell!r}")
        for g in inner:
            for it in g.split(";"):
                it = it.strip()
                mm = _ITEM.match(it)
                if not mm:
                    raise ContractError(f"cannot parse citation item {it!r} in {cell!r}")
                for y in re.split(r",\s*", mm.group("years")):
                    items.append((mm.group("who"), y))
        groups.append(items)
    return {"primary": groups[0], "secondary": groups[1] if len(groups) == 2 else [], "pers_comm": pers, "url": url}


def _single_author(authors_part: str) -> bool:
    return bool(re.fullmatch(r"[^,]+?(?:,|\s)\s*(?:[A-Z]\.[\s-]*)+(?:Jr\.)?", authors_part))


def match_item(who: str, year: str, entries: list[dict]) -> tuple[int, str]:
    """(entry index, how) for one in-text item; raises when neither the automatic rule nor CROSSWALK resolves it."""
    key = f"{who}, {year}"
    if key in CROSSWALK:
        tgt = [i for i, e in enumerate(entries) if e["label"] == CROSSWALK[key]]
        if len(tgt) != 1:
            raise ContractError(f"CROSSWALK target for {key!r} matches {len(tgt)} entries: {CROSSWALK[key]!r}")
        return tgt[0], "crosswalk"
    m = re.fullmatch(r"(?P<s1>.+?)(?: and (?P<s2>.+?)| (?P<etal>et al\.))?", who)
    assert m
    s1, s2, etal = m.group("s1"), m.group("s2"), m.group("etal")
    cands = [i for i, e in enumerate(entries) if fold(e["surname"]) == fold(s1) and e["year_tag"] == year]
    if not cands and re.fullmatch(r"\d{4}", year):  # in-text year without letter, list entry with one
        cands = [i for i, e in enumerate(entries) if fold(e["surname"]) == fold(s1) and e["year"] == int(year)]
    ok = []
    for i in cands:
        a = entries[i]["authors"]
        if s2 is not None and fold(s2) not in fold(a.split(",", 1)[-1] if "," in a else a):
            continue
        if s2 is None and etal is None and not _single_author(a):
            continue
        if etal is not None and _single_author(a):
            continue
        ok.append(i)
    if len(ok) != 1:
        raise ContractError(f"citation item {key!r}: {len(ok)} automatic matches among {len(cands)} candidates; add a CROSSWALK entry")
    return ok[0], "auto"


# ---------------------------------------------------------------- main build


EXPECT = {  # the hand-verified counts of the real supplement (a unit test passes its own for a tiny fixture)
    "rows": EXPECTED_ROWS, "ecosystems": EXPECTED_ECOSYSTEMS, "n_sum": EXPECTED_N_SUM, "caption_n_sum": CAPTION_N_SUM,
    "ref_paragraphs": EXPECTED_REF_PARAGRAPHS, "negative_rows": NEGATIVE_ROWS,
}


def build_tables(
    raw: list[dict], paras: list[str], access_date: str, expect: dict
) -> tuple[dict[str, pd.DataFrame], list[dict], dict, dict]:
    rows = parse_rows(raw)
    if len(rows) != expect["rows"]:
        raise ContractError(f"{len(rows)} table rows, expected {expect['rows']}")
    n_sum = sum(r["n"] for r in rows)
    if n_sum != expect["n_sum"]:
        raise ContractError(f"sum of n is {n_sum}, expected {expect['n_sum']} (the caption says {expect['caption_n_sum']})")
    if clean(UNITS_HEADER_RAW).replace("Median APPP ", "") != UNITS:
        raise ContractError("declared unit does not match the cleaned header")
    if UNITS not in ATTRIBUTE_DECL[ATTR].units:
        raise ContractError(f"{ATTR}: unit {UNITS!r} not declared")
    decl = ATTRIBUTE_DECL[ATTR]

    ecos: dict[str, tuple[str, str]] = {}
    for r in rows:
        if ecos.setdefault(r["ecosystem"], (r["country"], r["region"])) != (r["country"], r["region"]):
            raise ContractError(f"ecosystem {r['ecosystem']!r} has two different country/region pairs")
    if len(ecos) != expect["ecosystems"]:
        raise ContractError(f"{len(ecos)} distinct ecosystems, expected {expect['ecosystems']}")
    seen_neg = {(r["ecosystem"], r["measure"]) for r in rows if r["appp"] < 0}
    if seen_neg != expect["negative_rows"]:
        raise ContractError(f"negative APPP rows {sorted(seen_neg)} differ from the declared {sorted(expect['negative_rows'])}")

    if len(paras) != expect["ref_paragraphs"]:
        raise ContractError(f"{len(paras)} reference paragraphs, expected {expect['ref_paragraphs']}")
    entries = parse_entries(paras)

    # ---- references: resolve every cited item
    def key_of(i: int) -> str:
        e = entries[i]
        return f"doi:{e['doi']}" if e["doi"] else f"ref:{e['label']}"

    used_entries: set[int] = set()
    how_count = {"auto": 0, "crosswalk": 0}
    used_crosswalk: set[str] = set()
    crosswalk_rows = []
    row_refs = []
    n_items = 0
    for r in rows:
        sp = split_reference_cell(r["reference"])
        prim, sec = [], []
        for role, items, dest in (("primary", sp["primary"], prim), ("secondary", sp["secondary"], sec)):
            for who, year in items:
                i, how = match_item(who, year, entries)
                how_count[how] += 1
                if how == "crosswalk":
                    used_crosswalk.add(f"{who}, {year}")
                n_items += 1
                if i not in dest:
                    dest.append(i)
                used_entries.add(i)
                crosswalk_rows.append({"in_text": f"{who}, {year}", "role": role, "how": how, "entry_label": entries[i]["label"]})
        if set(prim) & set(sec):
            raise ContractError(f"{r['ecosystem']}: a paper is both primary and secondary: {r['reference']!r}")
        idx = prim + sec
        if not idx:
            raise ContractError(f"{r['ecosystem']}: no citation parsed from {r['reference']!r}")
        row_refs.append({"idx": idx, "pers": sp["pers_comm"], "url": sp["url"], "secondary": bool(sec)})
    if used_crosswalk != set(CROSSWALK):
        raise ContractError(f"unused CROSSWALK entries: {sorted(set(CROSSWALK) - used_crosswalk)}")
    if len({e_key for e_key in (key_of(i) for i in used_entries)}) != len(used_entries):
        raise ContractError("two cited list entries resolve to one reference key")

    # ---- tables
    facts, samples, methods, locs = [], [], {}, {}
    ref_lists: dict[str, list[int]] = {}
    for gi, (r, rr) in enumerate(zip(rows, row_refs)):
        rk = "+".join(key_of(i) for i in rr["idx"])
        ref_lists.setdefault(rk, rr["idx"])
        if ref_lists[rk] != rr["idx"]:
            raise ContractError(f"one reference key with two entry lists: {rk[:80]!r}")
        lk = f"{r['ecosystem']}|{HABITAT_RAW}"
        locs.setdefault(lk, {"eco": r["ecosystem"], "country": r["country"], "region": r["region"]})
        skey = f"row{gi:03d}"
        samples.append(skey)
        mkey = f"{r['measure']}|{r['method']}"
        if mkey not in methods:
            params = {"measure": r["measure"], "measure_meaning": MEASURES[r["measure"]],
                      "method_label": r["method"], "table": "Table S1 (supplement)"}
            methods[mkey] = {
                "source_key": SOURCE_KEY, "method_key": mkey, "source_method_id": None, "attribute": ATTR,
                "method_status": "matched", "technique": r["method"], "coring_method": None, "carbon_type": None,
                "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
                "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": None,
                "light_dark": None, "incubation_type": None, "experimental_status": None,
                "params": json.dumps(params, sort_keys=True),
            }
        flags = ["habitat_from_scope", "source_median"]
        if r["appp"] < 0:
            flags.append("negative_value")
        if rr["secondary"]:
            flags.append("cited_secondary")
        if rr["pers"]:
            flags.append("pers_comm")
        facts.append({
            "source_key": SOURCE_KEY, "fact_key": f"{skey}:{ATTR}", "reference_key": rk, "location_key": lk,
            "sample_key": skey, "method_key": mkey, "attribute": ATTR, "measurement_type": decl.measurement_type,
            "subfield": next(iter(decl.subfields)), "value": r["appp"], "units_raw": UNITS, "sd": np.nan,
            "se": np.nan, "n": r["n"], "flags": sorted(flags),
            "source_row_ref": f"{DATA_FILE}#pdf_page={r['pdf_page']}:table_row={r['page_row']}:{ATTR}",
        })

    fact = pd.DataFrame(facts)
    fact["n"] = pd.array(list(fact["n"]), dtype="Int64")
    method = pd.DataFrame(sorted(methods.values(), key=lambda m: m["method_key"]))
    lk_sorted = sorted(locs)
    nloc = len(lk_sorted)
    loc = pd.DataFrame({
        "source_key": SOURCE_KEY, "location_key": lk_sorted, "site_name": [locs[k]["eco"] for k in lk_sorted],
        "source_site_id": None, "source_core_id": lk_sorted, "latitude": np.nan, "longitude": np.nan,
        "position_accuracy": np.nan, "position_accuracy_units": None, "position_method": None,
        "country": [locs[k]["country"] for k in lk_sorted], "admin_division": None, "water_body": None,
        "habitat_type": HABITAT_RAW, "geomorphic_setting": None, "bioregion": [locs[k]["region"] for k in lk_sorted],
        "marine_ecoregion": None, "salinity_class": None, "salinity_class_level": None, "vegetation_class": None,
        "vegetation_class_level": None, "tidal_position": None, "tidal_position_level": None,
        "modification_status": [[] for _ in range(nloc)], "dominant_species": [[] for _ in range(nloc)],
        "elevation": np.nan, "elevation_units": None, "elevation_datum": None, "elevation_method": None,
        "water_depth_m": np.nan, "distance_to_shore_km": np.nan,
    })
    sample = pd.DataFrame({
        "source_key": SOURCE_KEY, "sample_key": samples, "source_sample_id": None, "depth_min_cm": np.nan,
        "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan, "representative_depth_max_cm": np.nan,
        "depth_notes": "depth-integrated annual production; no sampling depth",
        "sampling_year": pd.array([None] * len(samples), dtype="Int64"),
        "sampling_month": pd.array([None] * len(samples), dtype="Int64"), "taxon": None, "taxon_rank": None,
        "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None, "photosynthetic_sediment": None,
        "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan,
        "water_salinity_units": None, "bottom_water_o2": np.nan, "bottom_water_o2_units": None,
    })
    ref_keys = sorted(ref_lists)
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_keys})
    cit_rows = []
    for rk in ref_keys:
        for k, i in enumerate(ref_lists[rk]):
            e = entries[i]
            cit_rows.append({
                "source_key": SOURCE_KEY, "citation_key": f"{rk}|{k}", "reference_key": rk, "bibliography_id": e["label"],
                "publication_type": None, "doi": e["doi"], "title": e["rest"], "authors": e["authors"], "year": e["year"],
                "url": None, "openalex_id": None, "doi_resolved": None, "doi_resolution_source": None,
            })
    citation = pd.DataFrame(cit_rows)
    citation["year"] = pd.array(list(citation["year"]), dtype="Int64")
    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + ". Version and license are from the supplement PDF itself. Table S1 sums to "
                 f"{n_sum} measurements, {expect['caption_n_sum'] - n_sum} fewer than the {expect['caption_n_sum']} in its caption.",
    }])
    tables = {"source": source, "reference": reference, "citation": citation,
              "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
              "method": method, "fact": fact}

    # ---- row accounting and join checks
    if len(fact) != len(rows):
        raise ContractError(f"facts {len(fact)} != table rows {len(rows)}")
    if int(fact["n"].sum()) != n_sum:
        raise ContractError("fact n does not sum to the table's n")
    for what, a, b in (("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference.reference_key),
                       ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key)):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    expected_cits = sum(len(v) for v in ref_lists.values())
    if len(citation) != expected_cits:
        raise ContractError(f"citation rows {len(citation)} != cited papers {expected_cits}")
    flag_counts: dict[str, int] = {}
    for fl in fact["flags"]:
        for x in fl:
            flag_counts[x] = flag_counts.get(x, 0) + 1

    audit = {
        "table_rows": [{"pdf_page": r["pdf_page"], "page_row": r["page_row"], "ecosystem": r["ecosystem"],
                        "country": r["country"], "region": r["region"], "measure": r["measure"], "method": r["method"],
                        "appp": r["appp_text"], "n": r["n"], "reference": r["reference"]} for r in rows],
        "references": [{"entry": k, "label": e["label"], "year": e["year"], "doi": e["doi"], "cited_by_table": k in used_entries,
                        "text": e["text"]} for k, e in enumerate(entries)],
        "crosswalk": sorted({(c["in_text"], c["role"], c["how"], c["entry_label"]) for c in crosswalk_rows}),
    }
    report = {
        "pdf_pages": EXPECTED_N_PDF_PAGES, "table_rows": len(rows), "rows_per_pdf_page": {
            str(p + 1): sum(1 for r in rows if r["pdf_page"] == p + 1) for p in TABLE_PAGES},
        "n_sum": n_sum, "caption_n_sum": expect["caption_n_sum"], "n_sum_minus_caption": n_sum - expect["caption_n_sum"],
        "ecosystems": len(ecos), "facts": len(fact), "skipped": 0, "collapsed_repeats": 0,
        "n_measures": {m: sum(1 for r in rows if r["measure"] == m) for m in MEASURES},
        "n_methods": {m: sum(1 for r in rows if r["method"] == m) for m in sorted(METHODS)},
        "rows_with_n_1": sum(1 for r in rows if r["n"] == 1), "negative_value_rows": len(seen_neg),
        "locations": len(loc), "samples": len(sample), "methods": len(method), "references": len(reference),
        "citations": len(citation), "reference_list_paragraphs": len(paras), "reference_list_entries": len(entries),
        "entries_with_doi": sum(1 for e in entries if e["doi"]),
        "list_entries_cited_by_table": len(used_entries), "list_entries_not_cited_by_table": len(entries) - len(used_entries),
        "in_text_items": n_items, "items_matched_automatically": how_count["auto"], "items_via_crosswalk": how_count["crosswalk"],
        "rows_cited_secondary": sum(1 for x in row_refs if x["secondary"]),
        "rows_with_pers_comm": sum(1 for x in row_refs if x["pers"]), "rows_with_url_text": sum(1 for x in row_refs if x["url"]),
        "pers_comm_texts": sorted({x["pers"] for x in row_refs if x["pers"]}),
        "value_min": float(fact.value.min()), "value_max": float(fact.value.max()),
        "flag_counts": flag_counts, "attribute": ATTR, "units": UNITS,
    }
    return tables, [], report, audit


def write_audit(audit: dict, out_dir: Path) -> None:
    def w(name: str, rows: list[dict]) -> None:
        with open(out_dir / name, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
            wr.writeheader()
            wr.writerows(rows)

    w("table_s1_rows.csv", audit["table_rows"])
    w("references_parsed.csv", audit["references"])
    w("reference_crosswalk.csv", [dict(zip(("in_text", "role", "how", "entry_label"), c)) for c in audit["crosswalk"]])


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--audit-dir", type=Path, default=here, help="where the checked-in review CSVs are written")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    with pdfplumber.open(args.raw_dir / DATA_FILE) as pdf:
        if len(pdf.pages) != EXPECTED_N_PDF_PAGES:
            raise ContractError(f"{len(pdf.pages)} PDF pages, expected {EXPECTED_N_PDF_PAGES}")
        tables, skips, report, audit = build_tables(
            read_table(pdf), read_reference_paragraphs(pdf), args.access_date, EXPECT
        )
    write_extracted(tables, args.out_dir)
    write_audit(audit, args.audit_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
