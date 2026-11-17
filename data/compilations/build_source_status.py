"""Say EXPLICITLY, for every citation and reference, what kind of source it is and why it has
(or lacks) a DOI. Separates "no DOI because it is not a paper" from "no DOI because information
is missing", and flags DOIs that point at datasets rather than papers.

Per citation -> `citation_source_status` (keyed by citation_id; join citation_dim for keys, DOIs and source):
  reference_kind  paper | book_or_chapter | thesis | report | dataset | website_or_database |
                  personal_or_unpublished | unknown        (+ kind_basis = the evidence; kind_conflict)
  doi_status      paper_doi            document with a trusted DOI in doi_resolved
                  paper_openalex_only  document found in OpenAlex but it has no DOI
                  dataset_doi          doi_resolved is a DATASET DOI, not a paper
                  not_a_paper          no DOI, and the evidence says it is not a document
                  paper_doi_missing    evidence says it IS a document, but no DOI was found (info missing)
                  unidentified         cannot tell what it is (usually an author-year label only)
                  doi_kind_unknown     has a DOI/OpenAlex id but its type is not one we map
  paper_candidate_*  for dataset citations only: a companion PAPER found by title (find_paper_for_datasets.py).
                  Only `strong` candidates are shown; they are NOT in doi_resolved and never change doi_status.
Per reference -> `reference_source_status` (best status over its citations) and the view
`measurement_source_status` (one row per measurement). References without any citation row get
`no_citation_record`.

Run: uv run python data/compilations/build_source_status.py [--apply]   (--apply writes the tables)
"""

from __future__ import annotations

import argparse
import shutil
import time
from collections import Counter
from pathlib import Path

import duckdb
from _citation_common import citation_text
from dotenv import dotenv_values

from coastal_crawler.openalex_client import OpenAlexClient, norm_doi
from coastal_crawler.source_kind import (
    DOCUMENT_KINDS, kind_from_doi_prefix, kind_from_openalex_type, kind_from_publication_type, kind_from_text)

HERE = Path(__file__).parent
DB = HERE / "compilations.duckdb"
USER_AGENT = "coastal-crawler/0.1 (compilations source kinds)"
REQUEST_DELAY = 0.1
TIMEOUT = 120
MAX_SERVER_RETRIES = 5
RETRY_BACKOFF = 10.0
BATCH_SIZE = 50
SELECT = "id,doi,type"
# best status for a reference = first match in this order (a pointer to a paper beats everything)
PRIORITY = ["paper_doi", "paper_openalex_only", "dataset_doi", "doi_kind_unknown", "paper_doi_missing",
            "unidentified", "not_a_paper", "no_citation_record"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect(str(DB), read_only=True)
    cur = con.execute(
        """select c.citation_id, c.citation_key, c.reference_id, s.source_key, c.publication_type, c.doi_resolved,
                  c.openalex_id, c.doi_resolution_source, c.title, c.authors, c.bibliography_id, c.year
           from citation_dim c join source_dim s using (source_id) order by c.citation_id""")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    con.close()  # the --apply step reopens the file read-write
    gap = {r[0]: r[1:] for r in duckdb.connect().execute(f"""
        select k.citation_id, k.verdict, r.status, b.status, b.label_status
        from '{HERE / 'doi_check.parquet'}' k
        full join '{HERE / 'doi_resolution.parquet'}' r using (citation_id)
        full join '{HERE / 'doi_resolution_bib.parquet'}' b using (citation_id)""").fetchall()}

    cand = {r[0]: r[1:] for r in duckdb.connect().execute(
        f"select citation_id, citation_key, status, cand_doi, cand_openalex_id from '{HERE / 'paper_candidates.parquet'}'").fetchall()}
    for cid, (ckey, *_rest) in cand.items():
        assert ckey == next(r["citation_key"] for r in rows if r["citation_id"] == cid), f"paper_candidates stale for {cid}"
    client = OpenAlexClient(dotenv_values(HERE.parents[1] / ".env")["OPENALEX_API_KEY"], HERE / "_openalex_cache",
                            USER_AGENT, REQUEST_DELAY, TIMEOUT, MAX_SERVER_RETRIES, RETRY_BACKOFF)
    dois = sorted({norm_doi(r["doi_resolved"]) for r in rows if r["doi_resolved"]})
    by_doi = client.works_by_dois(dois, SELECT, BATCH_SIZE)
    oa_only = sorted({r["openalex_id"] for r in rows if r["openalex_id"] and not r["doi_resolved"]})
    by_id = client.works_by_openalex_ids(oa_only, SELECT, BATCH_SIZE)
    client.close()

    out, conflicts = [], 0
    for r in rows:
        oa_type = None
        if r["doi_resolved"]:
            w = by_doi.get(norm_doi(r["doi_resolved"]))
            oa_type = w["type"] if w else None
        elif r["openalex_id"]:
            w = by_id.get(r["openalex_id"])
            assert w is not None, f"openalex_id {r['openalex_id']} not found in OpenAlex"
            oa_type = w["type"]
        pub = kind_from_publication_type(r["publication_type"])
        oa_kind = kind_from_openalex_type(oa_type) if oa_type else ("unknown", "no_openalex_type")
        if oa_kind[0] != "unknown":  # an OpenAlex type we do not map (e.g. 'other') falls through to weaker evidence
            kind, basis = oa_kind
        elif r["doi_resolution_source"] == "source_file_data_doi":
            kind, basis = "dataset", "source_file_data_link"
        elif pub:
            kind, basis = pub
        elif r["doi_resolved"] and kind_from_doi_prefix(r["doi_resolved"]):
            kind, basis = kind_from_doi_prefix(r["doi_resolved"])
        else:
            kind, basis = kind_from_text(citation_text(r))
        conflict = None
        if pub and kind != "unknown" and pub[0] != kind:
            conflict = f"source says {pub[0]} ({r['publication_type']}), evidence says {kind}"
            conflicts += 1

        if r["doi_resolved"]:
            status = "paper_doi" if kind in DOCUMENT_KINDS else "dataset_doi" if kind == "dataset" else "doi_kind_unknown"
        elif r["openalex_id"]:
            status = "paper_openalex_only" if kind in DOCUMENT_KINDS else "doi_kind_unknown"
        elif kind in ("website_or_database", "personal_or_unpublished", "dataset"):
            status = "not_a_paper"
        elif kind in DOCUMENT_KINDS:
            status = "paper_doi_missing"
        else:
            status = "unidentified"
        g = gap.get(r["citation_id"], (None, None, None, None))
        detail = ";".join(f"{n}={v}" for n, v in zip(("stored_doi_check", "title_search", "bibliography", "label"), g) if v)
        cs = cand.get(r["citation_id"])
        strong = cs is not None and cs[1] == "strong"
        out.append((r["citation_id"], r["citation_key"], r["source_key"], r["reference_id"], kind, basis, conflict, oa_type,
                    status, r["doi_resolved"], r["openalex_id"], r["doi_resolution_source"], detail,
                    cs[1] if cs else None, cs[2] if strong else None, cs[3] if strong else None))
    assert len({o[0] for o in out}) == len(rows) == len(out)

    mem = duckdb.connect()
    mem.execute("""create table citation_source_status (citation_id integer, citation_key varchar, source_key varchar,
        reference_id integer, reference_kind varchar, kind_basis varchar, kind_conflict varchar, openalex_type varchar,
        doi_status varchar, doi_resolved varchar, openalex_id varchar, doi_resolution_source varchar, gap_detail varchar,
        paper_candidate_status varchar, paper_candidate_doi varchar, paper_candidate_openalex_id varchar)""")
    mem.executemany("insert into citation_source_status values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    mem.execute(f"copy citation_source_status to '{HERE / 'citation_source_status.parquet'}' (format parquet)")
    print("citations:", Counter(o[8] for o in out))
    print("kinds:", Counter(o[4] for o in out), "| source-vs-evidence conflicts:", conflicts)

    order = ", ".join(f"'{p}'" for p in PRIORITY)
    ref_ddl = f"""
        create table reference_source_status as
        with c as (select s.*, d.reference_id, d.doi_resolved from citation_source_status s
                   join citation_dim d using (citation_id)),
        ranked as (select reference_id, doi_status, list_position([{order}], doi_status) pr from c)
        select r.reference_id,
          coalesce((select doi_status from ranked where ranked.reference_id = r.reference_id order by pr limit 1),
                   'no_citation_record') as best_status,
          (select count(*) from c where c.reference_id = r.reference_id) as n_citations,
          (select list(distinct doi_resolved order by doi_resolved) from c where c.reference_id = r.reference_id
             and doi_status = 'paper_doi') as paper_dois,
          (select list(distinct doi_resolved order by doi_resolved) from c where c.reference_id = r.reference_id
             and doi_status = 'dataset_doi') as dataset_dois,
          (select list(distinct reference_kind order by reference_kind) from c where c.reference_id = r.reference_id) as kinds,
          (select list(distinct paper_candidate_doi order by paper_candidate_doi) from c where c.reference_id = r.reference_id
             and paper_candidate_doi is not null) as paper_doi_candidates,
          (select count(*) from c where c.reference_id = r.reference_id and paper_candidate_status = 'strong') as n_paper_candidates
        from reference_dim r"""
    view_ddl = """create view measurement_source_status as
        select m.measurement_id, m.reference_id, m.source_id, s.best_status, s.n_citations, s.paper_dois, s.dataset_dois, s.kinds,
          s.paper_doi_candidates, s.n_paper_candidates
        from measurement_fact m left join reference_source_status s using (reference_id)"""
    if not args.apply:
        print("dry-run: tables not written (use --apply)")
        return
    backup = DB.with_name(f"compilations.backup-{time.strftime('%Y%m%d-%H%M%S')}.duckdb")
    shutil.copy2(DB, backup)
    print("backup:", backup.name)
    w = duckdb.connect(str(DB))
    w.execute("begin")
    for obj in ("view measurement_source_status", "table reference_source_status", "table citation_source_status"):
        kind_, name = obj.split()
        w.execute(f"drop {kind_} if exists {name}")
    w.execute("""create table citation_source_status as
        select citation_id, reference_kind, kind_basis, kind_conflict, openalex_type, doi_status, gap_detail,
               paper_candidate_status, paper_candidate_doi, paper_candidate_openalex_id
        from read_parquet('%s')""" % (HERE / "citation_source_status.parquet"))
    w.execute(ref_ddl)
    w.execute(view_ddl)
    n_ref = w.execute("select count(*) from reference_source_status").fetchone()[0]
    assert n_ref == w.execute("select count(*) from reference_dim").fetchone()[0]
    assert w.execute("select count(*) from measurement_source_status").fetchone()[0] == \
        w.execute("select count(*) from measurement_fact").fetchone()[0], "view changed the fact count"
    w.execute("commit")
    print(f"written: citation_source_status {len(out)} rows, reference_source_status {n_ref} rows, view measurement_source_status")


if __name__ == "__main__":
    main()
