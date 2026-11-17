"""Find the companion PAPER for dataset citations (SERC / Dryad / PANGAEA records that carry a paper's title).

Targets (from citation_source_status): doi_status == 'dataset_doi', plus reference_kind == 'dataset'
rows with no DOI that have a title. For each, search OpenAlex by the dataset's cleaned title and keep
only NON-dataset works. A candidate is `strong` when ALL hold:
  * title Jaccard >= MIN_JACCARD with the cleaned dataset title,
  * it shares a first-author surname with the dataset record (first AUTHOR_WINDOW authors each),
  * its year is not later than the dataset's year + 1 (a paper cannot post-date its data by much),
  * exactly one distinct work passes.
Everything else is `weak` (title matches but author/year evidence is missing or contradicts),
`ambiguous` or `none`. Candidates are reported in a SEPARATE column; they never enter doi_resolved.

Output: paper_candidates.parquet. Run: uv run python data/compilations/find_paper_for_datasets.py
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import duckdb
from dotenv import dotenv_values

from coastal_crawler.citation_parse import fold
from coastal_crawler.citation_resolve import toks
from coastal_crawler.openalex_client import OpenAlexClient, norm_doi

HERE = Path(__file__).parent
USER_AGENT = "coastal-crawler/0.1 (dataset companion papers)"
REQUEST_DELAY = 0.1
TIMEOUT = 120
MAX_SERVER_RETRIES = 5
RETRY_BACKOFF = 10.0
BATCH_SIZE = 50
PER_PAGE = 10
MIN_JACCARD = 0.9
AUTHOR_WINDOW = 5
SELECT = "id,doi,title,publication_year,authorships,type"
_PREFIX = re.compile(r"^(?:dataset:\s*|data from:?\s*|data for:?\s*|supplement(?:ary)?(?: original| material)?(?: data)?(?: to| for)?:?\s*)", re.I)
_SUFFIX = re.compile(r"\s*[\(\[]?(?:version|v)\s*\d[\w.]*[\)\]]?\s*$", re.I)


def clean_title(t: str) -> str:
    t = t.strip().strip("{}'\"“”")
    t = _PREFIX.sub("", t)
    return _SUFFIX.sub("", t).strip().strip("'\"“”")


def jaccard(a: str, b: str) -> float:
    ta, tb = toks(a), toks(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def surnames(work: dict) -> set[str]:
    return {fold(a["author"]["display_name"]).replace("-", " ").split()[-1]
            for a in work["authorships"][:AUTHOR_WINDOW] if a["author"]["display_name"]}


def main() -> None:
    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    cur = con.execute(
        """select s.citation_id, s.citation_key, s.source_key, s.doi_status, s.reference_kind, s.doi_resolved,
                  c.title, c.authors, c.year
           from citation_source_status s join citation_dim c using (citation_id)
           where s.doi_status = 'dataset_doi' or (s.reference_kind = 'dataset' and s.doi_status = 'not_a_paper')
           order by s.citation_id""")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    con.close()

    client = OpenAlexClient(dotenv_values(HERE.parents[1] / ".env")["OPENALEX_API_KEY"], HERE / "_openalex_cache",
                            USER_AGENT, REQUEST_DELAY, TIMEOUT, MAX_SERVER_RETRIES, RETRY_BACKOFF)
    dois = sorted({norm_doi(r["doi_resolved"]) for r in rows if r["doi_resolved"]})
    ds_works = client.works_by_dois(dois, SELECT, BATCH_SIZE)

    out = []
    for r in rows:
        w = ds_works.get(norm_doi(r["doi_resolved"])) if r["doi_resolved"] else None
        title = (w["title"] if w and w["title"] else r["title"])
        ds_year = (w["publication_year"] if w else None) or r["year"]
        ds_authors = surnames(w) if w else set()
        if not title or len(toks(title)) < 4:
            out.append((r["citation_id"], r["citation_key"], r["source_key"], r["doi_resolved"], title, None, None, None, None, None, None, "none:no_usable_title"))
            continue
        q = clean_title(title)
        body = client.get({"search": q[:300], "per-page": PER_PAGE, "select": SELECT})
        passing = []
        for c in body["results"]:
            if c["type"] == "dataset" or not c["title"] or not toks(c["title"]):
                continue
            j = jaccard(c["title"], q)
            if j < MIN_JACCARD:
                continue
            shared = bool(ds_authors & surnames(c))
            late = bool(ds_year and c["publication_year"] and c["publication_year"] > ds_year + 1)
            passing.append((c, j, shared, late))
        ids = list(dict.fromkeys(p[0]["id"] for p in passing))
        if not passing:
            status, best = "none", None
        elif len(ids) > 1:
            status, best = "ambiguous", passing[0]
        else:
            c, j, shared, late = passing[0]
            status = "strong" if shared and not late else "weak"
            best = passing[0]
        if best:
            c, j, shared, late = best
            out.append((r["citation_id"], r["citation_key"], r["source_key"], r["doi_resolved"], q, c["id"].rsplit("/", 1)[-1],
                        norm_doi(c["doi"]) if c.get("doi") else None, c["title"], c["publication_year"], round(j, 3), shared, status))
        else:
            out.append((r["citation_id"], r["citation_key"], r["source_key"], r["doi_resolved"], q, None, None, None, None, None, None, status))
    client.close()
    assert len({o[0] for o in out}) == len(rows)

    mem = duckdb.connect()
    mem.execute("""create table t (citation_id integer, citation_key varchar, source_key varchar, dataset_doi varchar,
        dataset_title varchar, cand_openalex_id varchar, cand_doi varchar, cand_title varchar, cand_year integer,
        jaccard double, shared_author boolean, status varchar)""")
    mem.executemany("insert into t values (?,?,?,?,?,?,?,?,?,?,?,?)", out)
    mem.execute(f"copy t to '{HERE / 'paper_candidates.parquet'}' (format parquet)")
    print("targets:", len(rows), Counter(o[11].split(":")[0] for o in out))
    print(Counter((o[2], o[11].split(":")[0]) for o in out).most_common(14))


if __name__ == "__main__":
    main()
