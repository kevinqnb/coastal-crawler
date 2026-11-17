"""Steps 1-2: normalize every DOI in citation_dim, look it up in OpenAlex, and check
that the stored metadata (year, title text) agrees with what OpenAlex says.

Writes (next to this script; compilations.duckdb is NOT modified):
  openalex_doi_lookup.parquet   one row per distinct normalized DOI (found or not)
  openalex_doi_report.json      counts + bad-DOI strings + per-citation disagreements

Run:  uv run python -I data/compilations/openalex_verify_dois.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import duckdb
from dotenv import dotenv_values

from coastal_crawler.openalex_client import OpenAlexClient, norm_doi

HERE = Path(__file__).parent
SELECT = "id,doi,title,publication_year,type,authorships,primary_location,biblio,is_retracted"
BATCH_SIZE = 50
USER_AGENT = "coastal-crawler/0.1 (compilations DOI verification)"
REQUEST_DELAY = 0.1
TIMEOUT = 120
MAX_SERVER_RETRIES = 5
RETRY_BACKOFF = 10.0  # seconds, multiplied by attempt number
YEAR_TOLERANCE = 1  # OpenAlex year can differ by 1 (online-first vs issue year)
MIN_TITLE_TOKEN_OVERLAP = 0.6  # fraction of OA title tokens found in stored text


def tokens(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s).lower()
    return set(re.findall(r"[a-z0-9]{3,}", s))


def main() -> None:
    api_key = dotenv_values(HERE.parents[1] / ".env")["OPENALEX_API_KEY"]
    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    cur = con.execute(
        """select c.citation_id, s.source_key, c.doi, c.title, c.authors, c.year, c.citation_key
           from citation_dim c join source_dim s using (source_id) where c.doi is not null"""
    )
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]

    bad: list[dict] = []
    for r in rows:
        try:
            r["doi_norm"] = norm_doi(r["doi"])
        except ValueError:
            bad.append({"citation_id": r["citation_id"], "source": r["source_key"], "doi": r["doi"]})
            r["doi_norm"] = None
    good = [r for r in rows if r["doi_norm"]]
    assert len(good) + len(bad) == len(rows)
    distinct = sorted({r["doi_norm"] for r in good})

    client = OpenAlexClient(api_key, HERE / "_openalex_cache", USER_AGENT, REQUEST_DELAY, TIMEOUT,
                            MAX_SERVER_RETRIES, RETRY_BACKOFF)
    found = client.works_by_dois(distinct, SELECT, BATCH_SIZE)
    client.close()
    assert set(found) <= set(distinct)

    # ---- sidecar table: one row per distinct DOI -------------------------------------
    lookup = []
    for d in distinct:
        w = found.get(d)
        auths = [a["author"]["display_name"] for a in w["authorships"]] if w else None
        loc = (w.get("primary_location") or {}).get("source") if w else None
        oa_doi = norm_doi(w["doi"]) if w else None
        lookup.append(
            (d, w is not None, oa_doi, bool(w) and oa_doi != d,
             w["id"].rsplit("/", 1)[-1] if w else None, w["title"] if w else None,
             w["publication_year"] if w else None, w["type"] if w else None,
             json.dumps(auths) if w else None, loc["display_name"] if loc else None,
             json.dumps(w["biblio"]) if w else None, w["is_retracted"] if w else None)
        )
    out = HERE / "openalex_doi_lookup.parquet"
    mem = duckdb.connect()
    mem.execute(
        """create table t (doi_norm varchar, found boolean, oa_doi varchar, doi_is_alias boolean,
           openalex_id varchar, oa_title varchar,
           oa_year integer, oa_type varchar, oa_authors_json varchar, oa_venue varchar,
           oa_biblio_json varchar, oa_is_retracted boolean)"""
    )
    mem.executemany("insert into t values (?,?,?,?,?,?,?,?,?,?,?,?)", lookup)
    assert mem.execute("select count(*) from t").fetchone()[0] == len(distinct)
    mem.execute(f"copy t to '{out}' (format parquet)")

    # ---- step 2: do stored fields agree with OpenAlex? -------------------------------
    not_found, year_bad, title_bad, checked_year, checked_title = [], [], [], 0, 0
    for r in good:
        w = found.get(r["doi_norm"])
        if w is None:
            not_found.append({"citation_id": r["citation_id"], "source": r["source_key"], "doi": r["doi_norm"]})
            continue
        if r["year"] is not None and w["publication_year"] is not None:
            checked_year += 1
            if abs(r["year"] - w["publication_year"]) > YEAR_TOLERANCE:
                year_bad.append({"citation_id": r["citation_id"], "source": r["source_key"],
                                 "doi": r["doi_norm"], "stored_year": r["year"], "oa_year": w["publication_year"]})
        stored_text = " ".join(x for x in (r["title"], r["authors"], r["citation_key"]) if x)
        oa_toks = tokens(w["title"] or "")
        if oa_toks and r["title"]:  # only rows that store something title-like
            checked_title += 1
            overlap = len(oa_toks & tokens(stored_text)) / len(oa_toks)
            if overlap < MIN_TITLE_TOKEN_OVERLAP:
                title_bad.append({"citation_id": r["citation_id"], "source": r["source_key"],
                                  "doi": r["doi_norm"], "overlap": round(overlap, 2),
                                  "stored": r["title"][:100], "oa_title": w["title"][:100]})

    per_source: dict[str, dict[str, int]] = {}
    for r in rows:
        s = per_source.setdefault(r["source_key"], {"with_doi": 0, "bad_format": 0, "not_in_openalex": 0})
        s["with_doi"] += 1
    for b in bad:
        per_source[b["source"]]["bad_format"] += 1
    for n in not_found:
        per_source[n["source"]]["not_in_openalex"] += 1

    report = {
        "citations_with_doi": len(rows), "bad_format": len(bad), "distinct_dois": len(distinct),
        "found_in_openalex": len(found), "found_via_alias_doi": sum(r[3] for r in lookup),
        "citations_not_found": len(not_found),
        "year_checked": checked_year, "year_disagree": len(year_bad),
        "title_checked": checked_title, "title_disagree": len(title_bad),
        "per_source": per_source, "bad_doi_strings": bad, "not_found": not_found,
        "year_disagreements": year_bad, "title_disagreements": title_bad,
    }
    (HERE / "openalex_doi_report.json").write_text(json.dumps(report, indent=1))
    print({k: v for k, v in report.items() if not isinstance(v, (list, dict))})
    print(json.dumps(per_source, indent=0))


if __name__ == "__main__":
    main()
