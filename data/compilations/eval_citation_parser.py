"""Offline scorer for coastal_crawler.citation_parse against OpenAlex-verified citations.

Uses openalex_doi_lookup.parquet (written by openalex_verify_dois.py) as ground truth, so
it makes NO API calls. Excludes known-bad rows (autofill-suspect, year/title mismatches
from openalex_doi_report.json) so a wrong stored DOI is not counted as a parser error.

Run:  uv run python -I data/compilations/eval_citation_parser.py [--show 40]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from _citation_common import citation_text, toks
from coastal_crawler.citation_parse import parse_citation
from coastal_crawler.openalex_client import norm_doi

HERE = Path(__file__).parent
TITLE_RECALL = 0.8  # fraction of OpenAlex title tokens present in the parsed title
TITLE_LEN_RATIO = 3.0  # parsed title may be at most this many times longer (venue spill)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, required=True)
    args = ap.parse_args()

    report = json.loads((HERE / "openalex_doi_report.json").read_text())
    excluded = {x["citation_id"] for k in ("not_found", "year_disagreements", "title_disagreements") for x in report[k]}

    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    cur = con.execute("select citation_id, source_id, citation_key, bibliography_id, doi, title, authors, year from citation_dim where doi is not null")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    oa = {r[0]: r for r in duckdb.connect().execute(
        f"select doi_norm, oa_title, oa_authors_json from '{HERE / 'openalex_doi_lookup.parquet'}' where found").fetchall()}

    by_style: dict[str, Counter] = defaultdict(Counter)
    fails = []
    for r in rows:
        if r["citation_id"] in excluded:
            continue
        try:
            d = norm_doi(r["doi"])
        except ValueError:
            continue
        if d not in oa:
            continue
        _, oa_title, oa_auth_json = oa[d]
        if oa_title is None:  # OpenAlex has no title for this work: nothing to score against
            by_style["(oa has no title)"]["n"] += 1
            continue
        text = citation_text(r)
        if text is None:
            by_style["(no text)"]["n"] += 1
            continue
        p = parse_citation(text, r["year"], title_field=r["title"], authors_field=r["authors"])
        c = by_style[p.style]
        c["n"] += 1
        if p.title:
            oa_t, got_t = toks(oa_title), toks(p.title)
            rec = len(oa_t & got_t) / len(oa_t) if oa_t else 0
            good = rec >= TITLE_RECALL and len(got_t) <= TITLE_LEN_RATIO * len(oa_t)
            c["title_ok"] += good
            if not good:
                fails.append(("title", p.style, r["citation_id"], text[:150], p.title[:100], oa_title[:100]))
        if p.first_author:
            auth_toks = set().union(*(toks(a) for a in json.loads(oa_auth_json)[:3])) if oa_auth_json else set()
            ok = bool(toks(p.first_author) & auth_toks)
            c["author_ok"] += ok
            if not ok:
                fails.append(("author", p.style, r["citation_id"], text[:150], p.first_author, oa_auth_json[:100]))
    for style, c in sorted(by_style.items()):
        print(f"{style:18s} n={c['n']:5d} title_ok={c['title_ok']:5d} author_ok={c['author_ok']:5d}")
    print("TOTAL", sum(c["n"] for c in by_style.values()))
    for f in fails[: args.show]:
        print(f)


if __name__ == "__main__":
    main()
