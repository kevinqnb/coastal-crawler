"""Extraction worker — downloads PDFs, calls the adapter, stores results.

PDF downloads for the batch run in a background thread (``_download_all``)
while the main thread runs OCR + extraction for already-downloaded papers in
chunks (``extraction_chunk_size`` papers per ``adapter.extract_batch()``
call). This overlaps two otherwise-serial costs: Wiley's TDM rate limit
(10s between requests — see ``pdf.py``'s ``_throttle_wiley``) is paid by the
download thread while the GPUs are busy processing the previous chunk,
rather than blocking them.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Literal

import httpx
import structlog

from coastal_crawler.adapter import ExtractionAdapter, StubAdapter
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session
from coastal_crawler.pdf import download_pdf

log = structlog.get_logger(__name__)

_ERROR_BODY_PREVIEW_LEN = 500

# One item per claimed paper, in claim order: either a successful download
# ("downloaded", paper_id, pdf_path) or a download failure ("failed",
# paper_id) — the paper is already marked failed in the DB by the time a
# "failed" item is queued. `None` signals the downloader thread is done.
_DownloadEvent = tuple[Literal["downloaded"], int, Path] | tuple[Literal["failed"], int]


def _describe_http_status_error(exc: httpx.HTTPStatusError) -> str:
    """Build an error string that includes the HTTP response body.

    str(exc) reports only the status code and URL (e.g. "Server error '500
    Internal Server Error' for url '...'") — it never includes the response
    body. For Wiley's TDM API, the actually useful diagnostic (e.g. a
    disguised rate-limit violation returned as a bare HTTP 500 by Wiley's
    Apigee gateway) lives in that body, so append a preview of it.
    """
    body = (exc.response.text or "").strip()
    if body:
        return f"{exc}: {body[:_ERROR_BODY_PREVIEW_LEN]}"
    return f"{exc} (empty response body)"


def run_worker(
    batch_size: int = 10,
    adapter: ExtractionAdapter | None = None,
    chunk_size: int = 20,
) -> tuple[int, int]:
    """Claim a batch of relevant papers and run extraction.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple worker processes can
    run concurrently without claiming the same paper.

    The batch-claim transaction is committed immediately so that other workers
    see status='processing' and skip these rows. Downloads then run in a
    background thread, feeding a queue that the main thread drains into
    chunks of ``chunk_size`` papers; each chunk is extracted with a single
    ``adapter.extract_batch()`` call so OCR/extraction for multiple documents
    can run concurrently against vLLM's continuous batching instead of one
    document at a time. Each paper still gets its own short DB transaction:
    either insert extractions + mark_extracted, or mark_failed with the
    error text.

    Args:
        batch_size: Maximum papers to claim in one run.
        adapter:    Extraction adapter. Defaults to StubAdapter (returns []).
        chunk_size: Papers per extract_batch() call. Callers reading from
            Settings should pass settings.extraction_chunk_size explicitly
            (mirrors how batch_size is threaded through from the CLI).

    Returns:
        (extracted, failed) counts for the batch.
    """
    _adapter = adapter if adapter is not None else StubAdapter()

    with get_session() as session:
        papers = store.claim_batch(batch_size, session)
        paper_data = [(p.id, p.oa_pdf_url, p.discovered_from) for p in papers]
    # status='processing' now committed; session closed

    log.info("worker_batch_claimed", count=len(paper_data))

    if not paper_data:
        return 0, 0

    t_batch0 = time.monotonic()
    result_queue: queue.Queue[_DownloadEvent | None] = queue.Queue()
    downloader = threading.Thread(
        target=_download_all, args=(paper_data, result_queue), daemon=True
    )
    downloader.start()

    extracted = 0
    failed = 0
    chunk: list[tuple[int, Path]] = []

    def _flush_chunk() -> None:
        nonlocal extracted, failed
        if not chunk:
            return

        paper_ids = [pid for pid, _ in chunk]
        pdf_paths = [path for _, path in chunk]

        log.info("gpu_chunk_started", chunk_size=len(chunk))
        t0 = time.monotonic()
        batch_error: Exception | None = None
        try:
            batch_results = _adapter.extract_batch(pdf_paths)
        except Exception as exc:
            batch_results = [[] for _ in chunk]
            batch_error = exc
        log.info("gpu_chunk_done", chunk_size=len(chunk), seconds=round(time.monotonic() - t0, 2))

        for paper_id, pdf_path, results in zip(paper_ids, pdf_paths, batch_results):
            with get_session() as session:
                try:
                    if batch_error is not None:
                        raise batch_error
                    for result in results:
                        store.insert_extraction(paper_id, result, session)
                    store.mark_extracted(paper_id, session)
                    extracted += 1
                    log.info("paper_extracted", paper_id=paper_id, measurements=len(results))
                except Exception as exc:
                    # Roll back any flushed-but-uncommitted extraction rows
                    # before recording the failure, so we don't persist
                    # partial results.
                    session.rollback()
                    store.mark_failed(paper_id, str(exc)[:2000], session)
                    failed += 1
                    log.warning("paper_failed", paper_id=paper_id, error=str(exc))
            pdf_path.unlink(missing_ok=True)

        chunk.clear()

    while True:
        item = result_queue.get()
        if item is None:
            break
        if item[0] == "downloaded":
            _, paper_id, pdf_path = item
            chunk.append((paper_id, pdf_path))
            if len(chunk) >= chunk_size:
                _flush_chunk()
        else:
            failed += 1
    _flush_chunk()
    downloader.join()

    seconds = round(time.monotonic() - t_batch0, 2)
    total = extracted + failed
    papers_per_hour = round(total / seconds * 3600, 1) if seconds > 0 else None
    log.info(
        "worker_batch_done",
        extracted=extracted,
        failed=failed,
        seconds=seconds,
        papers_per_hour=papers_per_hour,
    )
    return extracted, failed


def requeue_failed() -> int:
    """Reset all papers with status='failed' back to 'relevant'.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_failed(session)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _download_all(
    paper_data: list[tuple[int, str | None, str | None]],
    result_queue: queue.Queue[_DownloadEvent | None],
) -> None:
    """Download every paper's PDF in order, pushing results to a queue.

    Runs in a background thread so downloads for later papers (including
    Wiley's ~10s/request throttle — see ``pdf.py``) overlap with GPU
    processing of earlier chunks in the main thread. Download failures are
    marked failed in the DB immediately, from this thread, using their own
    session (SQLAlchemy sessions are safe to open per-call against a shared
    thread-safe Engine).
    """
    for paper_id, oa_pdf_url, discovered_from in paper_data:
        t0 = time.monotonic()
        try:
            if not oa_pdf_url:
                raise ValueError("No open-access PDF URL available")
            pdf_path = download_pdf(oa_pdf_url, discovered_from)
            log.info("paper_downloaded", paper_id=paper_id, seconds=round(time.monotonic() - t0, 2))
            result_queue.put(("downloaded", paper_id, pdf_path))
        except httpx.HTTPStatusError as exc:
            reason = _describe_http_status_error(exc)
            _fail_download(paper_id, reason, time.monotonic() - t0, result_queue)
        except Exception as exc:
            _fail_download(paper_id, str(exc), time.monotonic() - t0, result_queue)
    result_queue.put(None)


def _fail_download(
    paper_id: int,
    error: str,
    seconds: float,
    result_queue: queue.Queue[_DownloadEvent | None],
) -> None:
    log.warning("paper_download_failed", paper_id=paper_id, error=error, seconds=round(seconds, 2))
    with get_session() as session:
        store.mark_failed(paper_id, error[:2000], session)
    result_queue.put(("failed", paper_id))
