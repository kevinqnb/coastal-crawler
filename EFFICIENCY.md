# EFFICIENCY.md — extraction throughput improvements

Working notes on speeding up `coastal-crawler extract`, written to scale from
the current ~100 papers/3hr rate toward a ~3000-paper run. Items are numbered
so we can reference and tackle them one at a time; they are not in strict
priority order, but roughly: 1-3 are the highest-leverage / lowest-risk, 4 is
a real but bounded structural win, 5 is a flag rather than a decision, and
6-8 are lower-priority items we considered and are deprioritizing for now
(kept here so we don't re-derive them later).

## Baseline measurement

Numbers below come from `scripts/out/extract_out.txt`, a real 100-paper run
(`coastal-crawler extract --batch-size 100`, `EXTRACTION_CHUNK_SIZE=20`) on
2026-07-22. **Caveat: this run used the placeholder measurement schema**
(`measurement_schema.py`) — prompt size and completion length will shift once
the real coastal schema is filled in, so treat per-call numbers as
provisional. The structural findings (serialization, retry behavior,
concurrency utilization) are schema-independent and safe to act on now.

Total wall time: 11,179.3s (~3.1hr) for 100 papers, 0 failures.

| Stage | Total time | Share |
|---|---|---|
| Extraction (`MEAS_LM`, gpt-oss-120b) | 8,032s | 72% |
| OCR (`DOC_LM`, OlmOCR2) | 2,944s | 26% |
| Downloads / claim overhead | 204s | 2% |

Confirmed directly from the logs: `gpu_chunk_done.seconds` equals
`ocr_batch_summary.seconds + extraction_batch_summary.seconds` in every one
of the 5 chunks — OCR and extraction never overlap (see item 4).

**Extraction alone is ~80s/paper.** That is the number that sets the floor:
even with every structural fix in this document applied to a single worker,
3,000 papers is still roughly 3000 × 80s ≈ 67 hours. Getting to something
meaningfully shorter requires more concurrent GPU work per worker (items 2,
3) and/or more workers (item 1) — not just removing serialization.

---

## 1. Run multiple `extract` jobs in parallel

**Status: implemented** — `scripts/wiley_download.py` (the pre-downloader),
`Settings.wiley_pdf_dir`, the `worker.py` local-lookup/requeue path, and
`requeue-processing` all landed. See the "What needs to be built" bullets
below, now marked with their resolution.

**Priority: highest — but requires decoupling Wiley downloads from GPU work
first (see below). Not a zero-code-change item after all.**

`store.claim_batch` (`src/coastal_crawler/db/store.py`) already uses
`SELECT ... FOR UPDATE SKIP LOCKED`, and CLAUDE.md documents this as
supporting multiple concurrent extraction workers by design. In principle
nothing about the current single-worker `coastal-crawler extract
--batch-size 100` prevents running several of these at once against the same
database — each job claims a disjoint set of `relevant` papers and processes
them independently.

**Why that's not enough on its own: the current corpus is 100% Wiley-sourced.**
`pdf.py`'s `_throttle_wiley` (`src/coastal_crawler/pdf.py:27,43-53`) paces
requests to ≥10s apart using a module-level variable
(`_last_wiley_request_at`) that only exists within one Python process. Wiley's
actual published limit — "60 requests per 10 minutes" — works out to exactly
one request per 10s, which is precisely the `_WILEY_MIN_INTERVAL_SECONDS`
constant. That means one process respecting the throttle is already using
100% of the allowed quota, with no margin. A second, uncoordinated process
running the same 10s self-pacing loop doesn't add "some risk" — it pushes
aggregate request rate to ~200% of the limit, which Wiley returns as a bare
HTTP 500 (a disguised `policies.ratelimit.QuotaViolation`, per CLAUDE.md),
surfacing as confusing `failed` papers rather than an obvious rate-limit
error. With every paper in the corpus going through this same path, naively
running N `extract` jobs (each downloading its own claimed batch) is not
viable as-is.

The mitigation is architectural, not a config tweak: **decouple Wiley
downloading from GPU work entirely.** One dedicated process does all Wiley
downloading, respecting the 10s pace with no cross-process coordination
needed (it's the only downloader), and writes PDFs to shared storage ahead of
time. N separate GPU-only worker jobs then just consume already-downloaded
PDFs and never talk to Wiley themselves. This is attractive here specifically
because the Wiley-imposed floor is well under the extraction-side floor: 3000
papers × 10s ≈ 8.3 hours of total download time, against extraction's own
~67-hour floor (see baseline math above) — a single downloader comfortably
stays ahead of even a heavily parallelized GPU pipeline once it's running.

(An alternative considered: a DB-backed shared throttle — e.g. a row holding
the real last-request timestamp, updated via `SELECT ... FOR UPDATE` so every
job's download thread waits on the same clock. This would let the existing
per-job download-thread architecture in `worker.py` keep working unmodified.
Rejected in favor of full decoupling because it still ties GPU-job wall-clock
time to Wiley's pacing — a job can stall mid-batch waiting on the shared
clock — whereas a separate pre-downloading process removes Wiley from the
GPU jobs' critical path altogether.)

**What needs to be built:**

- **New script: `scripts/wiley_download.py`.** A standalone, long-running
  pre-downloader. Queries the DB for papers that need a PDF (Wiley-sourced,
  status `relevant` — i.e. queued for extraction but not yet claimed), and
  for each one not already present in `data/wiley_pdfs/`, downloads it there.
  Should reuse `pdf.py`'s existing `_throttle_wiley`/`download_pdf`/
  `pdf_headers` logic rather than reimplementing the pacing or Wiley auth
  handling — the throttle behavior itself is already correct, it just needs
  to run in one dedicated process instead of inside every extract job.
  Needs to be idempotent/resumable (skip files that already exist on disk)
  since it's meant to run continuously or be restarted across a multi-day
  extraction campaign, staying ahead of however many GPU jobs are consuming
  from the directory.
- **File naming convention: `data/wiley_pdfs/{paper_id}.pdf`**, where
  `paper_id` is `papers.id` (the integer primary key). Chosen over `doi`
  (nullable, and contains `/` characters that aren't filesystem-safe),
  `openalex_id`/`semantic_scholar_id` (also nullable — a paper may only have
  one source ID populated), or the Wiley URL itself (not stable/parseable
  for this). `paper_id` is always present, collision-free, and is already the
  identifier used everywhere else in the pipeline's logging and DB calls
  (`worker.py`'s `paper_extracted`/`paper_failed` events, `store.py`'s
  `insert_extraction(paper_id, ...)`/`mark_extracted(paper_id, ...)`) — using
  it for the filename keeps this consistent with how the rest of the
  codebase already refers to papers, and makes matching a file back to its
  DB row a trivial lookup.
- **Implemented.** `worker.py`'s `_download_all` now takes an optional
  `wiley_pdf_dir` (threaded from `Settings.wiley_pdf_dir`, `config.py`,
  default `data/wiley_pdfs`) and branches on `pdf.py`'s `is_wiley_request`
  (promoted from `_is_wiley_request` to a public helper so both `worker.py`
  and `scripts/wiley_download.py` can reuse it): Wiley papers are looked up
  at `wiley_pdf_dir/{paper_id}.pdf` instead of downloaded; non-Wiley papers
  are unaffected. `_flush_chunk` only deletes the worker's own temp
  downloads (`is_temp=True`) — a file read from the pre-download cache is
  shared/persistent and must survive for reuse across runs and requeues.
- **Open design question — resolved: skip-and-requeue, no `claim_batch`
  change.** A claimed Wiley paper whose file isn't cached yet is reset to
  `relevant` immediately (no wait/poll) and reported as `requeued`, not
  `failed` — see `worker.py`'s `_requeue_undownloaded` and
  `store.reset_processing_to_relevant` (guarded on `status='processing'` so
  it can't stomp a concurrent transition). Chosen over changing
  `claim_batch`'s query because it needed no changes to the shared
  claim/lock path and closes the loop cleanly: `scripts/wiley_download.py`
  either produces a cached file or marks the paper `failed` outright (see
  below), so by the time a paper is claimed while still `relevant`, in
  steady state its file already exists — the only remaining "not ready yet"
  case is the transient race where a paper only just became `relevant`,
  which resolves itself on the next `extract` run once the downloader
  catches up. `coastal-crawler extract`'s output and the `worker_batch_done`
  log line both surface the requeued count so this isn't silent.
- **`scripts/submit_extract_job.sh` update — implemented.** `WILEY_PDF_DIR`
  flows through automatically via `.env`/`Settings`, so no functional change
  was needed there; added a preflight warning (not a hard failure) if the
  directory is empty/missing before starting the vLLM servers, since
  otherwise a job would burn ~10 minutes of GPU allocation just to requeue
  its whole claimed batch and exit.
- Progress: `coastal-crawler extract`'s echo and the `worker_batch_done` log
  line report `requeued` counts across GPU jobs; `scripts/wiley_download.py`
  logs `wiley_download_pass_done` with `downloaded`/`cached`/`failed` counts
  each pass.
- **Resolved: `requeue-processing` added.** `store.requeue_processing`
  (`processing` → `relevant`, bulk) plus a `coastal-crawler requeue-processing`
  CLI command, mirroring `requeue-failed`. Rescues papers stranded by a
  killed extraction job — more important now that multiple concurrent
  extraction jobs are supported.

---

## 2. Raise `MEAS_LM_MAX_CONCURRENT` and `EXTRACTION_CHUNK_SIZE`

**Priority: high — env var change only, cheap to test.**

**Where it lives:** `MEAS_LM_MAX_CONCURRENT` → `Settings.meas_lm_max_concurrent`
(`src/coastal_crawler/config.py:224-227`), default `4`. Read by
`build_extraction_adapter()` (`adapter.py:171`) and passed to `ExtractionLM`'s
constructor, where it bounds an `asyncio.Semaphore` inside `_call_batch`
(`extraction_lm.py:214`) that caps how many extraction API calls are
in flight to the `MEAS_LM` vLLM server at once.

`EXTRACTION_CHUNK_SIZE` → `Settings.extraction_chunk_size`
(`config.py:276-284`), default `20`. Read by `worker.py`'s `run_worker()` via
the CLI (`cli.py`'s `extract` command), it controls how many downloaded
papers are grouped into one `adapter.extract_batch()` call.

**Why it's relevant:** measured effective extraction concurrency in the
baseline run was only ~2.55, against a configured cap of 4
(derived as: sum of individual call durations, including the two
timed-out-and-retried calls, ≈ 20,500 call-seconds over 8,032s of actual
wall-clock ≈ 2.55x parallelism). The gap comes from the `asyncio.gather` in
`_call_batch` (`extraction_lm.py:220`) processing a fixed batch of 20 calls
through a semaphore of 4 — in-flight count ramps down 4→3→2→1 as calls finish
near the tail of the chunk, leaving GPU capacity idle exactly when the chunk
is close to done. vLLM's continuous batching throughput comes from having
enough concurrent requests in flight to amortize weight reads across the
batch (extraction is decode-bound — see item 5's math), so keeping more
requests in flight for more of the chunk's duration should raise aggregate
throughput.

**Suggested experiment:** raise `MEAS_LM_MAX_CONCURRENT` to somewhere in the
12-16 range, and raise `EXTRACTION_CHUNK_SIZE` alongside it so there's enough
work queued to keep the batch full through the tail (a chunk size of 20 with
concurrency 16 barely has one full wave; a larger chunk gives the semaphore
room to stay saturated longer).

**Caveats:**
- Prompts in the baseline run ran 6.6K-43K tokens (median ~19.7K), with
  `max_tokens=32768` requested per call. KV-cache memory, not compute, may
  become the binding constraint before you reach a concurrency of 12-16 —
  watch the `MEAS_LM` vLLM server's own log for request preemption or
  queueing when testing higher values, and back off if you see it.
- This is a single-worker (single GPU) knob. It composes with item 1
  (parallel workers) but doesn't replace it — raising concurrency helps
  utilize the one GPU allocated to `MEAS_LM` per job; running more jobs adds
  more GPUs.
- `DOC_LM_MAX_CONCURRENT` (`config.py:168-171`, default `32`) is the OCR-side
  equivalent. Baseline OCR effective concurrency was already ~19.5 against
  that cap of 32 (1,335 page-calls × ~43s mean ÷ 2,944s wall-clock), so
  there's some headroom there too, but OCR is only 26% of total time — lower
  priority than the extraction-side change.

---

## 3. Stop double-retrying timed-out extraction calls

**Priority: high — small, targeted code change.**

**Where it lives:** `ExtractionLM.__init__` (`extraction_lm.py:124`)
constructs `self.async_client = AsyncOpenAI(api_key=api_key,
base_url=api_base, timeout=2400.0)` without an explicit `max_retries`. The
OpenAI Python SDK defaults `max_retries` to `2` when unset, meaning every
logical API call the SDK makes is retried up to 2 additional times
internally (3 total attempts) on transient failures, including timeouts —
transparently, before `ExtractionLM`'s own code ever sees a failure.

**Why it's relevant:** `ExtractionLM._call_batch` (`extraction_lm.py:193-245`)
already implements its own outer retry loop with exponential backoff
(`max_retries=4` passed from `_extract_triples`, `extraction_lm.py:291`). The
SDK-level retry underneath it is pure duplication, and it's expensive when it
fires: the baseline run shows two calls logged as `extraction_call_failed`
with `error='Request timed out.'` at `seconds=1801.56` and `seconds=1201.36`
— almost exactly 3×600s and 2×600s, where 600s is the per-call timeout passed
to `.create()` in `_acall` (`extraction_lm.py:136,172`). That's the SDK
silently retrying a request that's going to fail anyway, three times over,
before finally surfacing the failure.

It's worse than just wasted retries, though: `_call_batch`'s structure is
`gather()` the whole chunk → *then* check which calls failed → *then*
`gather()` the retries (`extraction_lm.py:220-239`). A single straggler
therefore produces a **fully serialized single-call tail appended after the
rest of the chunk is already done.** Chunk 2 in the baseline log shows this
exactly: the failure is logged at 14:42:14, but `extraction_retry_round`
isn't logged until 14:45:54 (the code was waiting on the other 19 in-flight
calls), and the retry then runs alone — no other work overlapping it — until
it succeeds at 14:58:24. That's a ~750s tail, alone, on a chunk whose other
19 papers finished minutes earlier. This plausibly explains most of the
variance between the baseline run's fastest chunk (725s) and slowest (2,189s).

**Suggested fix:** pass `max_retries=0` (or `1`, if some tolerance for
one-shot transient network blips is wanted) explicitly when constructing
`ExtractionLM`'s `AsyncOpenAI` client, so a slow/stuck request fails fast and
falls through to the existing outer retry logic exactly once, instead of
silently eating up to 3x the timeout first.

**Caveats:**
- This doesn't fix the *cause* of the timeout, just removes the duplicated
  retry overhead. If a call is genuinely taking >600s because it's producing
  a very long completion (see item 5 — `max_tokens=32768` is ~2x the largest
  completion actually observed at 17,054 tokens), lowering `max_tokens` might
  address the root cause; that's a separate, more invasive change since it
  affects extraction quality/completeness and shouldn't be decided from
  throughput data alone.
- Worth also considering whether the retry-tail serialization itself should
  be restructured (e.g., retry a failed call immediately/independently
  rather than waiting for the whole chunk's `gather()` to finish first) —
  a slightly larger change than just the `max_retries` value, and not yet
  scoped in detail.

---

## 4. Pipeline OCR and extraction across chunks

**Priority: medium — real but bounded win (~20% of total time), most
invasive code change of the group.**

**Current behavior:** `DirectExtractionAdapter.extract_batch`
(`adapter.py:92-110`) runs `self.doc_lm.fit(...)` for every document in the
chunk to completion, then `self.meas_lm.fit(...)` for every document in the
chunk to completion — fully serial. Confirmed directly in the baseline log:
`gpu_chunk_done.seconds` equals `ocr_batch_summary.seconds +
extraction_batch_summary.seconds` in all 5 chunks, with no overlap. Two GPUs
are allocated per job (`submit_extract_job.sh`, `#$ -l gpus=2` — one for
`DOC_LM`, one for `MEAS_LM`), but each sits idle while the other stage runs.

**Why it's bounded:** because extraction (72% of total time) so outweighs
OCR (26%), pipelining can't approach a 2x win — overlapping a small stage
under a large one only reclaims the small stage's time. The realistic floor
is roughly `max(ΣOCR, ΣExtraction) + one chunk's worth of OCR` (the first
chunk's OCR still has to happen before any extraction can start) ≈ 8,032s +
~590s ≈ 8,630s, versus the current 10,975s of combined GPU-chunk time — about
a 20% reduction, not a 2x one.

**What the code change could look like:**

The existing download/GPU overlap in `worker.py` is already the right
pattern to generalize — it runs PDF downloads in a background thread
(`_download_all`) feeding a queue that the main thread drains into chunks,
so Wiley's throttle wait overlaps GPU work instead of blocking it
(`worker.py:1-9` docstring, `_download_all` at `worker.py:188-214`). The
same idea can extend one stage further: currently there are two stages
(download thread → GPU-chunk main thread), extraction would become a third.

Sketch of the shape (not a finished design):

- **Split `ExtractionAdapter`'s interface.** Right now `extract_batch()`
  is one call that does OCR-then-extraction atomically (`adapter.py:38-53`
  protocol, `adapter.py:92-110` implementation). Pipelining requires the
  worker to control the interleaving instead of the adapter doing both
  stages internally — so the adapter would need to expose the two stages
  separately, e.g. something like `ocr_batch(pdf_paths) -> list[str]` and
  `extract_from_ocr(ocr_texts) -> list[list[ExtractionResult]]`, with
  `extract_batch()` either removed or kept as a thin wrapper that calls both
  in sequence (for `StubAdapter`/tests, where pipelining doesn't matter).
- **Add a second background stage in `worker.py`.** Generalize the current
  two-stage queue (`_DownloadEvent` queue feeding `_flush_chunk`) into three:
  download thread → OCR thread/stage → extraction stage (main thread, or its
  own thread). Each stage consumes from the previous stage's queue in
  chunk-sized groups and pushes its own chunk-sized output to the next
  queue, so OCR on chunk N+1 can start as soon as chunk N's OCR output is
  handed off, while chunk N is still in extraction.
- **Preserve per-paper bookkeeping across the extra hop.** The current
  `_flush_chunk` (`worker.py:107-145`) ties `paper_id` to `pdf_path` through
  the whole chunk and does the `insert_extraction`/`mark_extracted`/
  `mark_failed` DB work per paper after the single `extract_batch()` call
  returns. With OCR and extraction split into separate stages, that
  `paper_id` association needs to carry through an intermediate
  `(paper_id, ocr_text)` queue item, and error handling needs to move: an
  OCR failure for one paper should be able to `mark_failed` immediately
  without blocking that paper's siblings from proceeding to extraction, and
  without needing to fail the whole chunk (mirroring how `_download_all`
  already handles per-paper failures inline today).
- **Chunk sizing may need to be decoupled.** OCR and extraction don't
  necessarily need matching chunk sizes for the overlap to work; worth
  deciding whether to keep one `EXTRACTION_CHUNK_SIZE` for both stages
  (simpler) or let them differ once the two stages are actually decoupled.
- **Threading vs. asyncio:** `worker.py` already mixes a background
  `threading.Thread` (downloads) with an `asyncio.run()`-per-call model
  inside `OCRLM`/`ExtractionLM` (`_call_batch_with_usage` /
  `_call_batch`, each spinning up their own event loop per call). Adding a
  third stage likely means either another dedicated thread (consistent with
  the current download-thread pattern, simplest to reason about) or
  restructuring the whole worker loop around one shared asyncio event loop
  (larger change, would also let `OCRLM`/`ExtractionLM` share concurrency
  primitives more directly — out of scope unless the thread-based version
  proves insufficient).

This is the most invasive item in this document and changes a public
interface (`ExtractionAdapter`) that `StubAdapter` and tests also implement,
so it's worth scoping as its own piece of work rather than combining with
items 1-3.

---

## 5. Flag: `max_tokens` and `temperature` on the extraction call

**Priority: informational — a question to revisit once the real schema
lands, not a decision to make now.**

**Where they live:** `ExtractionLM.__init__`'s `sampling_params` default
(`extraction_lm.py:110-117`): `temperature: 0.90`, `top_p: 0.95`, `top_k: 64`,
`max_tokens: 2048` — though `_extract_triples` (`extraction_lm.py:287-294`)
overrides `max_tokens` to `32768` for the actual extraction call
specifically.

**Why they're relevant to efficiency:**
- `max_tokens=32768` is roughly 2x the largest completion actually observed
  in the baseline run (17,054 tokens; mean 4,135, median ~3,106). Since
  extraction is decode-bound (mean completion 4,135 tokens at ~45 tok/s ≈
  92s, close to the observed median call time of 78s — decode dominates
  per-call latency roughly 5-10x over prefill), a call that runs away toward
  the cap burns proportionally more GPU time than a well-formed response,
  and a genuinely runaway/looping generation is a plausible explanation for
  calls slow enough to hit the 600s per-call timeout (see item 3). Capping
  `max_tokens` lower would bound worst-case per-call latency, at the risk of
  truncating a legitimately long, information-dense paper's output.
- `temperature=0.90` is high for a structured-extraction task where the
  desired output is closer to deterministic (find-and-report) than creative.
  Higher temperature plausibly increases variance in output length (more
  chance of extra/hallucinated list items before the model emits a closing
  bracket), which would show up as exactly the kind of long-tail latency
  seen in the baseline run's slowest calls. Lowering it is a reasonable
  hypothesis to test.

**Why this is a flag and not a recommendation:** both parameters affect
extraction *quality* — what gets extracted and how completely — not just
speed, and the baseline run was against the placeholder measurement schema
(`measurement_schema.py`), which produces no real measurements. Changing
these now would mean tuning against the wrong workload; the completion-length
distribution will shift once the real schema and its attribute list are
filled in. Revisit once real extraction output exists to evaluate against,
and change one parameter at a time so quality and speed effects can be
attributed separately.

---

## 6. (Deprioritized) OCR-side concurrency headroom

`DOC_LM_MAX_CONCURRENT` (`config.py:168-171`, default `32`) has some
observed headroom — baseline effective OCR concurrency was ~19.5 — but OCR
is only 26% of total wall-clock time, so even fully saturating this knob
caps out well below the impact of items 2-3 on the extraction side. Worth
a quick experiment if it's free to test, not worth dedicated effort.

## 7. (Deprioritized) Parallelizing PDF page rendering

`render_pdf_pages` (`src/coastal_crawler/extraction/pdf_render.py:88-106`)
renders pages one at a time in a loop, each spawning two subprocesses
(`pdfinfo` then `pdftoppm`) via `_get_pdf_page_dimensions` and
`_load_pdf_page`. This looked like a likely bottleneck before measurement —
serial subprocess spawns with no use of the 16 CPU cores requested in
`submit_extract_job.sh` (`-pe omp 16`) — but the data doesn't support
prioritizing it: OCR's 1,335 page-calls average 43s each against 2,944s of
OCR wall-clock time, meaning OCR is already inference-bound (effective
concurrency ~19.5), not subprocess-bound. Rendering overhead is a small
fraction of OCR's already-small (26%) share of total time. Not worth
pursuing until the higher-leverage items above are done and re-measured.

## 8. (Deprioritized) Trimming OCR'd document text before extraction

Considered: dropping low-value sections (references, acknowledgments,
boilerplate headers/footers) from the OCR'd text before it's sent to
`ExtractionLM`, to shrink the ~6.6K-43K token prompts. Math doesn't support
prioritizing it: extraction is decode-bound, not prefill-bound (see item 5's
per-call latency breakdown — decode dominates roughly 5-10x over prefill for
an MoE model at this prompt-length range), so trimming input would plausibly
buy something on the order of 10% of per-call latency, not the bulk of it.
Revisit only after items 1-4 are done, and only if prompt length turns out to
matter more once the real schema (with its own, possibly different, prompt
structure) is in place.
