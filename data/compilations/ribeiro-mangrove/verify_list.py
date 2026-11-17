#!/usr/bin/env python3
"""Independent check of the ribeiro-mangrove reference list (second PDF text engine, database round trip).

extract.py reads the list with pdfplumber. This script reads the same pages with poppler's ``pdftotext`` and checks that
(1) the number of ``. YEAR. `` occurrences equals the number of parsed entries, and (2) every citation row of the built
database (``python -I verify_list.py <compilations.duckdb>``) reappears verbatim (authors. year. text) in poppler's text of
the list pages, i.e. nothing was dropped, truncated, or merged at the page breaks; and the number of references and
citations equals the number of entries. Exits non-zero on any difference.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
PDF = HERE / "ecs22841-sup-0001-appendixs1.pdf"
FIRST_LIST_PAGE, LAST_LIST_PAGE = 3, 13  # 1-based PDF pages of the list


def norm(s: str) -> str:
    """Whitespace-collapsed and hyphen-free: poppler drops the hyphen of a word broken at a line end
    (``estuarina-lagunar`` -> ``estuarinalagunar``), pdfplumber keeps it, so hyphens are ignored on both sides."""
    return re.sub(r"\s+", " ", re.sub(r"-\s*", "", s)).strip()


def main() -> int:
    txt = subprocess.run(["pdftotext", "-f", str(FIRST_LIST_PAGE), "-l", str(LAST_LIST_PAGE), str(PDF), "-"],
                         capture_output=True, text=True, check=True).stdout
    # a page break inside an entry carries the page number (a lone number just before the form feed): drop it
    text = norm(re.sub(r"\s\d{1,2}\s*\f", " ", txt))
    n_years = len(re.findall(r"\.\s(?:1[89]|20)\d\d[a-z]?\.\s", text))
    con = duckdb.connect(sys.argv[1], read_only=True)
    cits = con.execute(
        "select c.authors, c.year, c.title, c.bibliography_id, c.reference_id from citation_dim c "
        "join source_dim s using (source_id) where s.source_key = 'ribeiro-mangrove'").fetchall()
    nref = con.execute("select count(*) from reference_dim r join source_dim s using (source_id) "
                       "where s.source_key = 'ribeiro-mangrove'").fetchone()[0]
    nfact = con.execute("select count(*) from measurement_fact f join source_dim s using (source_id) "
                        "where s.source_key = 'ribeiro-mangrove'").fetchone()[0]
    con.close()
    bad = 0
    if not (n_years == len(cits) == nref):
        print(f"FAIL counts: poppler year-bearing entries {n_years}, citations {len(cits)}, references {nref}")
        bad += 1
    if nfact:
        print(f"FAIL {nfact} fact rows for a reference-list-only source")
        bad += 1
    for authors, year, title, bib, _rid in cits:
        want = norm(f"{authors}. {year}. {title}")
        if want not in text:
            print(f"FAIL not found verbatim in poppler text: {want[:100]}")
            bad += 1
    print(f"{len(cits)} citation rows checked against poppler text ({n_years} year-bearing entries there)")
    print("PASS" if bad == 0 else f"{bad} FAILURES")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
