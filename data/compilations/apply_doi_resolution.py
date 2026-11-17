"""Write STRONG OpenAlex resolutions into citation_dim (openalex_id, doi_resolved, doi_resolution_source).

Only STRONG rows are written:
  * doi_resolution.parquet      status == 'resolved'     (title search: title, first author and year
                                all agree with exactly one non-dataset work)
  * doi_resolution_bib.parquet  status == 'bib_resolved' (label -> one bibliography entry -> work
                                agreeing on title, first author and year)
  * doi_resolution_source_file.parquet  Francesco only: the source file's own ``Data doi/data link`` column,
                                for labels with exactly ONE distinct data DOI. These are DATASET DOIs;
                                there is no OpenAlex work, so openalex_id stays NULL.
  * doi_check.parquet           STORED DOIs verified against the citation text (verdict verified_title /
                                verified_author_year), written as the canonical OpenAlex DOI so that
                                ``doi_resolved`` is the single trusted key. The two verdicts keep separate
                                source labels (title evidence is stronger than author+year evidence).
                                source_attested / unverifiable / mismatch / autofill_suspect are NOT written.
resolved_weak / ambiguous / no_match / bib_* other rows are NOT written. The stored ``doi`` column is never modified; for rows whose stored DOI
failed verification, ``doi_resolved`` holds the corrected DOI and consumers must prefer it.

Each --apply is a full REFRESH of the three columns: rows previously written (doi_resolution_source
not NULL) are cleared first, inside the same transaction, so a citation that is no longer strong
(e.g. a later bibliography makes its label ambiguous) loses its old value instead of keeping it.

compilations.duckdb is rebuilt from scratch by the central build, which empties these
columns, so re-run this script after every rebuild (it is idempotent). Because ids are
reassigned on rebuild, every row is checked against its (source_key, citation_key) before
writing; any mismatch aborts without writing.

Default is a DRY RUN. ``--apply`` makes a timestamped backup, writes in one transaction,
and asserts the row counts.
Run:  uv run python data/compilations/apply_doi_resolution.py [--apply]
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
DB = HERE / "compilations.duckdb"
SOURCE_LABEL = {"full": "openalex_title_search:full", "trimmed": "openalex_title_search:trimmed"}
BIB_LABEL = {"doi": "bibliography:doi", "title": "bibliography:title"}
STORED_LABEL = {"verified_title": "stored_doi:verified_title", "verified_author_year": "stored_doi:verified_author_year"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    res = duckdb.connect()
    rows = res.execute(
        f"""select citation_id, source_key, openalex_id, resolved_doi,
                   json_extract_string(evidence_json, '$.query') as q, citation_key
            from '{HERE / 'doi_resolution.parquet'}' where status = 'resolved' order by citation_id"""
    ).fetchall()
    assert rows and all(r[2] for r in rows), "title-search strong rows must all carry an openalex_id"
    assert all(r[4] in SOURCE_LABEL for r in rows), "unknown query label in evidence"
    rows = [(r[0], r[1], r[2], r[3], SOURCE_LABEL[r[4]], r[5]) for r in rows]
    bib = res.execute(
        f"""select citation_id, source_key, openalex_id, resolved_doi, route, citation_key
            from '{HERE / 'doi_resolution_bib.parquet'}' where status = 'bib_resolved' order by citation_id"""
    ).fetchall()
    assert all(r[2] and r[4] in BIB_LABEL for r in bib), "bib rows need an openalex_id and a known route"
    bib = [(r[0], r[1], r[2], r[3], BIB_LABEL[r[4]], r[5]) for r in bib]
    assert not ({r[0] for r in rows} & {r[0] for r in bib}), "a citation is strong in both resolution files"
    rows += bib
    pg = res.execute(f"select citation_id, source_key, doi, citation_key from '{HERE / 'doi_resolution_source_file.parquet'}' order by citation_id").fetchall()
    assert all(r[2] and r[2].startswith("10.") for r in pg), "pangaea rows must carry a DOI"
    pg = [(r[0], r[1], None, r[2], "source_file_data_doi", r[3]) for r in pg]
    assert not ({r[0] for r in rows} & {r[0] for r in pg}), "a citation is strong in two resolution files"
    rows += pg
    st = res.execute(
        f"""select citation_id, source_key, openalex_id, oa_doi, verdict, citation_key
            from '{HERE / 'doi_check.parquet'}' where verdict in {tuple(STORED_LABEL)} order by citation_id"""
    ).fetchall()
    assert all(r[2] and r[3] for r in st), "verified stored DOIs must have an openalex_id and canonical DOI"
    st = [(r[0], r[1], r[2], r[3], STORED_LABEL[r[4]], r[5]) for r in st]
    assert not ({r[0] for r in rows} & {r[0] for r in st}), "a citation is both resolved and a verified stored DOI"
    rows += st
    ro = duckdb.connect(str(DB), read_only=args.apply is False)
    cur = {r[0]: r for r in ro.execute(
        "select c.citation_id, s.source_key, c.openalex_id, c.doi_resolved, c.citation_key from citation_dim c "
        "join source_dim s using (source_id)").fetchall()}
    # guard: ids must still point at the same source, and resolution target must be unfilled or identical
    for cid, source_key, oa_id, doi, label, ckey in rows:
        assert cid in cur, f"citation_id {cid} missing from current DB (rebuilt? rerun resolve_citations.py)"
        assert cur[cid][1] == source_key, f"citation_id {cid}: source {cur[cid][1]} != {source_key} (DB rebuilt)"
        assert cur[cid][4] == ckey, f"citation_id {cid}: citation_key {cur[cid][4]!r} != {ckey!r} (DB rebuilt/reordered)"

    n_doi = sum(1 for r in rows if r[3])
    print(f"strong rows: {len(rows)} ({n_doi} with DOI, {len(rows) - n_doi} OpenAlex id only; {len(pg)} are source-file dataset DOIs; {len(st)} are verified stored DOIs); mode={'APPLY' if args.apply else 'dry-run'}")
    if not args.apply:
        return

    backup = DB.with_name(f"compilations.backup-{time.strftime('%Y%m%d-%H%M%S')}.duckdb")
    ro.close()
    shutil.copy2(DB, backup)
    print("backup:", backup.name)
    con = duckdb.connect(str(DB))
    con.execute("begin")
    cleared = con.execute("select count(*) from citation_dim where doi_resolution_source is not null").fetchone()[0]
    con.execute("update citation_dim set openalex_id = NULL, doi_resolved = NULL, doi_resolution_source = NULL "
                "where doi_resolution_source is not null")
    for cid, _, oa_id, doi, label, _ck in rows:
        con.execute(
            "update citation_dim set openalex_id = ?, doi_resolved = ?, doi_resolution_source = ? where citation_id = ?",
            [oa_id, doi, label, cid],
        )
    written = con.execute("select count(*) from citation_dim where doi_resolution_source is not null").fetchone()[0]
    assert written == len(rows), (written, len(rows))
    total = con.execute("select count(*) from citation_dim").fetchone()[0]
    con.execute("commit")
    print(f"cleared {cleared} previously written rows; written: {written} of {total} citation rows")


if __name__ == "__main__":
    main()
