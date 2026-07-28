"""Extraction worker — reads OCR text files, calls the measurement adapter,
stores results.

Claims papers whose OCR text is already on disk (``status='ocr_done'``,
written by ``ocr_worker.py``) and reads ``ocr_dir/{paper_id}.txt`` directly
— a synchronous local disk read, not a network call, so unlike the OCR
worker this has no need for a background download thread. Papers are
processed in chunks of ``chunk_size`` so multiple documents can run
concurrently against vLLM's continuous batching, rather than one document at
a time.

``run_worker_until_idle`` layers a poll-and-wait loop on top of
``run_worker`` so extraction can start at roughly the same time as OCR and
keep claiming newly-OCR'd papers as they appear, stopping once no new work
has shown up for ``idle_timeout`` seconds.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import structlog

from coastal_crawler.adapter import MeasurementAdapter, StubMeasurementAdapter
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session

log = structlog.get_logger(__name__)

_UPSTREAM_STATUSES = ("relevant", "ocr_processing", "ocr_done")


def run_worker(
    batch_size: int = 10,
    adapter: MeasurementAdapter | None = None,
    chunk_size: int = 20,
    ocr_dir: Path = Path("data/ocr"),
) -> tuple[int, int, int]:
    """Claim a batch of OCR'd papers and run measurement extraction.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple worker processes can
    run concurrently without claiming the same paper.

    The batch-claim transaction is committed immediately so that other
    workers see status='processing' and skip these rows. Claimed papers are
    processed in chunks of ``chunk_size``; each chunk is extracted with a
    single ``adapter.extract_batch()`` call so multiple documents can run
    concurrently against vLLM's continuous batching. Each paper still gets
    its own short DB transaction: either insert extractions + mark_extracted,
    or mark_failed with the error text.

    Args:
        batch_size: Maximum papers to claim in one run.
        adapter:    Measurement adapter. Defaults to StubMeasurementAdapter
            (returns []).
        chunk_size: Papers per extract_batch() call. Callers reading from
            Settings should pass settings.extraction_chunk_size explicitly
            (mirrors how batch_size is threaded through from the CLI).
        ocr_dir: Directory to read OCR text files from (named
            ``{paper_id}.txt``, written by ocr_worker.py). A claimed paper
            whose file isn't there (shouldn't happen given ocr_worker.py's
            write-then-commit ordering, but disk issues are possible) is
            reset to 'relevant' (counted in ``requeued``, not ``failed``) so
            it's picked back up once OCR catches up, instead of being
            treated as an extraction failure.

    Returns:
        (extracted, failed, requeued) counts for the batch.
    """
    _adapter = adapter if adapter is not None else StubMeasurementAdapter()

    with get_session() as session:
        papers = store.claim_batch(batch_size, session)
        paper_ids = [p.id for p in papers]
    # status='processing' now committed; session closed

    log.info("worker_batch_claimed", count=len(paper_ids))

    if not paper_ids:
        return 0, 0, 0

    t_batch0 = time.monotonic()
    extracted = 0
    failed = 0
    requeued = 0

    def _process_chunk(chunk_paper_ids: list[int]) -> None:
        nonlocal extracted, failed, requeued

        ids: list[int] = []
        ocr_texts: list[str] = []
        for paper_id in chunk_paper_ids:
            path = ocr_dir / f"{paper_id}.txt"
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("paper_ocr_text_missing", paper_id=paper_id, error=str(exc))
                with get_session() as session:
                    store.reset_processing_to_relevant(paper_id, session)
                requeued += 1
                continue
            ids.append(paper_id)
            ocr_texts.append(text)

        if not ids:
            return

        log.info("gpu_chunk_started", chunk_size=len(ids))
        t0 = time.monotonic()
        batch_error: Exception | None = None
        try:
            batch_results = _adapter.extract_batch(ocr_texts)
        except Exception as exc:
            batch_results = [[] for _ in ids]
            batch_error = exc
        log.info("gpu_chunk_done", chunk_size=len(ids), seconds=round(time.monotonic() - t0, 2))

        for paper_id, outcome in zip(ids, batch_results):
            with get_session() as session:
                try:
                    if batch_error is not None:
                        raise batch_error
                    # A str outcome means extraction failed for this specific
                    # document (e.g. the model's response was unparseable
                    # even after retries) — surface it as a failure for this
                    # paper alone, not a clean extraction of zero
                    # measurements, so requeue-failed can retry it.
                    if isinstance(outcome, str):
                        raise RuntimeError(outcome)
                    for result in outcome:
                        store.insert_extraction(paper_id, result, session)
                    store.mark_extracted(paper_id, session)
                    extracted += 1
                    log.info("paper_extracted", paper_id=paper_id, measurements=len(outcome))
                except Exception as exc:
                    # Roll back any flushed-but-uncommitted extraction rows
                    # before recording the failure, so we don't persist
                    # partial results.
                    session.rollback()
                    store.mark_failed(paper_id, str(exc)[:2000], session)
                    failed += 1
                    log.warning("paper_failed", paper_id=paper_id, error=str(exc))

    for i in range(0, len(paper_ids), chunk_size):
        _process_chunk(paper_ids[i : i + chunk_size])

    seconds = round(time.monotonic() - t_batch0, 2)
    total = extracted + failed
    papers_per_hour = round(total / seconds * 3600, 1) if seconds > 0 else None
    log.info(
        "worker_batch_done",
        extracted=extracted,
        failed=failed,
        requeued=requeued,
        seconds=seconds,
        papers_per_hour=papers_per_hour,
    )
    return extracted, failed, requeued


def run_worker_until_idle(
    batch_size: int = 10,
    adapter: MeasurementAdapter | None = None,
    chunk_size: int = 20,
    ocr_dir: Path = Path("data/ocr"),
    poll_interval: float = 60.0,
    idle_timeout: float = 1800.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> tuple[int, int, int]:
    """Repeatedly claim and extract batches, waiting for new OCR output.

    Calls ``run_worker()`` in a loop so extraction can start at roughly the
    same time as a concurrently-running OCR job and keep picking up newly
    ``ocr_done`` papers as they appear, even against a partially-populated
    OCR directory. The idle clock resets on any batch that claims at least
    one paper; once a batch claims nothing, it starts (or continues) an idle
    timer and sleeps ``poll_interval`` before trying again. Returns once
    ``idle_timeout`` seconds have elapsed with no new claims, or immediately
    once there is no upstream work left at all (``relevant``/
    ``ocr_processing``/``ocr_done`` all empty) rather than waiting out the
    full idle window for a queue that's provably drained.

    ``sleep_fn``/``now_fn`` are injectable so tests don't sleep for real.

    Returns:
        (extracted, failed, requeued) counts summed across every batch.
    """
    total_extracted = total_failed = total_requeued = 0
    idle_start: float | None = None

    while True:
        extracted, failed, requeued = run_worker(batch_size, adapter, chunk_size, ocr_dir)
        total_extracted += extracted
        total_failed += failed
        total_requeued += requeued

        # requeued does NOT count as progress: it means a claimed paper's
        # OCR file was missing and got bounced back to 'relevant', not that
        # real work happened. Treating it as progress would let a
        # misconfigured ocr_dir (or a backlog of pre-split rows with no
        # OCR file) reset the idle clock every iteration with no sleep in
        # between, busy-looping claim/requeue against the DB forever.
        if extracted + failed > 0:
            idle_start = None
            continue

        if idle_start is None:
            idle_start = now_fn()
        if now_fn() - idle_start >= idle_timeout:
            break

        with get_session() as session:
            counts = store.count_by_status(session)
        if sum(counts.get(s, 0) for s in _UPSTREAM_STATUSES) == 0:
            break

        sleep_fn(poll_interval)

    return total_extracted, total_failed, total_requeued


def requeue_failed() -> int:
    """Reset all papers with status='failed' back to 'ocr_done'.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_failed(session)


def requeue_processing() -> int:
    """Reset all papers with status='processing' back to 'ocr_done'.

    Rescues papers stranded mid-batch by an extraction job that was killed
    (walltime limit, OOM, node preemption) before it could mark them
    extracted or failed.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_processing(session)
