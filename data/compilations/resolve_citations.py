"""Resolve citations that lack a trustworthy DOI to OpenAlex works.

Targets (from doi_check.parquet): rows with NO stored DOI, plus rows whose stored DOI failed
verification (mismatch_title, mismatch_author_year, not_found, malformed). Rows whose DOI is
verified are left alone; 'unverifiable' DOIs are left alone and reported.

Rows whose text has a title are searched by title (citation_resolve.resolve_title). Rows with
only author+year are recorded as 'author_year_only' (resolved in a later pass); rows with
neither are 'unparsed'. Nothing is written to compilations.duckdb.

Output: doi_resolution.parquet, one row per target citation.
Run:  uv run python data/compilations/resolve_citations.py --per-page 5 [--limit N]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import duckdb
from _citation_common import citation_text
from dotenv import dotenv_values

from coastal_crawler.citation_parse import parse_citation
from coastal_crawler.citation_resolve import resolve_title
from coastal_crawler.openalex_client import OpenAlexClient

HERE = Path(__file__).parent
USER_AGENT = "coastal-crawler/0.1 (compilations citation resolution)"
REQUEST_DELAY = 0.1
TIMEOUT = 120
MAX_SERVER_RETRIES = 5
RETRY_BACKOFF = 10.0  # seconds, multiplied by attempt number
BAD_VERDICTS = ("mismatch_title", "mismatch_author_year", "not_found", "malformed", "autofill_suspect")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-page", type=int, required=True)
    ap.add_argument("--limit", type=int, default=None, help="smoke run: only the first N targets")
    args = ap.parse_args()

    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    cur = con.execute(
        f"""select c.citation_id, s.source_key, c.citation_key, c.bibliography_id, c.doi, c.title,
                   c.authors, c.year, k.verdict
            from citation_dim c join source_dim s using (source_id)
            left join '{HERE / 'doi_check.parquet'}' k using (citation_id)
            where c.doi is null or k.verdict in {BAD_VERDICTS}
            order by c.citation_id"""
    )
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    n_dim = con.execute("select count(*) from citation_dim").fetchone()[0]
    n_verified_or_unv = con.execute(
        f"select count(*) from '{HERE / 'doi_check.parquet'}' where verdict not in {BAD_VERDICTS}"
    ).fetchone()[0]
    assert len(rows) + n_verified_or_unv == n_dim, "targets + left-alone rows must partition citation_dim"
    if args.limit:
        rows = rows[: args.limit]

    client = OpenAlexClient(dotenv_values(HERE.parents[1] / ".env")["OPENALEX_API_KEY"], HERE / "_openalex_cache",
                            USER_AGENT, REQUEST_DELAY, TIMEOUT,
                            MAX_SERVER_RETRIES, RETRY_BACKOFF)
    out = []
    for r in rows:
        reason = "no_doi" if r["doi"] is None else f"bad_doi:{r['verdict']}"
        text = citation_text(r)
        p = parse_citation(text, r["year"], title_field=r["title"], authors_field=r["authors"]) if text else None
        if p is None:
            status, res = "no_text", None
        elif p.title:
            res = resolve_title(client, p, args.per_page)
            status = res.status
        else:
            res, status = None, p.style  # author_year_only | unparsed
        out.append((
            r["citation_id"], r["source_key"], r["citation_key"], reason, r["doi"], p.style if p else None,
            p.title if p else None, p.first_author if p else None, p.year if p else None, status,
            res.openalex_id if res else None, res.doi if res else None, res.oa_title if res else None,
            json.dumps(res.evidence) if res else None,
        ))
    client.close()
    assert len(out) == len(rows) and len({o[0] for o in out}) == len(rows)

    mem = duckdb.connect()
    mem.execute(
        """create table t (citation_id integer, source_key varchar, citation_key varchar, reason varchar, stored_doi varchar,
           parse_style varchar, parsed_title varchar, parsed_first_author varchar, parsed_year integer,
           status varchar, openalex_id varchar, resolved_doi varchar, oa_title varchar, evidence_json varchar)"""
    )
    mem.executemany("insert into t values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    mem.execute(f"copy t to '{HERE / ('doi_resolution_smoke.parquet' if args.limit else 'doi_resolution.parquet')}' (format parquet)")

    print("targets:", len(out), "| left alone (verified/unverifiable):", n_verified_or_unv)
    print(Counter(o[9] for o in out))
    by_reason: dict[str, Counter] = {}
    for o in out:
        by_reason.setdefault(o[3], Counter())[o[9]] += 1
    for k, c in sorted(by_reason.items()):
        print(f"  {k:32s}", dict(c))


if __name__ == "__main__":
    main()
