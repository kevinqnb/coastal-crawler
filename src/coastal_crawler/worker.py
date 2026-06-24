"""Extraction worker — downloads PDFs, calls the adapter, stores results."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import structlog

from coastal_crawler.adapter import ExtractionAdapter, StubAdapter
from coastal_crawler.config import get_settings
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session

log = structlog.get_logger(__name__)


def run_worker(
    batch_size: int = 10,
    adapter: ExtractionAdapter | None = None,
) -> tuple[int, int]:
    """Claim a batch of discovered papers and run extraction.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple worker processes can
    run concurrently without claiming the same paper.

    The batch-claim transaction is committed immediately so that other workers
    see status='processing' and skip these rows.  Each paper then gets its own
    short transaction: either insert extractions + mark_extracted, or
    mark_failed with the error text.

    Args:
        batch_size: Maximum papers to claim in one run.
        adapter:    Extraction adapter. Defaults to StubAdapter (returns []).

    Returns:
        (extracted, failed) counts for the batch.
    """
    _adapter = adapter if adapter is not None else StubAdapter()

    with get_session() as session:
        papers = store.claim_batch(batch_size, session)
        paper_data = [(p.id, p.oa_pdf_url, p.discovered_from) for p in papers]
    # status='processing' now committed; session closed

    log.info("worker_batch_claimed", count=len(paper_data))

    extracted = 0
    failed = 0
    for paper_id, oa_pdf_url, discovered_from in paper_data:
        if _process_paper(paper_id, oa_pdf_url, discovered_from, _adapter):
            extracted += 1
        else:
            failed += 1

    log.info("worker_batch_done", extracted=extracted, failed=failed)
    return extracted, failed


def requeue_failed() -> int:
    """Reset all papers with status='failed' back to 'discovered'.

    Returns:
        Count of papers requeued.
    """
    with get_session() as session:
        return store.requeue_failed(session)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _process_paper(
    paper_id: int,
    oa_pdf_url: str | None,
    discovered_from: str | None,
    adapter: ExtractionAdapter,
) -> bool:
    """Download, extract, and persist results for one paper.

    Returns True on success, False on any failure (error is recorded in DB).
    """
    with get_session() as session:
        try:
            if not oa_pdf_url:
                raise ValueError("No open-access PDF URL available")

            try:
                pdf_path = _download_pdf(oa_pdf_url, discovered_from)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    log.warning("paper_pdf_inaccessible", paper_id=paper_id, status_code=exc.response.status_code)
                    store.mark_pdf_inaccessible(paper_id, session)
                    return False
                raise

            try:
                results = adapter.extract(pdf_path)
                for result in results:
                    store.insert_extraction(paper_id, result, session)
                store.mark_extracted(paper_id, session)
                log.info("paper_extracted", paper_id=paper_id, measurements=len(results))
            finally:
                pdf_path.unlink(missing_ok=True)

            return True

        except Exception as exc:
            log.warning("paper_failed", paper_id=paper_id, error=str(exc))
            # Roll back any flushed-but-uncommitted extraction rows before
            # recording the failure, so we don't persist partial results.
            session.rollback()
            store.mark_failed(paper_id, str(exc)[:2000], session)
            return False


def _pdf_headers(discovered_from: str | None, url: str) -> dict[str, str]:
    headers: dict[str, str] = {"User-Agent": "coastal-crawler/1.0"}
    if discovered_from == "wiley" or "wiley" in url.lower():
        key = get_settings().wiley_api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
    return headers


def _download_pdf(url: str, discovered_from: str | None = None) -> Path:
    """Download *url* to a temporary file and return its Path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    pdf_path = Path(tmp.name)
    tmp.close()

    resp = httpx.get(url, headers=_pdf_headers(discovered_from, url), timeout=60, follow_redirects=True)
    resp.raise_for_status()
    pdf_path.write_bytes(resp.content)
    return pdf_path
