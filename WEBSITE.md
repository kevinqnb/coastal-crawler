# coastal-crawler results website — progress

Tracks Phase 0 of the plan in `.claude/plans` (results website over existing
extractions). Phases 1 (`weekly_update.py`) and 2 (external hosting + sync)
are deliberately out of scope for this pass — see "Left to do" below.

## Quick start — spin it up and view it in a browser

Assumes Postgres is already up via `scripts/serve_postgres.sh` (check
`qstat`; host is in `/projectnb/mcnet/kevin/my_pgserver_host.txt`).

On the SCC login node (`scc1.bu.edu`):

```bash
source scripts/db_env.sh
uv run uvicorn coastal_crawler.site.app:app --host 0.0.0.0 --port 8123
```

From your own machine, tunnel in and open the browser at the forwarded port:

```bash
ssh -L 8123:localhost:8123 quinnk@scc1.bu.edu
```

Then visit **http://localhost:8123**.

Login-node processes get killed after ~15 minutes idle — fine for a quick
look, but for anything longer-running, submit it as a proper SGE job (like
`scripts/serve_postgres.sh`) instead.

**To stop the server**: `Ctrl-C` if it's running in the foreground of your
SSH session, or from the login node:

```bash
pkill -f "uvicorn coastal_crawler.site.app"
```

## Done

- **Migrations** (`alembic/versions/20260728_*`): `papers.authors` (JSONB),
  `papers.publication_date` (DATE); `extractions.judgement` (VARCHAR) +
  new `votes` table. Applied to the live DB.
- **Discovery mappers** (`sources/openalex.py`, `sources/semantic_scholar.py`,
  `sources/wiley.py`) now capture authors/publication_date for newly
  discovered papers — previously discarded (`"metadata": {}` hardcoded,
  `publication_date` used only for the watermark then dropped).
- **`scripts/backfill_authors.py`**: one-off DOI-keyed Crossref lookup to
  populate authors/publication_date for papers discovered *before* this
  change. **Not yet run** — existing papers' authors/publication_date are
  still NULL on the site until someone runs it (`uv run scripts/backfill_authors.py`,
  ~250 papers × 0.5s delay ≈ 2 min).
- **`db/store.py`**: `list_extractions` (paginated, deduped, excludes
  `data->'context'`), `get_extraction`, `record_vote` (+ majority
  `judgement` recompute).
- **`site/snippets.py`**: pure `find_snippet()` — locates which OCR page
  (via `<page number="N">` tags) a measurement's value came from. Validated
  against 25 random real extraction rows: **25/25 hit rate**.
- **`site/app.py` + templates + `static/style.css`**: FastAPI + Jinja2 app.
  `GET /` (paginated list, attribute facet filter), `GET /extraction/{id}`
  (paper metadata, measurement fields, OCR snippet, vote buttons),
  `POST /extraction/{id}/vote`. Verified end-to-end against the live DB —
  list, detail, snippet rendering, and voting (tally + judgement update)
  all work.
- Tests: `tests/test_snippets.py` (8 tests) and new `store.py` coverage in
  `tests/test_store.py` (`TestListExtractions`, `TestGetExtraction`,
  `TestRecordVote`) — all passing against a real Postgres test DB. Full
  suite: 195 passed, 13 failed — all 13 pre-existing failures confined to
  `tests/test_discovery.py` (references `Settings` fields that don't exist
  in current `config.py`, e.g. `openalex_email`, `semantic_scholar_queries`,
  and patches `discovery.get_settings` which doesn't exist either — the
  test file predates a refactor and was never updated; confirmed via
  `git log`/`git diff` that neither the test file nor `discovery.py` were
  touched this session). One of those tests (`test_no_queries_returns_zero`)
  will hang making a real network call if run without deselecting it —
  the field-name typo means it silently falls through to the real
  `.env` API key/query instead of the intended empty-query short-circuit.
  Not fixed here — out of scope, pre-existing, and touches unrelated
  Wiley/discovery surface.
- `mypy src/`: clean on every file touched this session (`db/models.py`,
  `db/store.py`'s new functions, `sources/*.py`, `site/app.py`,
  `site/snippets.py`). 31 pre-existing errors remain in `db/store.py`'s
  untouched functions (SQLAlchemy `Result.rowcount` stub gaps) plus 2 in
  `config.py` and 1 in `discovery.py` — none introduced this session.
- **Performance fix — list page was taking ~21s (~10s filtered).** Root
  cause confirmed via `EXPLAIN ANALYZE`: every `extractions` row embedded a
  full copy of its paper's OCR text **twice** — once in `data->'context'`
  and once (missed originally, caught after the first migration landed) in
  `provenance->'context'` — so `list_extractions`' dedup query had to
  detoast a ~55-90KB JSONB blob per row just to read `attribute`/`value`/
  `units`, regardless of indexing (confirmed indexes alone don't fix this:
  forcing an Index Scan with `enable_seqscan=off` was still 6s+, since
  Postgres detoasts per row independent of which scan method reads it).
  Fixed with three migrations: `b1c2d3e4f5a6` (expression indexes — kept,
  cheap, but not the actual fix), `c2d3e4f5a6b7` (new `paper_ocr_context`
  table, one row per *paper* instead of one per *measurement*; backfills
  from any one extraction row per paper, then strips `context` from
  `data`), `d3e4f5a6b7c8` (same strip for `provenance`, which the first
  migration missed). `adapter.py`'s `_to_result()` no longer copies
  `context` into either field for new rows; `worker.py` now calls the new
  `store.upsert_paper_ocr_context()` once per paper (not per measurement)
  alongside `insert_extraction()`. `site/app.py`'s snippet lookup now reads
  from `paper_ocr_context` first, falling back to `data.get("context")` for
  any row that predates this change. `list_extractions` also restructured
  into a narrow id-ranking query + a hydration query for just the winning
  page (see store.py docstring) — kept for its own sake even though it
  turned out not to be the thing that made the query fast.
  **Result**: `extractions` table 961MB → 12MB (`VACUUM FULL`, safe since
  the extract job was stopped for this); list page ~21s → ~0.07s, filtered
  ~10s → ~0.03s. New tests: `TestPaperOcrContext` (store.py),
  `test_context_dropped_from_data_and_provenance` (adapter.py),
  `test_ocr_context_stored_once_per_paper` (worker.py) — 139 total passing.
  **`submit_extract_job.sh` needs a fresh run to pick up the new code** —
  it was stopped mid-run for this change and hasn't been restarted yet.

## Key findings while building

- **`Extraction.data`/`provenance` used to carry a full copy of the paper's
  OCR text** in a `context` key (`extraction_lm.py` embeds it per record) —
  **fixed in a later session**, see "Performance fix" above. Now lives once
  per paper in `paper_ocr_context`, not once per measurement.
- **`OCR_DIR` text files aren't actually on disk** for the papers already
  extracted (likely cleaned up after that run finished). The snippet
  feature was originally redesigned around `data['context']` instead of
  reading `OCR_DIR/{paper_id}.txt`, and now reads `paper_ocr_context`
  instead (see "Performance fix") — same reasoning, still no dependency on
  OCR_DIR being populated. A later phase that syncs to an external host was
  already planned to precompute snippets rather than ship `OCR_DIR`, so
  this doesn't change that design — if anything `paper_ocr_context` is a
  smaller, cleaner thing to sync than the old shape was.
- Running `uv sync` after adding the new dependencies (fastapi, uvicorn,
  etc.) pruned `torch`/`transformers` and a number of other packages from
  the venv. They were **not** part of the tracked `uv.lock` (the diff was
  purely additive) and nothing in `src/`/`scripts/` imports them, so this
  looks like leftover manual installs being reconciled away, not a real
  regression — but flagging it in case something outside this repo relied
  on them being present in this venv.
- Migrations had to be run as the `quinnk` OS user via local trust auth
  directly on the Postgres host (`ssh scc-k06.scc.bu.edu`,
  `DATABASE_URL=postgresql://localhost/coastal-crawler`) — the `coastal_app`
  user in `scripts/db_env.sh` doesn't own the tables and can't run DDL.
  Worth remembering for the next migration.

## Left to do

- **Run `scripts/backfill_authors.py`** so existing papers show
  authors/publication date on the site (currently blank for all ~250).
- **Visual/UX pass in an actual browser** — everything above was verified
  via `curl`/response bodies, not a rendered browser. Worth a look before
  calling the aesthetic (light blue, Palatino/Courier New, coastal vibe)
  done.
- **Phase 1** (`scripts/weekly_update.py`): submit/poll discover → filter →
  ocr → extract as one scheduled flow. Design is in the plan file; nothing
  built yet, per this session's scope.
- **Phase 2** (external hosting + `scripts/sync_site_db.py`): sync a
  snippet-bearing, `context`-stripped copy of extractions to an external
  host; app already supports this without code changes (just point
  `DATABASE_URL` at the synced copy) since snippets don't depend on
  `OCR_DIR`.
- **Known limitations to revisit**: `list_extractions`' dedup key
  (`paper_id`, `attribute`, `value`, `units`) won't catch two extraction
  passes that word the same measurement slightly differently (e.g. "12.5"
  vs "12.50"); a tied vote leaves `judgement` unresolved rather than
  picking a side; `voter_hash` (IP+UA hash) is a soft deterrent, not real
  dedup — someone can vote repeatedly from a different browser/network.

## Running it locally

```bash
source scripts/db_env.sh   # or point DATABASE_URL elsewhere
uv run uvicorn coastal_crawler.site.app:app --reload
```

Note: the SCC login node kills processes after ~15 minutes, so this is only
for quick interactive checks there. For anything longer-running, submit it
as a proper SGE job (like `scripts/serve_postgres.sh`) instead of `nohup &`
on the login node directly.
