"""Verify every stored DOI against the citation's OWN text (not just "does the DOI exist").

For each citation_dim row with a DOI, compares the OpenAlex record for that DOI with the
parsed citation text. Writes doi_check.parquet (one row per citation) with a verdict:

  verified_title        parsed title matches the OpenAlex title
  verified_author_year  no title in text, but first author and year agree
  mismatch_title        text has a title and it does NOT match -> DOI is wrong (e.g. autofill)
  mismatch_author_year  no title; first author or year disagrees
  autofill_suspect      DOI shared by several citations of one source with different years, and this
                        row's year is not the OpenAlex year (drag-filled label, e.g. 'West and Larkum 1980')
  source_attested       label/text cannot verify it (author/year differ or text too thin), but the DOI is
                        printed in the compilation's OWN source list (PANGAEA JSON-LD for Francesco, the
                        paper's bibliography for Shao). Evidence of a different kind: not label-verified.
  unverifiable          text too thin to check (e.g. only a DOI)
  not_found             DOI not in OpenAlex
  malformed             string is not DOI-shaped
  oa_no_title           OpenAlex record has no title and text has no usable author/year

Nothing is written to compilations.duckdb. Requires openalex_doi_lookup.parquet
(openalex_verify_dois.py). Run:  uv run python data/compilations/build_doi_check.py
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import duckdb
from _citation_common import author_match, citation_text, title_match, year_match

import json

from coastal_crawler.bibliography import load_jats, parse_label
from coastal_crawler.citation_parse import parse_citation
from coastal_crawler.openalex_client import norm_doi

HERE = Path(__file__).parent


def source_list_dois() -> dict[str, set[str]]:
    """DOIs printed in each compilation's own source list, by source_key."""
    pg = json.loads((HERE / "francesco-phytoplankton" / "pangaea_sources.json").read_text())["sources"]
    fran = {norm_doi(x["identifier"]) for x in pg if x["identifier"].startswith("https://doi.org/")}
    shao = {e.doi for e in load_jats(HERE / "shao-nfix" / "essd-15-3673-2023.xml") if e.doi}
    return {"francesco-phytoplankton": fran, "shao-nfix": shao}


def label_year(text: str | None) -> int | None:
    """Year printed in the label text, for rows whose ``year`` column is empty."""
    lab = parse_label(text) if text else None
    return lab.year if lab else None


def flag_autofill(out: list[tuple], years: dict[int, int | None], oa_years: dict[str, int | None]) -> list[tuple]:
    """verified_author_year rows whose DOI is shared within a source by rows with different years,
    and whose own year is not the OpenAlex year, become autofill_suspect."""
    groups: dict[tuple[str, str], set[int | None]] = {}
    for o in out:
        if o[4]:
            groups.setdefault((o[1], o[4]), set()).add(years[o[0]])
    flagged = []
    for o in out:
        if o[5] == "verified_author_year" and len(groups[(o[1], o[4])]) > 1 and years[o[0]] != oa_years[o[4]]:
            o = (*o[:5], "autofill_suspect", *o[6:])
        flagged.append(o)
    return flagged


def main() -> None:
    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    cur = con.execute(
        """select c.citation_id, s.source_key, c.citation_key, c.bibliography_id, c.doi, c.title,
                  c.authors, c.year from citation_dim c join source_dim s using (source_id)
           where c.doi is not null order by c.citation_id"""
    )
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    oa = {
        r[0]: r
        for r in duckdb.connect().execute(
            f"select doi_norm, found, oa_doi, openalex_id, oa_title, oa_year, oa_authors_json "
            f"from '{HERE / 'openalex_doi_lookup.parquet'}'"
        ).fetchall()
    }

    out = []
    for r in rows:
        try:
            d = norm_doi(r["doi"])
        except ValueError:
            out.append((r["citation_id"], r["source_key"], r["citation_key"], r["doi"], None, "malformed", None, None, None))
            continue
        _, found, oa_doi, oa_id, oa_title, oa_year, oa_auth = oa[d]
        if not found:
            out.append((r["citation_id"], r["source_key"], r["citation_key"], r["doi"], d, "not_found", None, None, None))
            continue
        text = citation_text(r)
        p = parse_citation(text, r["year"], title_field=r["title"], authors_field=r["authors"]) if text else None
        if p and p.title and oa_title:
            verdict = "verified_title" if title_match(oa_title, p.title) else "mismatch_title"
        elif p and p.first_author and (p.year or label_year(text)) and oa_auth and oa_year:
            ok = author_match(p.first_author, oa_auth) and year_match(p.year or label_year(text), oa_year)
            verdict = "verified_author_year" if ok else "mismatch_author_year"
        elif text is None:
            verdict = "unverifiable"
        else:
            verdict = "oa_no_title" if not oa_title else "unverifiable"
        out.append((r["citation_id"], r["source_key"], r["citation_key"], r["doi"], d, verdict, oa_doi, oa_id, p.style if p else None))

    lists = source_list_dois()
    out = [(*o[:5], "source_attested", *o[6:]) if o[5] in ("mismatch_author_year", "unverifiable", "mismatch_title")
           and o[4] in lists.get(o[1], ()) else o for o in out]
    out = flag_autofill(out, {r["citation_id"]: (r["year"] or label_year(citation_text(r))) for r in rows},
                        {d: v[5] for d, v in oa.items()})
    assert len(out) == len(rows) and len({o[0] for o in out}) == len(rows)
    mem = duckdb.connect()
    mem.execute(
        """create table t (citation_id integer, source_key varchar, citation_key varchar, stored_doi varchar, doi_norm varchar,
           verdict varchar, oa_doi varchar, openalex_id varchar, parse_style varchar)"""
    )
    mem.executemany("insert into t values (?,?,?,?,?,?,?,?,?)", out)
    mem.execute(f"copy t to '{HERE / 'doi_check.parquet'}' (format parquet)")

    print(Counter(o[5] for o in out))
    by_src: dict[str, Counter] = {}
    for o in out:
        by_src.setdefault(o[1], Counter())[o[5]] += 1
    for s, c in sorted(by_src.items()):
        print(f"{s:28s}", dict(c))


if __name__ == "__main__":
    main()
