# CLAUDE.md — coastal-crawler

## What this project is

A multi-stage pipeline that discovers coastal-ecosystem papers from academic APIs, filters them for relevance using an LLM, OCRs their PDFs, and extracts structured measurements from the OCR'd text via a native OCR/extraction pipeline (`src/coastal_crawler/extraction/`). OCR and extraction are two independent stages/processes (`coastal-crawler ocr` and `coastal-crawler extract`) connected only through the DB and a shared directory of OCR text files — they're meant to run concurrently, e.g. as two separate 1-GPU cluster jobs.

This repo conforms to the experiment contract. When implementing an experiment, read `notes/hub/conventions.md` first.

**No magic numbers.** Any value that could plausibly change between runs — batch size, chunk size, seed, model name, prompt text — belongs in a `configs/<id>.yaml`'s `params` (see `scripts/run_experiment.py`), a `Settings` field (`config.py`), or a CLI flag (`cli.py`). It does not get hardcoded in a pipeline stage's implementation.

This is research code. Its output goes into papers. The failure mode that matters is
not a crash — it is code that runs cleanly and produces a number that is quietly
wrong. Optimize for catching that.

## Fail loud

Defensive coding is an anti-pattern here. It converts crashes, which I would notice,
into wrong results, which I would not.

- No bare `except:` and no `except Exception` that swallows and continues.
- No default values for missing config keys. A missing key is a hard error.
- No silent fallbacks — no "if the GPU isn't available, use CPU," no "if the file is
  missing, skip it," no substituting an empty result for a failure.
- Assert at every pipeline boundary: shapes, dtypes, row counts, ID sets, and that
  joins didn't drop or duplicate rows.
- If something is genuinely optional, it gets an explicit flag, not an inferred
  default.

## Staged gates before any full run

Never go from "code written" to "full experiment submitted." Walk the ladder, and say
which rung we are on:

1. **Unit tests** on a tiny hand-built fixture where I can verify the expected output
   by inspection.
2. **Smoke run** — one step, one batch, smallest possible input, run directly in the
   current shell session — never by opening a new interactive or batch session
   yourself. Confirms plumbing, not results.
3. **Tiny end-to-end** whose result I can predict in advance. If it doesn't match the
   prediction, stop; do not scale up.
4. **Full run**, submitted through the wrapper.

Propose this sequence yourself rather than waiting for me to ask.

## Sanity controls, not just tests

Unit tests catch broken code. These catch broken _experiments_. Include them in the
experiment plan, not as an afterthought:

- **Shuffled-label / permutation control** — performance should collapse to chance.
  If it doesn't, something is leaking.
- **Known-answer case** — an input whose correct output I can state up front.
- **Seed determinism** — two runs with the same seed produce identical output. If they
  don't, find out why before interpreting anything.
- **Ablation direction** — removing a component I believe matters should hurt. A
  component that can be deleted with no effect is either useless or unused.

## Guarding the evaluation code

Changes to metric, scoring, or evaluation logic are the highest-risk edits in the
repo, because the natural debugging loop — adjust until the numbers look reasonable —
is indistinguishable from fitting the metric to the hypothesis.

- Never modify eval or metric code as part of "getting the experiment to run." If the
  eval breaks, report it and stop.
- Any change to eval logic is a separate, standalone commit with its own
  justification, and it invalidates every prior number computed with the old version.
  Say that explicitly when proposing such a change.
- When results look surprising, the first hypothesis is a bug in _our_ code, not a
  real effect. Investigate in that order.

## Guarding the eval/metrics paths specifically

The global `~/.claude/settings.json` has an `Edit(**/eval/**)` / `Edit(**/metrics/**)`
ask-rule, but it only matches a literal directory *segment* named `eval` or `metrics`
— it silently matches nothing if this repo organizes scoring code differently (e.g.
a flat `analysis/metrics.py`). If that's the case here, add a project-level
`.claude/settings.json` enumerating the actual scoring/eval file paths under `ask` —
see `hub/safety.md` for the rationale and scholarlm's `.claude/settings.json` for an
example. Don't assume the global pattern is protecting this repo just because it
hasn't complained.

This repo has no eval/scoring code today — `metrics.json` is throughput/outcome
counts (`ocr_done`/`failed`/`requeued`), not an accuracy or quality score (see
`scripts/run_experiment.py`'s metrics shim note). Revisit this section once one
exists.

## Running one pipeline stage as a contract experiment

Each pipeline stage (`filter` / `ocr` / `extract`) is one "experiment" under the contract: `scripts/run_experiment.py configs/<id>.yaml` calls that stage's existing entry point (`run_filter` / `run_ocr_worker` / `run_worker`) directly — no CLI subprocess, no reimplemented logic — and writes its return tuple to `metrics.json` as throughput/outcome counts (e.g. `ocr_done`/`failed`/`requeued`), since no retry/quality metric exists in this repo today. `configs/<id>.yaml`'s `params.stage` selects the stage; `params.env` overrides any `Settings` env var (e.g. `DOC_LM_MODEL`) for that run only, without touching the shared `.env`; the config's top-level `seed` fills that stage's `<ROLE>_SEED`. Submit with `bash scripts/submit.sh <id>`, which reads `params.stage` and qsubs `scripts/run_experiment_job.sh` with the matching GPU profile. Multiple experiments can be submitted at once — each is an independent qsub job; same-stage jobs that land on the same host get their own model-server port automatically (see `scripts/find_free_port.py`), so they don't collide on `.env`'s static `<ROLE>_PORT`.

## Pipeline stages and paper statuses

Papers move through the following statuses in order:

```
discovered → filtering → relevant → ocr_processing → ocr_done → processing → extracted
                       ↘ irrelevant                 ↘ ocr_failed             ↘ failed
```

| Status | Set by | Meaning |
|---|---|---|
| `discovered` | discovery sources | Newly inserted; not yet filtered |
| `filtering` | `claim_batch_for_filter` | Claimed by a filter worker; in progress |
| `inaccessible` | `mark_inaccessible` (legacy — no longer set by `run_filter`) | Historical: PDF URL unreachable when checked during filter. Kept for old rows only; new PDF-accessibility failures now surface as `ocr_failed`/`failed` during OCR/extraction. |
| `relevant` | `mark_relevant` | Passed LLM filter; queued for OCR |
| `irrelevant` | `mark_irrelevant` | No abstract, or rejected by LLM |
| `ocr_processing` | `claim_batch_for_ocr` | Claimed by an OCR worker (`coastal-crawler ocr`); in progress |
| `ocr_done` | `mark_ocr_done` | OCR text written to `OCR_DIR/{paper_id}.txt`; queued for extraction |
| `ocr_failed` | `mark_ocr_failed` | OCR failed — PDF download error (including inaccessible/rate-limited URLs), OCR-model error, empty OCR output, or missing PDF URL; descriptive error text stored |
| `processing` | `claim_batch` | Claimed by an extraction worker (`coastal-crawler extract`); in progress |
| `extracted` | `mark_extracted` | Measurements extracted; results in `extractions` table |
| `failed` | `mark_failed` | Extraction failed — adapter/model error or missing OCR text file; descriptive error text stored |

The filter stage intentionally does not check PDF accessibility — title/abstract are already in the DB from discovery, so relevance is judged with zero network calls. Whether the PDF is downloadable is discovered at OCR time (the only stage that touches the raw PDF), avoiding a double-download and keeping the filter stage from being bottlenecked by a PDF host's rate limit (e.g. Wiley's TDM API — see `pdf.py`'s `_throttle_wiley`).

**OCR (`coastal-crawler ocr`) depends on `scripts/wiley_download.py` for Wiley papers.** `pdf.py`'s Wiley throttle only paces requests within one process, so running multiple `coastal-crawler ocr` jobs in parallel — each downloading its own claimed batch — would blow through Wiley's rate limit. `scripts/wiley_download.py` is the one process that talks to Wiley: it pre-downloads PDFs for `relevant` papers into `WILEY_PDF_DIR` (default `data/wiley_pdfs/{paper_id}.pdf`), and `coastal-crawler ocr` reads Wiley papers' PDFs from there instead of downloading them live (`ocr_worker.py`'s `_download_all`, gated on `Settings.wiley_pdf_dir`). It must be running (or already caught up) for Wiley papers to be OCR'd at all — a claimed Wiley paper whose file isn't cached yet is reset to `relevant` (not `ocr_failed`) and picked up again once the downloader catches up. `coastal-crawler extract` has zero Wiley (or PDF) awareness — it only reads OCR text already written to disk. See EFFICIENCY.md item 1 for the full rationale.

**Extraction waits for OCR.** `coastal-crawler extract`'s `--idle-timeout`/`--poll-interval` options (used by `scripts/submit_extract_job.sh`) let it keep polling `claim_batch` for newly `ocr_done` papers instead of exiting the moment it drains the queue — this is what lets the `ocr` and `extract` jobs run concurrently against a partially-populated `OCR_DIR`, with extraction stopping itself only after no new work has appeared for `idle_timeout` seconds (or immediately once there's provably no upstream work left).

Requeue commands (each skips as many upstream stages as it safely can, given what's already on disk/in the DB):
`requeue-failed` resets `failed` → `ocr_done` (skip re-filter and re-OCR; only measurement extraction failed, and the OCR text file is still valid).
`requeue-processing` resets `processing` → `ocr_done` (rescues papers stranded mid-batch by a killed extraction job — matters more with multiple concurrent extraction jobs).
`requeue-ocr-processing` resets `ocr_processing` → `relevant` (rescues papers stranded mid-batch by a killed OCR job).
`requeue-ocr-failed` resets `ocr_failed` → `relevant` (retry OCR).
`requeue-ocr` resets `ocr_done`/`ocr_failed`/`processing`/`extracted`/`failed` → `relevant` (forces a full re-OCR, e.g. after changing `DOC_LM_MODEL`).
`requeue-filtering` resets `filtering` → `discovered` (rescues papers stranded mid-batch by a killed job).
`requeue-irrelevant` resets `irrelevant` → `discovered` (clears `filter_confidence`; useful after updating the prompt).
`requeue-inaccessible` resets `inaccessible` → `discovered` (legacy — for clearing out rows that predate the filter no longer performing a PDF check; see `scripts/recover_oa_inaccessible.py` / `scripts/diagnose_inaccessible.py` for managing that backlog).

## Key source files

| File | Role |
|---|---|
| `src/coastal_crawler/config.py` | `Settings` — all env vars via pydantic-settings. `get_settings()` is `lru_cache`'d. |
| `src/coastal_crawler/discovery.py` | Orchestrates all enabled discovery sources; calls `store.upsert_papers`. |
| `src/coastal_crawler/sources/` | One file per API source (`openalex.py`, `semantic_scholar.py`, `wiley.py`). Each implements `DiscoverySource` from `sources/base.py`. |
| `src/coastal_crawler/relevance_filter.py` | `AbstractFilter` class (logprob scoring) and `run_filter()` batch function. |
| `src/coastal_crawler/ocr_worker.py` | `run_ocr_worker()` — claims `relevant` papers, downloads PDFs (or reads them from `Settings.wiley_pdf_dir` for Wiley papers), calls the OCR adapter, writes OCR text to `Settings.ocr_dir/{paper_id}.txt`. |
| `src/coastal_crawler/worker.py` | `run_worker()` — claims `ocr_done` papers, reads OCR text from `Settings.ocr_dir`, calls the measurement adapter. `run_worker_until_idle()` layers a poll-and-wait loop on top so extraction can run concurrently with a still-running OCR job. |
| `src/coastal_crawler/pdf.py` | Shared PDF download/throttle logic: `download_pdf`, `is_wiley_request`, `_throttle_wiley` (Wiley's ~10s/request pacing). Used only by `ocr_worker.py` and `scripts/wiley_download.py` — `worker.py` has no PDF/network dependency at all. |
| `scripts/wiley_download.py` | Standalone long-running pre-downloader — the one process that talks to Wiley's TDM API; see the Wiley-OCR-dependency note above. |
| `src/coastal_crawler/adapter.py` | Two independent adapters: `OCRAdapter`/`DirectOCRAdapter`/`StubOCRAdapter`/`build_ocr_adapter()` (wraps `OCRLM`; called by `cli.py ocr`) and `MeasurementAdapter`/`DirectMeasurementAdapter`/`StubMeasurementAdapter`/`build_measurement_adapter()` (wraps `ExtractionLM`; called by `cli.py extract`). |
| `src/coastal_crawler/extraction/` | Native OCR/extraction pipeline (no external dependency): `ocr_lm.py` (`OCRLM`, fast-mode VLM OCR), `extraction_lm.py` (`ExtractionLM`, single-call direct measurement extraction), `pdf_render.py` (PDF page rendering via poppler-utils). |
| `src/coastal_crawler/measurement_schema.py` | Domain-specific `EntitySchema`, `MeasurementEventSchema`, `ATTRIBUTE_INFO_DICT`, `DirectExtractionSchema`, and `build_direct_extraction_prompt()` for `ExtractionLM` — placeholder, fill in before running extraction for real. |
| `src/coastal_crawler/db/models.py` | SQLAlchemy ORM: `Paper`, `Extraction`, `CrawlState`. |
| `src/coastal_crawler/db/store.py` | All SQL — every DB operation lives here; callers own commit/rollback. |
| `src/coastal_crawler/db/engine.py` | `get_session()` context manager. |
| `src/coastal_crawler/cli.py` | Typer CLI: `discover`, `filter`, `ocr`, `extract`, `status`, `requeue-failed`, `requeue-processing`, `requeue-ocr-processing`, `requeue-ocr-failed`, `requeue-ocr`, `requeue-irrelevant`. |
| `alembic/versions/` | One migration file per schema change. |
| `scripts/run_experiment.py` | Experiment-contract adapter: dispatches `configs/<id>.yaml`'s `params.stage` to `run_filter`/`run_ocr_worker`/`run_worker`, writes the standardized `$RUNS_ROOT/<id>/` run directory. |
| `scripts/submit.sh` / `scripts/run_experiment_job.sh` | Contract submit wrapper (`bash scripts/submit.sh <id>`) and the generic SGE job body it qsubs — starts the stage's vLLM server on a private port, waits for health, runs `run_experiment.py`, tears the server down. |

## Relevance filter details

- Uses the OpenAI Python client pointed at any OpenAI-compatible endpoint (vLLM, cloud, etc.)
- `max_tokens=1`, `logprobs=True`, `temperature=FILTER_TEMPERATURE`, `seed=FILTER_SEED`, `top_logprobs=FILTER_TOP_LOGPROBS`
- Confidence = `p_true / (p_true + p_false)` from summed probabilities of case variants in the top-N logprobs
- Papers with no abstract are auto-rejected (`filter_confidence = NULL`)
- If neither `true` nor `false` appears in the top N, the paper is conservatively rejected and a warning is logged
- On API error, the paper is reset to `discovered` (retry next run), not permanently failed
- The user-written `FILTER_RELEVANCE_PROMPT` is a pure system-role instruction describing inclusion/exclusion criteria; the module appends its own output-format instruction (`Respond with only the single word true or false...`)

## Reproducibility — model serving

Three models are served the same way: the filter LLM (`FILTER_*`), the OCR/VLM (`DOC_LM_*`), and the extraction LLM (`MEAS_LM_*`). Each role's `.env` parameters follow the same three groups:

**Inference params** (`<ROLE>_SEED`, plus `FILTER_TEMPERATURE`/`FILTER_TOP_LOGPROBS` for the filter specifically) — passed to every API call by the corresponding client (`relevance_filter.py`, or `OCRLM`/`ExtractionLM` via `build_ocr_adapter()`/`build_measurement_adapter()` in `adapter.py`).

**Serving params** (`<ROLE>_PORT`, `<ROLE>_TENSOR_PARALLEL_SIZE`, `<ROLE>_GPU_MEMORY_UTILIZATION`, `<ROLE>_DTYPE`, `<ROLE>_QUANTIZATION`, `<ROLE>_MAX_MODEL_LEN`) — used only by `scripts/serve_model.sh <ROLE>` to launch vLLM.

**Singularity params** (`<ROLE>_SIF_PATH`) — optional; enables running vLLM inside a Singularity container (recommended on HPC clusters). The HuggingFace cache is taken from `HF_HOME` in the environment — set that in your shell profile or job script, not in `.env`.

`scripts/serve_model.sh <FILTER|DOC_LM|MEAS_LM> [gpu_id]` is the single generalized serving script for all three roles, indirecting through `${ROLE}_*` env vars. The optional `gpu_id` argument pins the server to one GPU via `CUDA_VISIBLE_DEVICES` — needed when colocating multiple servers on one multi-GPU node. `DOC_LM` and `MEAS_LM` now run as separate single-GPU jobs (see `submit_ocr_job.sh`/`submit_extract_job.sh` below) rather than colocated on one node, but `gpu_id` is still useful for local/single-node setups that run both stages together. It has two modes selected automatically:
- **Singularity mode** (if `<ROLE>_SIF_PATH` is set): runs `singularity run --nv` with the HuggingFace cache and (if applicable) local model directory bind-mounted. Singularity shares the host network namespace so `<ROLE>_PORT` is reachable on the host without port mapping.
- **Direct mode** (if `<ROLE>_SIF_PATH` is unset): runs `vllm serve` directly, assuming vLLM is installed in the active environment.

Build the SIF once per model, pinning a specific image tag for reproducibility:
```bash
singularity pull vllm-openai.sif docker://vllm/vllm-openai:v0.6.4
```
The SIF file itself is the reproducibility artifact — its content is immutable once built.

`<ROLE>_PORT` must match the port in `<ROLE>_BASE_URL`. `<ROLE>_SEED` is passed to both the vLLM server (`--seed`) and every API call, making greedy decoding fully deterministic.

On an HPC cluster, each stage submits as a single self-contained job — start the server in the background, wait for health, run the CLI command, kill the server on exit:
```bash
qsub scripts/submit_filter_job.sh    # 1 GPU: filter LLM
qsub scripts/submit_ocr_job.sh       # 1 GPU: DOC_LM, runs `coastal-crawler ocr`
qsub scripts/submit_extract_job.sh   # 1 GPU: MEAS_LM, runs `coastal-crawler extract --idle-timeout ...`
```
Submit the OCR and extract jobs at roughly the same time — they're independent 1-GPU allocations (no longer one combined 2-GPU job), connected only through the DB (`ocr_done` status) and `OCR_DIR` on shared storage. `submit_extract_job.sh` passes `--idle-timeout`/`--poll-interval` so it keeps polling for newly-`ocr_done` papers instead of exiting the moment it catches up to a still-running OCR job. All three job scripts poll readiness via the shared `scripts/wait_for_health.sh <port> <server_pid>` helper and preflight the DB connection via `scripts/check_db.py`. Server and client run on the same node and communicate over `localhost`, so no cross-node service discovery is needed for either job.

`build_ocr_adapter()`/`build_measurement_adapter()` (`adapter.py`) construct the production `DirectOCRAdapter`/`DirectMeasurementAdapter` from `Settings`. `build_ocr_adapter()` raises `RuntimeError` if `DOC_LM_MODEL` is unset; `build_measurement_adapter()` raises if `MEAS_LM_MODEL`/`MEAS_LM_ENTITY_IDENTIFICATION_PROMPT` are unset — both mirror `run_filter()`'s guard for `FILTER_MODEL`/`FILTER_RELEVANCE_PROMPT`. Extraction runs `ExtractionLM`: a single LLM call per document that extracts a flat list of (entity, event, attribute, value, units) records directly, with no intermediate provenance/attribute-detection steps and no page-rendering/table-cleaning pipeline. `build_direct_extraction_prompt()` (`measurement_schema.py`) combines `MEAS_LM_ENTITY_IDENTIFICATION_PROMPT` with `MEASUREMENT_EVENT_PROMPT` and the `ATTRIBUTE_INFO_DICT` attribute list into the single prompt this call needs. The entity schema (`EntitySchema`, `ATTRIBUTE_INFO_DICT`) lives in `measurement_schema.py` as a placeholder — extraction produces zero real measurements until it's filled in for the coastal domain. `OCRLM` is fast-mode only (1024px page renders, no orientation correction) — requires poppler-utils (`pdfinfo`/`pdftoppm`) on the system.

## DB schema notes

- `papers.status` is an untyped `String` — new status values don't require a migration, only behavior changes
- `papers.doi` is the primary cross-source dedup key; `openalex_id` and `semantic_scholar_id` are fallback unique keys
- `papers.paper_metadata` maps to the DB column `metadata` (renamed to avoid shadowing `DeclarativeBase.metadata`)
- `papers.filter_confidence REAL` — NULL means not yet filtered or model emitted no boolean token
- `extractions` rows accumulate — re-running with a new model version adds rows rather than overwriting

## Concurrency

`claim_batch_for_filter`, `claim_batch_for_ocr`, and `claim_batch` all use `SELECT ... FOR UPDATE SKIP LOCKED`. Multiple filter, OCR, and extraction workers can each run in parallel without claiming the same paper — and, since OCR and extraction claim out of different statuses (`relevant` vs `ocr_done`), an `ocr` job and an `extract` job can also run concurrently against each other with no coordination needed beyond the DB. Each batch-claim transaction commits immediately so sibling workers (and the other stage) see the updated status right away.

## Adding a new discovery source

1. Create `src/coastal_crawler/sources/mysource.py` implementing `DiscoverySource` (see `base.py`)
2. Register it in the `registry` dict in `discovery.py`
3. Add any required config fields to `Settings` in `config.py`
4. Document in `.env.example`

## Migrations

Follow the naming convention of existing files:

```bash
alembic revision -m "describe change"   # creates a new file in alembic/versions/
# edit the generated file, then:
alembic upgrade head
```

## Running tests

Tests require a real PostgreSQL test database (no mocking of the DB layer):

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost/crawler_test uv run --with pytest --with pytest-mock pytest
```

The full suite is ~45s wall (~30s of tests + a fixed ~33s import floor from
`scholarlm`/`torch` at collection time — every `pytest` invocation pays that
floor, so running many small subsets back-to-back is slower than one full run).
There is no fast/slow split and no marker tier — 45s is short enough that the
main set *is* the full suite.

For a change scoped to one area, run just its test file(s) — no need to run
everything to confirm working order:

| Changed source | Run |
|---|---|
| `sources/`, `discovery.py` | `pytest tests/test_discovery.py` |
| `relevance_filter.py` | `pytest tests/test_relevance_filter.py` |
| `ocr_worker.py`, `pdf.py` | `pytest tests/test_ocr_worker.py` |
| `worker.py` | `pytest tests/test_worker.py` |
| `judge_worker.py` | `pytest tests/test_judge_worker.py` |
| `adapter.py`, `measurement_schema.py` | `pytest tests/test_adapter.py` |
| `db/store.py`, `db/models.py` | `pytest tests/test_store.py` |
| `warehouse.py`, `scripts/build_warehouse.py` | `pytest tests/test_warehouse.py tests/test_build_warehouse.py` |
| `site/` | `pytest tests/test_app.py tests/test_snippets.py` |
| `tests/conftest.py` (test-env isolation) | `pytest tests/test_config.py`, then the full suite |
| `config.py` | full suite (no single file covers `Settings` behaviour) |

Run the full suite before committing regardless — it's 45s.

**Test isolation:** `tests/conftest.py`'s `_isolate_settings` autouse fixture
builds every `Settings()` with `env_file=None` and the discovery/model
credential env vars stripped, so tests never read the real `.env` and never
reach a real API. `_no_retry_backoff` turns any `sources.http` rate-limit
backoff during a test into an immediate failure. `tests/test_config.py` guards
both — if it fails after a dependency bump, fix the isolation before trusting
any other result (a stale test hitting a live API with a real key once made
the suite take 15 minutes).

Type-checking:

```bash
mypy src/
```
