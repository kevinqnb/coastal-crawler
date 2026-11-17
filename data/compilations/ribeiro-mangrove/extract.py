#!/usr/bin/env python3
"""Ribeiro et al. (2019) Appendix S1 reference list -> compilation-contract tables. NO MEASUREMENTS.

Ribeiro, R.A., Rovai, A.S., Twilley, R.R., Castaneda-Moya, E. (2019): Spatial variability of mangrove primary productivity
in the neotropics. Ecosphere 10(8), e02841, doi:10.1002/ecs2.2841. Raw file (this directory):
``ecs22841-sup-0001-appendixs1.pdf`` (13 PDF pages): page 1 front matter, page 2 Table S1 (AIC model coefficients) and S2
(ANOVA) of the authors' litterfall model, pages 3-13 "List of studies used to build the litterfall model for mangroves in the
neotropics". Tables S1 and S2 are modelling output, not measurements, and are not loaded.

What this builds: the ``source`` row, and one ``reference`` + one ``citation`` row per listed study (``reference_key`` =
``ref:<first author [and second | et al.] year[letter]>``). There is NO location, sample, method or fact row, so the build needs
the explicit ``--allow-factless ribeiro-mangrove`` flag (contract change C1); this extract passes the same exemption to
``write_extracted``. The point of listing the studies is to seed literature extraction, which needs their DOIs (``doi`` NULL here;
the later OpenAlex job fills ``doi_resolved``).

Parsing (Ecosphere reference style, double spaced, no hanging indent): text lines are read with pdfplumber, page-number lines
dropped; a vertical gap above ``PARAGRAPH_GAP_PT`` between line tops starts a new paragraph. A page can end in the middle of an
entry: the first paragraph of a page continues the last one of the previous page when that one is incomplete (no ``. YEAR.``
or no final full stop); when it is complete the next paragraph must look like an author start, else the parse raises. Each
paragraph must match ``Authors. YEAR[letter]. Title and publication details``; the extract asserts that the number of parsed
entries equals the number of paragraphs that contain a year and the number of paragraphs (a paragraph that fails to parse
raises), and that the reference keys are unique. ``citation.authors`` = the author string as printed (initials-first after the
first author), ``citation.year`` the year, ``citation.title`` the text after the year (title, journal, volume:pages, publisher
or institution as printed: the entries have no separable title) and ``publication_type`` from keywords: ``dissertation`` /
``thesis`` (the words ``Dissertation.`` / ``Thesis.`` as a sentence), ``book chapter`` (``Pages N-N in``), ``proceedings`` (``Proceedings of``), ``journal article``
(ends in ``volume:pages.``), else NULL (a report or book the keywords do not identify).

The PDF's text layer garbles a few author strings (e.g. ``S. . Iriarte-Vivar``, ``C. M. A. . M. A. Hernandez``: duplicated
initials in the source file); they are kept as printed.

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "ribeiro-mangrove"
SOURCE_NAME = "Ribeiro et al. 2019 Appendix S1: studies used to build the neotropical mangrove litterfall model (reference list only)"
SOURCE_DOI = "10.1002/ecs2.2841"
SOURCE_VERSION = "Ecosphere 10(8) e02841, Appendix S1 (PDF created 2019-07-23); no version number exists"
SOURCE_LICENSE = "CC BY 3.0"  # decided by the user; the PDF itself states no license
DATA_FILE = "ecs22841-sup-0001-appendixs1.pdf"
CITATION = (
    "Ribeiro, R.A., Rovai, A.S., Twilley, R.R., Castaneda-Moya, E. (2019): Spatial variability of mangrove primary "
    "productivity in the neotropics. Ecosphere 10(8), e02841, doi:10.1002/ecs2.2841 (Appendix S1)"
)
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset()  # no measurement cells exist
check_skip_reasons(ALLOWED_SKIP_REASONS)

EXPECTED_N_PDF_PAGES = 13
LIST_PAGES = range(2, 13)  # 0-based pdfplumber page indexes holding the list (PDF pages 3-13)
LIST_TITLE = "List of studies used to build the litterfall"
PAGE_NUMBER_X0 = 500  # page-number lines sit at the right margin
PARAGRAPH_GAP_PT = 32.0  # line tops are ~27.7 pt apart inside an entry and ~35.6 pt between entries
REVIEWED_ENTRIES = 87  # the entry count of the list as reviewed by eye (set once the review is done; a mismatch raises)

_YEAR = r"(?:1[89]|20)\d\d"
_ENTRY = re.compile(rf"^(?P<authors>.+?)\.\s+(?P<year>{_YEAR}[a-z]?)\.\s+(?P<rest>.+)$", re.S)
_COMPLETE = re.compile(rf"\.\s+{_YEAR}[a-z]?\.\s+.+\.$", re.S)
_AUTHOR_START = re.compile(r"^[A-ZÀ-Ý][^,.]{1,40},\s+(?:[A-Z]\.|and\s)")
_ARTICLE_END = re.compile(r"(?:\d+(?:\(\d+\))?)?:[\w.-]+\s*[–-]\s*[\w.-]+\.$")


def read_paragraphs(pdf: pdfplumber.PDF) -> list[str]:
    paras: list[str] = []
    for pi in LIST_PAGES:
        lines = [ln for ln in pdf.pages[pi].extract_text_lines() if not (ln["x0"] > PAGE_NUMBER_X0 and ln["text"].isdigit())]
        if pi == LIST_PAGES[0]:
            if not lines[0]["text"].startswith(LIST_TITLE):
                raise ContractError(f"PDF page {pi + 1} does not start with the list title: {lines[0]['text']!r}")
            lines = lines[1:]
        prev = None
        for k, ln in enumerate(lines):
            if prev is not None:
                new = ln["top"] - prev["top"] > PARAGRAPH_GAP_PT
            elif not paras:
                new = True
            elif _COMPLETE.search(paras[-1]):
                if not _AUTHOR_START.match(ln["text"]):
                    raise ContractError(
                        f"PDF page {pi + 1}: previous paragraph is complete but the page starts with {ln['text'][:60]!r}")
                new = True
            else:
                new = False  # the previous page's last entry is cut mid-way: this line continues it
            if new:
                paras.append(ln["text"])
            else:
                joiner = "" if paras[-1].endswith("-") else " "
                paras[-1] += joiner + ln["text"]
            prev = ln
    return [re.sub(r"\s+", " ", p).strip() for p in paras]


def _surname_after_initials(seg: str) -> str:
    m = re.match(r"^(?:[A-ZÀ-Ý]\.\s*)+(.+)$", seg.strip())
    return (m.group(1) if m else seg).strip()


def parse_entries(paras: list[str]) -> list[dict]:
    entries = []
    for p in paras:
        m = _ENTRY.match(p)
        if not m:
            raise ContractError(f"reference paragraph does not match 'Authors. YEAR. Title...': {p[:100]!r}")
        authors, tag, rest = m.group("authors"), m.group("year"), m.group("rest")
        segs = [s for s in re.split(r",\s+(?:and\s+)?|\s+and\s+", authors) if s]
        n_auth = len(segs) - 1  # the first author is 'Surname, Initials' (two segments)
        if n_auth < 1 or "," not in authors:
            raise ContractError(f"cannot read the author list of {p[:100]!r}")
        first = segs[0]
        if n_auth == 1:
            who = first
        elif n_auth == 2:
            who = f"{first} and {_surname_after_initials(segs[-1])}"
        else:
            who = f"{first} et al."
        if re.search(r"\bDissertation\.", rest) or re.search(r"\bDissertation$", rest):
            ptype = "dissertation"
        elif re.search(r"\bThesis\b\.?", rest) and re.search(r"(?:^|\.\s)(?:MSc |PhD |Ph\.D\. |M\.Sc\. )?Thesis\.?", rest):
            ptype = "thesis"
        elif re.search(r"\bPages \d+[–-]\d+ in\b", rest):
            ptype = "book chapter"
        elif re.search(r"\bProceedings of\b", rest):
            ptype = "proceedings"
        elif _ARTICLE_END.search(rest):
            ptype = "journal article"
        else:
            ptype = None
        entries.append({"text": p, "authors": authors, "year_tag": tag, "year": int(tag[:4]), "rest": rest,
                        "key": f"ref:{who} {tag}", "n_authors": n_auth, "publication_type": ptype})
    year_bearing = sum(1 for p in paras if re.search(_YEAR, p))
    if not (len(entries) == year_bearing == len(paras)):
        raise ContractError(f"parsed entries {len(entries)} != year-bearing paragraphs {year_bearing} != paragraphs {len(paras)}")
    keys = [e["key"] for e in entries]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ContractError(f"reference keys collide (two entries by the same first author(s) and year): {dup}")
    return entries


def build_tables(entries: list[dict], access_date: str) -> tuple[dict[str, pd.DataFrame], dict]:
    keys = [e["key"] for e in entries]
    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + ". Reference list only; no measurements (Tables S1 and S2 are the authors' model output and are not "
                 "loaded). The PDF states no license; CC BY 3.0 is the license decided for it.",
    }])
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": keys})
    citation = pd.DataFrame({
        "source_key": SOURCE_KEY, "citation_key": [f"{k}|0" for k in keys], "reference_key": keys,
        "bibliography_id": [e["authors"] + " " + e["year_tag"] for e in entries],
        "publication_type": [e["publication_type"] for e in entries], "doi": None,
        "title": [e["rest"] for e in entries], "authors": [e["authors"] for e in entries],
        "year": pd.array([e["year"] for e in entries], dtype="Int64"), "url": None, "openalex_id": None,
        "doi_resolved": None, "doi_resolution_source": None,
    })
    tables = {"source": source, "reference": reference, "citation": citation}
    for name in ("study_screening", "location", "sample", "method", "fact"):
        tables[name] = empty_table(name)
    # location has no derived columns to supply from extract.py: write_extracted derives them (zero rows)
    tables["location"] = tables["location"].drop(columns=[
        c for c in ("habitat", "bottom_type", "setting", "salinity_zone", "modification_class") if c in tables["location"].columns])
    if len(reference) != len(entries) or len(citation) != len(entries):
        raise ContractError("reference/citation rows differ from the parsed entries")
    pt: dict[str, int] = {}
    for e in entries:
        pt[str(e["publication_type"])] = pt.get(str(e["publication_type"]), 0) + 1
    report = {
        "pdf_pages": EXPECTED_N_PDF_PAGES, "reference_list_entries": len(entries), "references": len(reference),
        "citations": len(citation), "facts": 0, "skipped": 0,
        "entries_by_publication_type": dict(sorted(pt.items())),
        "entries_by_n_authors": {str(k): sum(1 for e in entries if e["n_authors"] == k)
                                 for k in sorted({e["n_authors"] for e in entries})},
        "year_min": min(e["year"] for e in entries), "year_max": max(e["year"] for e in entries),
        "entries_with_year_letter": sum(1 for e in entries if e["year_tag"][-1].isalpha()),
        "entries_with_doi_in_text": sum(1 for e in entries if re.search(r"10\.\d{4,9}/", e["text"])),
    }
    return tables, report


def write_audit(entries: list[dict], out_dir: Path) -> None:
    with open(out_dir / "references_parsed.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=["n", "key", "year", "publication_type", "n_authors", "text"], lineterminator="\n")
        wr.writeheader()
        for i, e in enumerate(entries, 1):
            wr.writerow({"n": i, "key": e["key"], "year": e["year"], "publication_type": e["publication_type"] or "",
                         "n_authors": e["n_authors"], "text": e["text"]})


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--audit-dir", type=Path, default=here, help="where the checked-in review CSV is written")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    with pdfplumber.open(args.raw_dir / DATA_FILE) as pdf:
        if len(pdf.pages) != EXPECTED_N_PDF_PAGES:
            raise ContractError(f"{len(pdf.pages)} PDF pages, expected {EXPECTED_N_PDF_PAGES}")
        paras = read_paragraphs(pdf)
    entries = parse_entries(paras)
    if len(entries) != REVIEWED_ENTRIES:
        raise ContractError(f"{len(entries)} entries parsed; the list reviewed by eye had {REVIEWED_ENTRIES}")
    tables, report = build_tables(entries, args.access_date)
    write_extracted(tables, args.out_dir, allow_factless=frozenset({SOURCE_KEY}))
    write_audit(entries, args.audit_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        fh.write("")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
