"""OCR worker — downloads PDFs, calls the OCR adapter, writes OCR text files.

PDF downloads for the batch run in a background thread (``_download_all``)
while the main thread runs OCR for already-downloaded papers in chunks
(``ocr_chunk_size`` papers per ``adapter.ocr_batch()`` call). This overlaps
two otherwise-serial costs: Wiley's TDM rate limit (10s between requests —
see ``pdf.py``'s ``_throttle_wiley``) is paid by the download thread while
the GPU is busy processing the previous chunk, rather than blocking it.

Each paper's OCR text is written to ``ocr_dir/{paper_id}.txt`` via a
same-directory staging file + ``os.replace()`` *before* the paper is marked
``ocr_done`` and that transaction committed — this ordering is what makes it
safe for the extraction stage (``worker.py``) to poll the DB and read these
files without ever observing a partially-written one.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Literal

import httpx
import structlog

from coastal_crawler.adapter import OCRAdapter, StubOCRAdapter
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session
from coastal_crawler.pdf import download_pdf, is_wiley_request

log = structlog.get_logger(__name__)

_ERROR_BODY_PREVIEW_LEN = 500

# One item per claimed paper, in claim order:
#   ("downloaded", paper_id, pdf_path, is_temp) — is_temp is True for a
#       freshly network-downloaded file (worker owns it, deletes it after
#       use) and False for a file read from the Wiley pre-download cache
#       (shared/persistent — must NOT be deleted).
#   ("failed", paper_id) — already marked ocr_failed in the DB.
#   ("requeued", paper_id) — a Wiley paper whose PDF hasn't been
#       pre-downloaded yet; reset from 'ocr_processing' back to 'relevant'
#       rather than ocr_failed, since this isn't an OCR error.
# `None` signals the downloader thread is done.
_DownloadEvent = (
    tuple[Literal["downloaded"], int, Path, bool]
    | tuple[Literal["failed"], int]
    | tuple[Literal["requeued"], int]
)


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


def _write_ocr_text_atomic(ocr_dir: Path, paper_id: int, text: str) -> None:
    """Write OCR text to ocr_dir/{paper_id}.txt so it's never observed partial.

    Writes to a staging file in the same directory (guaranteeing a
    same-filesystem, atomic os.replace()), then renames it into place.
    """
    final_path = ocr_dir / f"{paper_id}.txt"
    staging = ocr_dir / f"{paper_id}.txt.{os.getpid()}.tmp"
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, final_path)


def run_ocr_worker(
    batch_size: int = 10,
    adapter: OCRAdapter | None = None,
    chunk_size: int = 20,
    wiley_pdf_dir: Path | None = None,
    ocr_dir: Path = Path("data/ocr"),
) -> tuple[int, int, int]:
    """Claim a batch of relevant papers and OCR them to text files.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple OCR worker processes can
    run concurrently without claiming the same paper.

    The batch-claim transaction is committed immediately so that other workers
    see status='ocr_processing' and skip these rows. Downloads then run in a
    background thread, feeding a queue that the main thread drains into
    chunks of ``chunk_size`` papers; each chunk is OCR'd with a single
    ``adapter.ocr_batch()`` call so multiple documents can run concurrently
    against vLLM's continuous batching, rather than one document at a time.
    Each paper still gets its own short DB transaction: either write its OCR
    text file + mark_ocr_done, or mark_ocr_failed with the error text.

    Args:
        batch_size: Maximum papers to claim in one run.
        adapter:    OCR adapter. Defaults to StubOCRAdapter (returns "").
        chunk_size: Papers per ocr_batch() call. Callers reading from
            Settings should pass settings.ocr_chunk_size explicitly.
        wiley_pdf_dir: If set, Wiley papers' PDFs are read from
            ``wiley_pdf_dir/{paper_id}.pdf`` (written ahead of time by
            scripts/wiley_download.py) instead of being downloaded live —
            this is what makes it safe to run multiple OCR jobs in parallel
            (see EFFICIENCY.md item 1). A claimed Wiley paper whose file
            isn't there yet is reset to 'relevant' (counted in ``requeued``,
            not ``failed``) rather than treated as an OCR failure. If None
            (the default), Wiley papers are downloaded live exactly as
            before.
        ocr_dir: Directory to write OCR text files into (named
            ``{paper_id}.txt``). Created if missing.

    Returns:
        (ocr_done, failed, requeued) counts for the batch.
    """
    _adapter = adapter if adapter is not None else StubOCRAdapter()
    ocr_dir.mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        papers = store.claim_batch_for_ocr(batch_size, session)
        paper_data = [(p.id, p.oa_pdf_url, p.discovered_from) for p in papers]
    # status='ocr_processing' now committed; session closed

    log.info("ocr_worker_batch_claimed", count=len(paper_data))

    if not paper_data:
        return 0, 0, 0

    t_batch0 = time.monotonic()
    result_queue: queue.Queue[_DownloadEvent | None] = queue.Queue()
    downloader = threading.Thread(
        target=_download_all, args=(paper_data, result_queue, wiley_pdf_dir), daemon=True
    )
    downloader.start()

    ocr_done = 0
    failed = 0
    requeued = 0
    chunk: list[tuple[int, Path, bool]] = []

    def _flush_chunk() -> None:
        nonlocal ocr_done, failed
        if not chunk:
            return

        paper_ids = [pid for pid, _, _ in chunk]
        pdf_paths = [path for _, path, _ in chunk]
        is_temp_flags = [is_temp for _, _, is_temp in chunk]

        log.info("gpu_chunk_started", chunk_size=len(chunk))
        t0 = time.monotonic()
        batch_error: Exception | None = None
        try:
            texts = _adapter.ocr_batch(pdf_paths)
        except Exception as exc:
            texts = ["" for _ in chunk]
            batch_error = exc
        log.info("gpu_chunk_done", chunk_size=len(chunk), seconds=round(time.monotonic() - t0, 2))

        for paper_id, pdf_path, is_temp, text in zip(paper_ids, pdf_paths, is_temp_flags, texts):
            with get_session() as session:
                try:
                    if batch_error is not None:
                        raise batch_error
                    if not text.strip():
                        raise RuntimeError("OCR produced empty output")
                    _write_ocr_text_atomic(ocr_dir, paper_id, text)
                    store.mark_ocr_done(paper_id, session)
                    ocr_done += 1
                    log.info("paper_ocred", paper_id=paper_id, chars=len(text))
                except Exception as exc:
                    session.rollback()
                    store.mark_ocr_failed(paper_id, str(exc)[:2000], session)
                    failed += 1
                    log.warning("paper_ocr_failed", paper_id=paper_id, error=str(exc))
            # Only the worker's own temp downloads get cleaned up here — a
            # file read from the Wiley pre-download cache is shared/
            # persistent and must survive for reuse by future runs.
            if is_temp:
                pdf_path.unlink(missing_ok=True)

        chunk.clear()

    while True:
        item = result_queue.get()
        if item is None:
            break
        if item[0] == "downloaded":
            _, paper_id, pdf_path, is_temp = item
            chunk.append((paper_id, pdf_path, is_temp))
            if len(chunk) >= chunk_size:
                _flush_chunk()
        elif item[0] == "failed":
            failed += 1
        else:  # "requeued"
            requeued += 1
    _flush_chunk()
    downloader.join()

    seconds = round(time.monotonic() - t_batch0, 2)
    total = ocr_done + failed
    papers_per_hour = round(total / seconds * 3600, 1) if seconds > 0 else None
    log.info(
        "ocr_worker_batch_done",
        ocr_done=ocr_done,
        failed=failed,
        requeued=requeued,
        seconds=seconds,
        papers_per_hour=papers_per_hour,
    )
    return ocr_done, failed, requeued


def requeue_ocr_processing() -> int:
    """Reset all papers with status='ocr_processing' back to 'relevant'.

    Rescues papers stranded mid-batch by an OCR job that was killed
    (walltime limit, OOM, node preemption) before it could mark them
    ocr_done or ocr_failed.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_ocr_processing(session)


def requeue_ocr_failed() -> int:
    """Reset all papers with status='ocr_failed' back to 'relevant'.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_ocr_failed(session)


def requeue_ocr() -> int:
    """Reset every paper touched by OCR or extraction back to 'relevant'.

    Use this to force a full re-OCR (e.g. after changing DOC_LM_MODEL).

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_ocr(session)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _download_all(
    paper_data: list[tuple[int, str | None, str | None]],
    result_queue: queue.Queue[_DownloadEvent | None],
    wiley_pdf_dir: Path | None = None,
) -> None:
    """Resolve every paper's PDF in order, pushing results to a queue.

    Runs in a background thread so downloads for later papers (including
    Wiley's ~10s/request throttle — see ``pdf.py``) overlap with GPU
    processing of earlier chunks in the main thread. Download failures are
    marked ocr_failed in the DB immediately, from this thread, using their
    own session (SQLAlchemy sessions are safe to open per-call against a
    shared thread-safe Engine).

    If ``wiley_pdf_dir`` is set, Wiley papers never hit the network here —
    their PDF is looked up at ``wiley_pdf_dir/{paper_id}.pdf``, written
    ahead of time by scripts/wiley_download.py. A paper whose file isn't
    there yet is reset to 'relevant' and reported as "requeued", not
    "failed": the pre-downloader hasn't caught up, not a real error.
    """
    for paper_id, oa_pdf_url, discovered_from in paper_data:
        t0 = time.monotonic()
        try:
            if not oa_pdf_url:
                raise ValueError("No open-access PDF URL available")
            if wiley_pdf_dir is not None and is_wiley_request(discovered_from, oa_pdf_url):
                cached_path = wiley_pdf_dir / f"{paper_id}.pdf"
                if not cached_path.exists():
                    _requeue_undownloaded(paper_id, result_queue)
                    continue
                log.info("paper_pdf_found_local", paper_id=paper_id, path=str(cached_path))
                result_queue.put(("downloaded", paper_id, cached_path, False))
                continue
            pdf_path = download_pdf(oa_pdf_url, discovered_from)
            log.info("paper_downloaded", paper_id=paper_id, seconds=round(time.monotonic() - t0, 2))
            result_queue.put(("downloaded", paper_id, pdf_path, True))
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
        store.mark_ocr_failed(paper_id, error[:2000], session)
    result_queue.put(("failed", paper_id))


def _requeue_undownloaded(
    paper_id: int,
    result_queue: queue.Queue[_DownloadEvent | None],
) -> None:
    log.info("paper_wiley_pdf_not_ready", paper_id=paper_id)
    with get_session() as session:
        store.reset_ocr_processing_to_relevant(paper_id, session)
    result_queue.put(("requeued", paper_id))
