"""Match Francesco's DOI-less 'Short reference' strings to the source list on the PANGAEA
dataset page (JSON-LD ``isBasedOn``, saved to francesco-phytoplankton/pangaea_sources.json).

Matching is first-author surname + year only, so a short reference can match several
Pangaea entries (e.g. 'Boyd et al. 2004' -> 12 per-experiment datasets). Reports unique /
multiple / none (informational: this is an inference).

The rows written to doi_resolution_source_file.parquet come from the AUTHORITATIVE route instead: the
raw file's own ``Data doi/data link`` column. A DOI-less citation gets doi_resolved only when its
short reference has exactly ONE distinct data link in the file and that link is a doi.org DOI.
Short references with several data DOIs (one per experiment/cruise) are reported, not written:
they need per-row references (an extractor change). The Pangaea name+year match is asserted to
agree wherever both routes give a DOI. Nothing is written to compilations.duckdb here.
Run: uv run python data/compilations/francesco_source_list.py [--fetch]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import duckdb
import httpx

from coastal_crawler.citation_parse import fold
from coastal_crawler.openalex_client import norm_doi

HERE = Path(__file__).parent
URL = "https://doi.pangaea.de/10.1594/PANGAEA.932417"
SAVED = HERE / "francesco-phytoplankton" / "pangaea_sources.json"


def fetch() -> None:
    r = httpx.get(URL, follow_redirects=True, timeout=60)
    r.raise_for_status()
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
    d = next(x for x in (json.loads(b) for b in blocks) if "isBasedOn" in x)
    out = []
    for s in d["isBasedOn"]:
        cr = s.get("creator") or []
        cr = cr if isinstance(cr, list) else [cr]
        out.append({"identifier": s["identifier"], "type": s.get("additionalType"),
                    "first_author": (cr[0].get("familyName") or cr[0].get("name")) if cr else None,
                    "year": s.get("datePublished"), "title": s.get("name")})
    SAVED.write_text(json.dumps({"url": URL, "sources": out}, indent=1))
    print("saved", len(out), "sources")


def norm_label(s: str) -> str:
    return " ".join(fold(s.replace("Ã¶", "o")).split())


def file_data_links() -> dict[str, set[str]]:
    """Short reference -> distinct ``Data doi/data link`` values in the raw Francesco file."""
    con = duckdb.connect()
    con.execute(f"""create table raw as select * from read_csv('{HERE / "francesco-phytoplankton" / "Global_marine_phytoplankton_production_dataset.txt"}',
        delim='\t', header=true, quote='"', all_varchar=true, strict_mode=false, ignore_errors=true)""")
    cols = [r[0] for r in con.execute("describe raw").fetchall()]
    sr = next(c for c in cols if c.startswith("Short ref"))
    dd = next(c for c in cols if c.startswith("Data doi"))
    out: dict[str, set[str]] = {}
    for short, link in con.execute(f'select "{sr}", "{dd}" from raw').fetchall():
        out.setdefault(norm_label(short), set()).add(link.strip())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    if ap.parse_args().fetch:
        fetch()
    src = json.loads(SAVED.read_text())["sources"]
    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    rows = con.execute(
        """select d.citation_id, d.bibliography_id, count(m.measurement_id), d.citation_key
           from citation_dim d join source_dim s using(source_id) left join measurement_fact m using(reference_id)
           where s.source_key='francesco-phytoplankton' and d.doi is null group by all order by 4 desc""").fetchall()
    tot = {"unique": 0, "multiple": 0, "none": 0}
    pangaea_doi: dict[int, str | None] = {}
    file_links = file_data_links()
    apply_rows, multi = [], []
    for cid, bib, facts, ckey in rows:
        first = re.split(r"\s+(?:et al|and)\b|,|;", bib.strip())[0].strip()
        first = re.sub(r"\b(?:19|20)\d\d\b", "", first).strip()  # 'Lomas 2010' -> 'Lomas'
        sur = fold(first.split()[-1]) if first.split() else ""
        # year: the 4-digit year after the FIRST author block (before any '; in:')
        m = re.search(r"\b(19|20)\d\d\b", bib.split("; in:")[0])
        y = m.group(0) if m else None
        hits = [s for s in src if s["first_author"] and sur and sur in fold(s["first_author"]) and y and s["year"] == y]
        kind = "unique" if len(hits) == 1 else "multiple" if hits else "none"
        tot[kind] += facts
        pangaea_doi[cid] = norm_doi(hits[0]["identifier"]) if kind == "unique" and hits[0]["identifier"].startswith("https://doi.org/") else None
        print(f"{kind:8s} {facts:6d} {bib[:50]!r:55} -> {[h['identifier'].split('doi.org/')[-1] for h in hits][:3]}{'...' if len(hits) > 3 else ''} ({len(hits)})")
    print("facts by match kind:", tot)
    for cid, bib, facts, ckey in rows:
        links = file_links.get(norm_label(bib))
        if not links:
            continue
        if len(links) > 1:
            multi.append((bib.strip(), len(links), facts))
        elif next(iter(links)).lower().startswith("https://doi.org/10."):
            doi = norm_doi(next(iter(links)))
            assert pangaea_doi[cid] in (None, doi), f"{bib!r}: file data DOI {doi} disagrees with Pangaea match {pangaea_doi[cid]}"
            apply_rows.append((cid, "francesco-phytoplankton", ckey, bib.strip(), doi, facts))
    mem = duckdb.connect()
    mem.execute("create table t (citation_id integer, source_key varchar, citation_key varchar, label varchar, doi varchar, facts bigint)")
    mem.executemany("insert into t values (?,?,?,?,?,?)", apply_rows)
    mem.execute(f"copy t to '{HERE / 'doi_resolution_source_file.parquet'}' (format parquet)")
    print(f"apply-ready (file data DOI, single per label): {len(apply_rows)} citations, {sum(r[5] for r in apply_rows)} facts")
    for r in apply_rows: print("  ", r[3][:42], "->", r[4])
    print("NOT written (several data DOIs per label; needs per-row references):", multi)


if __name__ == "__main__":
    main()
