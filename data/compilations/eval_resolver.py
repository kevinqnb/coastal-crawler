"""End-to-end check of citation_resolve: hide the DOI of already-verified citations, resolve
them from text alone, and compare to the known OpenAlex work.

Sample = rows whose DOI verdict is verified_title (so the "truth" is itself title-verified)
and whose text parses to a title. foster-seagrass is excluded (its stored DOIs are the
suspect ones). Costs ~10 credits per search; responses are cached.

Run:  uv run python data/compilations/eval_resolver.py --n 300 --seed 0 --per-page 5
"""

from __future__ import annotations

import argparse
import random
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
from _citation_common import citation_text
from dotenv import dotenv_values

from coastal_crawler.citation_parse import parse_citation
from coastal_crawler.citation_resolve import resolve_title
from coastal_crawler.openalex_client import OpenAlexClient

HERE = Path(__file__).parent
USER_AGENT = "coastal-crawler/0.1 (compilations citation resolution eval)"
REQUEST_DELAY = 0.1
TIMEOUT = 120
MAX_SERVER_RETRIES = 5
RETRY_BACKOFF = 10.0  # seconds, multiplied by attempt number


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--per-page", type=int, required=True)
    args = ap.parse_args()

    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    cur = con.execute(
        f"""select c.citation_id, s.source_key, c.citation_key, c.bibliography_id, c.title, c.authors,
                   c.year, k.openalex_id, k.oa_doi
            from citation_dim c join source_dim s using (source_id)
            join '{HERE / 'doi_check.parquet'}' k using (citation_id)
            where k.verdict = 'verified_title' and s.source_key <> 'foster-seagrass'
            order by c.citation_id"""
    )
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    parsed = []
    for r in rows:
        p = parse_citation(citation_text(r), r["year"], title_field=r["title"], authors_field=r["authors"])
        if p.title:
            parsed.append((r, p))
    random.Random(args.seed).shuffle(parsed)
    sample = parsed[: args.n]
    assert len(sample) == args.n, (len(sample), args.n)

    client = OpenAlexClient(dotenv_values(HERE.parents[1] / ".env")["OPENALEX_API_KEY"], HERE / "_openalex_cache",
                            USER_AGENT, REQUEST_DELAY, TIMEOUT,
                            MAX_SERVER_RETRIES, RETRY_BACKOFF)
    tally: dict[str, Counter] = defaultdict(Counter)
    wrong = []
    for r, p in sample:
        res = resolve_title(client, p, args.per_page)
        correct = res.openalex_id == r["openalex_id"]
        tally[res.status]["n"] += 1
        tally[res.status]["correct"] += correct
        if res.status in ("resolved", "resolved_weak") and not correct:
            wrong.append((r["source_key"], r["citation_id"], p.title[:70], "truth:", r["oa_doi"], "got:", res.doi, res.evidence))
        if res.status in ("no_match", "ambiguous"):
            tally[res.status][r["source_key"]] += 1
    client.close()

    for status, c in tally.items():
        print(status, dict(c))
    acc = sum(tally[s]["n"] for s in ("resolved", "resolved_weak"))
    cor = sum(tally[s]["correct"] for s in ("resolved", "resolved_weak"))
    print(f"accepted={acc}/{args.n}  accepted_correct={cor}  precision={cor / acc:.3f}  recall={cor / args.n:.3f}")
    for w in wrong:
        print("WRONG", w)


if __name__ == "__main__":
    main()
