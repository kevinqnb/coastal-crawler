"""Table-clean worker — re-renders each OCR'd paper's PDF pages, calls the
table-clean adapter to normalize <table> markup against those page images,
writes cleaned text files.

Sits between ``coastal-crawler ocr`` and ``coastal-crawler extract``: claims
``ocr_done`` papers, writes ``table_clean_dir/{paper_id}.txt``, and marks them
``table_clean_done``/``table_clean_failed``. Extraction (``worker.py``) then
claims from ``table_clean_done`` and reads from ``table_clean_dir`` instead of
``ocr_dir`` — it never sees raw OCR text.

PDF access mirrors ``ocr_worker.py``'s pattern exactly (Wiley-cache-aware
download in a background thread, live download for everything else) — this
means non-Wiley PDFs already downloaded once at OCR time are downloaded again
here; an accepted, explicit cost of giving table-clean its own PDF access
rather than threading rendered images through from the OCR stage.

scholarlm's ``MeasurementLM._clean_tables()`` (called via
``adapter.DirectTableCleanAdapter``) raises ``FileNotFoundError`` for the
*entire* call if any one document's pre-rendered image directory is missing —
not just that document. So every paper's page images are rendered and
validated *before* the adapter is called; any paper that fails rendering or
whose rendered page count doesn't cover every page number referenced in its
OCR text is excluded from the call and marked ``table_clean_failed``
immediately, rather than risking the whole chunk.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Literal

import httpx
import structlog
from scholarlm.utils.pdf import process_pdf

from coastal_crawler.adapter import StubTableCleanAdapter, TableCleanAdapter
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session
from coastal_crawler.pdf import download_pdf, is_wiley_request

log = structlog.get_logger(__name__)

_ERROR_BODY_PREVIEW_LEN = 500

# Matches DocumentLM.fit()'s <page number="N"> tagging exactly — see
# scholarlm.measurementlm.MeasurementLM._get_page_numbers, which this
# duplicates rather than reaching into (a private method on a MeasurementLM
# instance the worker doesn't otherwise need).
_PAGE_NUMBER_RE = re.compile(r'<page number="(\d+)">')
_TABLE_TAG_RE = re.compile(r'<table number="\d+">')

# See ocr_worker.py's _DownloadEvent for the same pattern.
_DownloadEvent = (
    tuple[Literal["downloaded"], int, Path, bool]
    | tuple[Literal["failed"], int]
    | tuple[Literal["requeued"], int]
)


def _describe_http_status_error(exc: httpx.HTTPStatusError) -> str:
    """Mirrors ocr_worker.py's _describe_http_status_error exactly."""
    body = (exc.response.text or "").strip()
    if body:
        return f"{exc}: {body[:_ERROR_BODY_PREVIEW_LEN]}"
    return f"{exc} (empty response body)"


def _write_table_clean_text_atomic(table_clean_dir: Path, paper_id: int, text: str) -> None:
    """Write cleaned text to table_clean_dir/{paper_id}.txt so it's never observed partial.

    Mirrors ocr_worker.py's _write_ocr_text_atomic exactly: staging file in
    the same directory (guaranteeing a same-filesystem, atomic os.replace()),
    then renamed into place.
    """
    final_path = table_clean_dir / f"{paper_id}.txt"
    staging = table_clean_dir / f"{paper_id}.txt.{os.getpid()}.tmp"
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, final_path)


def _max_page_number(ocr_text: str) -> int | None:
    """Highest page number referenced in <page number="N"> tags, or None if none found."""
    numbers = [int(m) for m in _PAGE_NUMBER_RE.findall(ocr_text)]
    return max(numbers) if numbers else None


def run_table_clean_worker(
    batch_size: int = 10,
    adapter: TableCleanAdapter | None = None,
    chunk_size: int = 20,
    wiley_pdf_dir: Path | None = None,
    ocr_dir: Path = Path("data/ocr"),
    table_clean_dir: Path = Path("data/table_clean"),
    scratch_dir: Path = Path("data/table_clean_scratch"),
    page_render_dim: int = 2048,
) -> tuple[int, int, int]:
    """Claim a batch of ocr_done papers and normalize their table markup.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple table-clean worker
    processes can run concurrently without claiming the same paper.

    The batch-claim transaction commits immediately so other workers see
    status='table_clean_processing' and skip these rows. PDF downloads run
    in a background thread (same Wiley-cache-aware pattern as
    ocr_worker.py), feeding a queue the main thread drains into chunks of
    ``chunk_size`` papers. For each chunk, every paper's pages are rendered
    and validated before a single ``adapter.clean_tables_batch()`` call —
    any paper that fails rendering/validation is excluded from that call and
    marked failed immediately, so one bad paper can't take down its whole
    chunk (see module docstring).

    Args:
        batch_size: Maximum papers to claim in one run.
        adapter: Table-clean adapter. Defaults to StubTableCleanAdapter
            (returns documents unchanged).
        chunk_size: Papers per clean_tables_batch() call.
        wiley_pdf_dir: If set, Wiley papers' PDFs are read from
            ``wiley_pdf_dir/{paper_id}.pdf`` instead of downloaded live —
            same as ocr_worker.py. A claimed Wiley paper whose file isn't
            there yet is reset to 'ocr_done' (counted in ``requeued``, not
            ``failed``).
        ocr_dir: Directory to read OCR text files from (named
            ``{paper_id}.txt``, written by ocr_worker.py).
        table_clean_dir: Directory to write cleaned text files into (named
            ``{paper_id}.txt``). Created if missing.
        scratch_dir: Per-paper scratch directory for pre-rendered page .b64
            files. Created if missing; each paper's subdirectory is deleted
            after that paper is processed (success or failure).
        page_render_dim: target_longest_dim passed to
            scholarlm.utils.pdf.process_pdf() for page renders.

    Returns:
        (table_clean_done, failed, requeued) counts for the batch.
    """
    _adapter = adapter if adapter is not None else StubTableCleanAdapter()
    table_clean_dir.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        papers = store.claim_batch_for_table_clean(batch_size, session)
        paper_data = [(p.id, p.oa_pdf_url, p.discovered_from) for p in papers]
    # status='table_clean_processing' now committed; session closed

    log.info("table_clean_worker_batch_claimed", count=len(paper_data))

    if not paper_data:
        return 0, 0, 0

    t_batch0 = time.monotonic()
    result_queue: queue.Queue[_DownloadEvent | None] = queue.Queue()
    downloader = threading.Thread(
        target=_download_all, args=(paper_data, result_queue, wiley_pdf_dir), daemon=True
    )
    downloader.start()

    table_clean_done = 0
    failed = 0
    requeued = 0
    chunk: list[tuple[int, Path, bool]] = []

    def _flush_chunk() -> None:
        nonlocal table_clean_done, failed
        if not chunk:
            return

        # Prepare every paper's OCR text + rendered page images first,
        # excluding (and failing) any paper that can't be prepared, so a
        # single bad paper never causes _clean_tables to abort the whole
        # call (see module docstring).
        ready_paper_ids: list[int] = []
        ready_documents: list[str] = []
        ready_scratch_dirs: list[str] = []

        for paper_id, pdf_path, is_temp in chunk:
            paper_scratch_dir = scratch_dir / str(paper_id)
            try:
                ocr_text = (ocr_dir / f"{paper_id}.txt").read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("table_clean_ocr_text_missing", paper_id=paper_id, error=str(exc))
                with get_session() as session:
                    store.mark_table_clean_failed(paper_id, f"OCR text missing: {exc}"[:2000], session)
                failed += 1
                continue

            try:
                images = process_pdf(str(pdf_path), target_longest_dim=page_render_dim)
            except Exception as exc:
                log.warning("table_clean_render_failed", paper_id=paper_id, error=str(exc))
                with get_session() as session:
                    store.mark_table_clean_failed(paper_id, f"Page render failed: {exc}"[:2000], session)
                failed += 1
                continue

            max_page = _max_page_number(ocr_text)
            if max_page is not None and len(images) <= max_page:
                error = (
                    f"Page count mismatch: OCR text references page {max_page} but only "
                    f"{len(images)} page(s) rendered from the current PDF — the PDF likely "
                    f"changed since OCR."
                )
                log.warning("table_clean_page_count_mismatch", paper_id=paper_id, error=error)
                with get_session() as session:
                    store.mark_table_clean_failed(paper_id, error, session)
                failed += 1
                continue

            paper_scratch_dir.mkdir(parents=True, exist_ok=True)
            for i, image_b64 in enumerate(images):
                (paper_scratch_dir / f"{i}.b64").write_text(image_b64, encoding="utf-8")

            ready_paper_ids.append(paper_id)
            ready_documents.append(ocr_text)
            ready_scratch_dirs.append(str(paper_scratch_dir))

        if ready_paper_ids:
            log.info("gpu_chunk_started", chunk_size=len(ready_paper_ids))
            t0 = time.monotonic()
            batch_error: Exception | None = None
            try:
                cleaned_texts = _adapter.clean_tables_batch(ready_documents, ready_scratch_dirs)
            except Exception as exc:
                cleaned_texts = list(ready_documents)
                batch_error = exc
            log.info(
                "gpu_chunk_done", chunk_size=len(ready_paper_ids), seconds=round(time.monotonic() - t0, 2)
            )

            context_length_exceeded = _adapter.context_length_exceeded_docs

            # Fail-loud guard: if every table-bearing paper in this chunk
            # came back byte-identical to its input, that's the signature of
            # a broken endpoint (wrong modality, wrong table-tag regex, a
            # swallowed server error) — not coincidence. Raise for the whole
            # surviving subset rather than silently marking them all done.
            if batch_error is None:
                table_bearing = [
                    i
                    for i, doc in enumerate(ready_documents)
                    if _TABLE_TAG_RE.search(doc) and i not in context_length_exceeded
                ]
                if table_bearing and all(
                    cleaned_texts[i].strip() == ready_documents[i].strip() for i in table_bearing
                ):
                    batch_error = RuntimeError(
                        f"Every table-bearing paper in this chunk ({len(table_bearing)}) came back "
                        f"unchanged from table-clean — this usually means the model/endpoint isn't "
                        f"actually cleaning tables (wrong modality, mismatched table-tag format, or "
                        f"a swallowed server error), not that the tables were already clean."
                    )

            for i, paper_id in enumerate(ready_paper_ids):
                with get_session() as session:
                    try:
                        if batch_error is not None:
                            raise batch_error
                        if i in context_length_exceeded:
                            raise RuntimeError("Table-clean context length exceeded for this paper")
                        cleaned_text = cleaned_texts[i]
                        if not cleaned_text.strip():
                            raise RuntimeError("Table-clean produced empty output")
                        if _TABLE_TAG_RE.search(ready_documents[i]) and cleaned_text.strip() == ready_documents[i].strip():
                            log.warning("table_clean_no_change_detected", paper_id=paper_id)
                        _write_table_clean_text_atomic(table_clean_dir, paper_id, cleaned_text)
                        store.mark_table_clean_done(paper_id, session)
                        table_clean_done += 1
                        log.info("paper_table_cleaned", paper_id=paper_id, chars=len(cleaned_text))
                    except Exception as exc:
                        session.rollback()
                        store.mark_table_clean_failed(paper_id, str(exc)[:2000], session)
                        failed += 1
                        log.warning("paper_table_clean_failed", paper_id=paper_id, error=str(exc))

        for paper_id, pdf_path, is_temp in chunk:
            shutil.rmtree(scratch_dir / str(paper_id), ignore_errors=True)
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
    total = table_clean_done + failed
    papers_per_hour = round(total / seconds * 3600, 1) if seconds > 0 else None
    log.info(
        "table_clean_worker_batch_done",
        table_clean_done=table_clean_done,
        failed=failed,
        requeued=requeued,
        seconds=seconds,
        papers_per_hour=papers_per_hour,
    )
    return table_clean_done, failed, requeued


def requeue_table_clean_processing() -> int:
    """Reset all papers with status='table_clean_processing' back to 'ocr_done'.

    Rescues papers stranded mid-batch by a table-clean job that was killed
    (walltime limit, OOM, node preemption) before it could mark them
    table_clean_done or table_clean_failed.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_table_clean_processing(session)


def requeue_table_clean_failed() -> int:
    """Reset all papers with status='table_clean_failed' back to 'ocr_done'.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_table_clean_failed(session)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _download_all(
    paper_data: list[tuple[int, str | None, str | None]],
    result_queue: queue.Queue[_DownloadEvent | None],
    wiley_pdf_dir: Path | None = None,
) -> None:
    """Resolve every paper's PDF in order, pushing results to a queue.

    Exact mirror of ocr_worker.py's _download_all, targeting
    table_clean_failed / reset_table_clean_processing_to_ocr_done instead of
    the OCR-stage equivalents.
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
        store.mark_table_clean_failed(paper_id, error[:2000], session)
    result_queue.put(("failed", paper_id))


def _requeue_undownloaded(
    paper_id: int,
    result_queue: queue.Queue[_DownloadEvent | None],
) -> None:
    log.info("paper_wiley_pdf_not_ready", paper_id=paper_id)
    with get_session() as session:
        store.reset_table_clean_processing_to_ocr_done(paper_id, session)
    result_queue.put(("requeued", paper_id))
