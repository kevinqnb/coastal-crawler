---
id: 2026-11-17-compilations-doi-resolution-01
kind: build
---

## Session 2026-11-17

### Prompts
- With the compilations database built, resolve DOIs for its references and citations through OpenAlex, so every measurement can be keyed to the source study's DOI and not only the compilation's own. Walk through the setup first and say whether an API key is needed.
- The key is in `.env`. Keep the scripts in `data/compilations` unless they are general helpers the extraction pipeline could reuse. Start with a data investigation, and test how well a partial search finds a DOI we already know.
- Go ahead with the plan: verify the stored DOIs, then build a citation parser. Foster's autofilled DOIs: try to repair them, otherwise flag and move on. Keep every reference of a measurement. Credits do not matter (US$10 prepaid).
- Leave weak, ambiguous and author-year-only matches alone: resolve only citations with title, author and year agreeing, and write only those strong matches into the database. For Francesco, does the PANGAEA dataset page's source list help? Would the PDFs of the compilation papers help?
- Added the introducing papers (JATS XML for Tang, Strydom, Shao, Francesco, Mouw; PDFs for Davis and Shao's supplement; RIS and PDF for Pessarrodona). The Davis xlsx is all there is, and Strydom's supplement holds no citations. Write Francesco's unique PANGAEA matches.
- Run it all by the reviewer: have we made a best attempt to strongly resolve DOIs?
- `doi_resolved` should be the single trusted key. Keep author+year-verified DOIs below title-verified.
- The point of a DOI is a pointer to the original paper to download for the pilot extraction. A website or dataset is not a paper, and "no DOI because it is not a paper" must be kept apart from "no DOI because information is missing".
- Try to find the companion paper of dataset citations, but it is fine if that does not pan out.
- Clean up unused columns, tables and files, commit, and write this devlog.

### Implemented
OpenAlex needs no key for light use; a free key raises the budget (10,000 credits a day: a search costs 10, a DOI batch lookup 1). Reusable code in `src/coastal_crawler/`: a disk-cached client with DOI batching, alias handling, a bounded 5xx retry that still raises, and the key redacted from errors (`openalex_client`); citation parsing (`citation_parse`); title/author/year acceptance and resolution (`citation_resolve`); bibliography loaders and label-to-entry matching (`bibliography`); and an evidence-ranked source-kind classifier (`source_kind`). 53 hand-built-fixture tests. The scripts, and their parquet sidecars (derived, git-ignored), are in `data/compilations/` and documented in its README ("DOI resolution"); the order to re-run them after each rebuild is there too.

Findings that shaped the work:
- Stored DOIs cannot be trusted. Foster's DOIs were autofilled with an incrementing counter, and most of the incremented values are real DOIs of unrelated papers, so a lookup "finds" them. Every stored DOI is therefore checked against the citation's own text, not just looked up. 2,938 citations carry a DOI: 2,670 verified (2,009 by title, 661 by author and year), the rest mismatched, not found, malformed, `autofill_suspect` or `source_attested` (the DOI is in the compilation's own source list).
- Resolution of the 3,228 citations with no or failed DOI, by title search: 1,995 strong, 114 weak, 75 ambiguous, 487 no match, 428 author-year-only, 129 unparseable. Tested by hiding the DOI of verified citations. On two seeds not used for tuning, the strong tier was 184/185 and 191/192 correct, the weak tier 53/55 and 46/52, recall about 0.79. Dataset records can never be strong (generic series titles match sibling and mirror records).
- Author-year labels were resolved through the introducing paper's bibliography (label to exactly one entry, then the entry's work must agree on title, author and year): Tang 55 of 61, Shao 12, Davis 10; the rest none. A label with a year suffix ("2018b") is refused.
- Francesco's raw file has a `Data doi/data link` column the extractor ignored. It confirmed the five PANGAEA DOIs first written from name-and-year matching, gave 10 citations (9,438 facts), and showed that matching was incomplete (Savidge looked unique but has 6 data DOIs). Seven labels (about 4,400 facts) with several data DOIs each are not written; they need one reference per data DOI in Francesco's extractor.
- The reviewer's checks led to: using the label's year for DOIs whose year column was empty, the `source_attested` and `autofill_suspect` verdicts, `citation_key` in every sidecar so the write-back aborts if ids drifted, a write-back that refreshes its own columns in one transaction (a stale row had survived), and a hand audit of 67 written rows (none wrong).

Database. `doi_resolved` is the single trusted key (4,753 citations; 450,689 of 695,589 facts, 64.8%), with `doi_resolution_source` naming the evidence. `doi_resolved` alone is not a paper pointer, so three derived objects say what each reference is: `citation_source_status`, `reference_source_status` and the view `measurement_source_status`. Facts by `best_status`: `paper_doi` 50.7%, `not_a_paper` 18.2% (websites, databases, personal communications, unpublished, datasets without a DOI), `dataset_doi` 14.0%, `unidentified` 12.9%, `no_citation_record` 1.8% (Mouw ID 2), `paper_openalex_only` 1.7%, `paper_doi_missing` 0.8%. The kind is `unknown` unless OpenAlex's type, the source's own publication type, a data-repository DOI prefix or explicit wording in the label says otherwise. A companion-paper search for dataset citations found 27 strong candidates of 546 (a separate column, never in `doi_resolved`); most of those facts already pointed at the same paper, so it added about 2,000 facts. Cleanup removed leftover probe and audit scripts and columns the status tables duplicated from `citation_dim`.

Things to know. The API key was printed once in a traceback before the client redacted it (rotate it). `Settings` cannot load because of an inline comment on `FILTER_MAX_MODEL_LEN` in `.env`, so the scripts read the key with `dotenv_values`; unrelated and untouched. The wider test suite needs a Postgres test database and was not run, and `repr_worker.py` has one pre-existing unused variable. Re-run the scripts after every central rebuild.

### Commits
fcf25e8 Add OpenAlex client and citation parsing, resolution and source-kind modules
0792b32 Compilations: DOI resolution scripts, source-kind status tables, README
