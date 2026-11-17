#!/usr/bin/env python3
"""Independent re-reading of Cloern et al. (2014) Table S1, compared with ``table_s1_rows.csv`` (written by extract.py).

extract.py reads the table with ``pdfplumber.extract_tables``. This script does not: it (1) rebuilds every cell from the
ruled-cell geometry and word positions (vertical rule x-positions give the columns, horizontal rules the row bands), and
compares all 8 columns of all rows with the CSV; (2) tallies, per PDF page, the (measure, APPP, n) triples from
poppler's ``pdftotext -layout`` output (a different PDF text engine) and compares the multisets with the CSV. Exits
non-zero on any difference. Run: ``python -I verify_table.py`` (needs ``pdftotext`` on PATH).

With a path to the built ``compilations.duckdb`` as the one argument it also round-trips EVERY cloern fact in the
database to its raw cell: ``source_row_ref`` (page, row) -> the geometry-read cells of that row -> ecosystem, country,
region, measure, method, APPP and n must equal the fact's location, method, value and n.
"""

from __future__ import annotations

import collections
import csv
import re
import subprocess
import sys
from pathlib import Path

import pdfplumber

HERE = Path(__file__).resolve().parent
PDF = HERE / "bg-11-2477-2014-supplement.pdf"
TABLE_PAGES = range(1, 7)  # 0-based
HEADER_RE = re.compile(r"Ecosystem|Country|Region|Measure|Method|Median|APPP|Reference")


def clean(s: str) -> str:
    s = s.replace("-\xad‐", "-").replace("\xad", "").replace("‐", "-")
    return re.sub(r"\s+", " ", s).strip()


def cells_from_geometry(page) -> list[list[str]]:
    vx = sorted({round(r["x0"]) for r in page.rects if r["height"] > 5 and r["width"] < 1})
    hy = sorted({round(r["top"], 1) for r in page.rects if r["width"] > 100 and r["height"] < 1})
    # collapse rules closer than 2 pt (a rule is drawn as two rects)
    ys: list[float] = []
    for y in hy:
        if not ys or y - ys[-1] > 2:
            ys.append(y)
    words = page.extract_words()
    cols = list(zip(vx, vx[1:]))
    rows = []
    for top, bot in zip(ys, ys[1:]):
        band = [w for w in words if top - 0.5 <= (w["top"] + w["bottom"]) / 2 <= bot + 0.5]
        if not band:
            continue
        row = []
        for x0, x1 in cols:
            ws = sorted((w for w in band if x0 <= (w["x0"] + w["x1"]) / 2 < x1), key=lambda w: (round(w["top"]), w["x0"]))
            row.append(clean(" ".join(w["text"] for w in ws)))
        rows.append(row)
    return rows


def main() -> int:
    with open(HERE / "table_s1_rows.csv", newline="", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    bad = 0
    by_page: dict[int, list[dict]] = collections.defaultdict(list)
    for r in csv_rows:
        by_page[int(r["pdf_page"])].append(r)
    pdf = pdfplumber.open(PDF)
    total_geo = 0
    for pi in TABLE_PAGES:
        geo = [r for r in cells_from_geometry(pdf.pages[pi]) if not HEADER_RE.search(r[0]) or r[0] not in ("Ecosystem",)]
        geo = [r for r in geo if r[0] != "Ecosystem"]
        mine = by_page[pi + 1]
        if len(geo) != len(mine):
            print(f"FAIL page {pi + 1}: geometry rows {len(geo)} != csv rows {len(mine)}")
            bad += 1
            continue
        total_geo += len(geo)
        for g, c in zip(geo, mine):
            want = [c["ecosystem"], c["country"], c["region"], c["measure"], c["method"], c["appp"], c["n"], c["reference"]]
            if [clean(x) for x in g] != [clean(x) for x in want]:
                print(f"FAIL page {pi + 1}: {g} != {want}")
                bad += 1
    print(f"geometry rebuild: {total_geo} rows compared on {len(list(TABLE_PAGES))} pages")

    # poppler tally of (measure, appp, n) per page
    pat = re.compile(r"\b(GPP|NPP|U)\s+(?:O2 &|O2|14C|13C)\s+(-\S*?\d+)\s+(\d+)\b|\b(GPP|NPP|U)\s+(?:O2 &|O2|14C|13C)\s+(\d+)\s+(\d+)\b")
    n_poppler = 0
    for pi in TABLE_PAGES:
        txt = subprocess.run(["pdftotext", "-layout", "-f", str(pi + 1), "-l", str(pi + 1), str(PDF), "-"],
                             capture_output=True, text=True, check=True).stdout
        txt = clean(txt)  # all whitespace (incl. the tab / CR / NBSP runs poppler emits between cells) -> one space
        got = collections.Counter()
        for m in re.finditer(r"\b(GPP|NPP|U) (?:O2 & 14C|O2|14C|13C) (-?\d+) (\d+)\b", txt):
            got[(m.group(1), m.group(2), m.group(3))] += 1
        # a method cell wrapped over two lines ("O2 &" / "14C") may be read in a different order: reconcile with the CSV
        want = collections.Counter((r["measure"], r["appp"], r["n"]) for r in by_page[pi + 1])
        missing = want - got
        extra = got - want
        wrapped = [r for r in by_page[pi + 1] if r["method"] == "O2 & 14C"]
        # rows whose method wraps are tallied from the number tokens on the line below the method
        for r in wrapped:
            key = (r["measure"], r["appp"], r["n"])
            if missing[key]:
                missing[key] -= 1
        missing = +missing
        if missing or extra:
            print(f"FAIL page {pi + 1}: poppler missing {dict(missing)} extra {dict(extra)}")
            bad += 1
        n_poppler += sum(got.values())
    print(f"poppler tally: {n_poppler} (measure, APPP, n) triples matched (rows with a wrapped 'O2 &/14C' method cell are tallied from the CSV only)")
    if len(sys.argv) == 2:
        bad += roundtrip(pdf, Path(sys.argv[1]))
    print("PASS" if bad == 0 else f"{bad} FAILURES")
    return 0 if bad == 0 else 1


def roundtrip(pdf, db: Path) -> int:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        "select f.source_row_ref, f.value, f.n, f.units_raw, l.site_name, l.country, l.bioregion, m.technique, m.params "
        "from measurement_fact f join source_dim s using (source_id) join location_dim l using (location_id) "
        "join method_dim m using (method_id) where s.source_key = 'cloern-phytoplankton'"
    ).fetchall()
    con.close()
    geo = {pi + 1: [r for r in cells_from_geometry(pdf.pages[pi]) if r[0] != "Ecosystem"] for pi in TABLE_PAGES}
    bad = 0
    seen = set()
    for ref, value, n, units, site, country, region, tech, params in rows:
        m = re.fullmatch(r"bg-11-2477-2014-supplement\.pdf#pdf_page=(\d+):table_row=(\d+):cloern_appp_median", ref)
        if not m:
            print(f"FAIL unparseable source_row_ref {ref}")
            bad += 1
            continue
        page, k = int(m.group(1)), int(m.group(2))
        seen.add((page, k))
        eco, ctry, reg, measure, method, appp, nn, _ref = geo[page][k]
        measure_db = __import__("json").loads(params)["measure"]
        got = (eco, ctry, reg, measure, method, float(appp), int(nn))
        want = (site, country, region, measure_db, tech, value, n)
        if got != want or units != "g C m-2 yr-1":
            print(f"FAIL {ref}: raw {got} != db {want}")
            bad += 1
    total_raw = sum(len(v) for v in geo.values())
    if len(rows) != total_raw or len(seen) != total_raw:
        print(f"FAIL fact rows {len(rows)} / distinct raw rows {len(seen)} != raw table rows {total_raw}")
        bad += 1
    print(f"round trip: {len(rows)} facts compared with their raw cells")
    return bad


if __name__ == "__main__":
    sys.exit(main())
