#!/usr/bin/env python3
"""Pre-download Wiley TDM PDFs for 'relevant' papers, one process at a time.

Wiley's TDM rate limit ("60 requests / 10 minutes") works out to exactly one
request per 10s (see pdf.py's `_throttle_wiley`), and that throttle only
paces requests *within a single process*. Running N `coastal-crawler extract`
jobs in parallel, each downloading its own claimed batch from Wiley, would
push aggregate request rate to N x the limit. This script decouples the two:
it is the *only* process that talks to Wiley, downloading PDFs ahead of time
into a shared directory (WILEY_PDF_DIR / data/wiley_pdfs/ by default, keyed
by `{paper_id}.pdf`); extraction workers then read from that directory
instead of hitting the network (see worker.py's `_download_all` and
EFFICIENCY.md item 1 for the full design rationale).

Idempotent and resumable: files already present on disk are skipped, so this
can be restarted freely and is meant to run continuously (or be re-run
periodically) across a multi-day extraction campaign, staying ahead of
however many GPU jobs are consuming from the directory.

Usage:
    uv run scripts/wiley_download.py                # poll continuously
    uv run scripts/wiley_download.py --once          # one pass, then exit
    uv run scripts/wiley_download.py --poll-interval 60
    uv run scripts/wiley_download.py --dir /path/to/wiley_pdfs
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

import httpx
import structlog

from coastal_crawler.config import get_settings
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session
from coastal_crawler.pdf import download_pdf, is_wiley_request

log = structlog.get_logger(__name__)

_ERROR_BODY_PREVIEW_LEN = 500
_MIN_PDF_BYTES = 1024


def _describe_http_status_error(exc: httpx.HTTPStatusError) -> str:
    """Mirror worker.py's helper: include the response body, not just the status line.

    Wiley's Apigee gateway hides rate-limit diagnostics in the body of a
    bare HTTP 500, which str(exc) alone never surfaces.
    """
    body = (exc.response.text or "").strip()
    if body:
        return f"{exc}: {body[:_ERROR_BODY_PREVIEW_LEN]}"
    return f"{exc} (empty response body)"


def _looks_like_pdf(path: Path) -> bool:
    """Reject non-PDF content (e.g. a 200-with-HTML landing page).

    download_pdf() only checks the HTTP status code — a "successful"
    response can still be an HTML error/login page. Caching that under
    {paper_id}.pdf would poison every future extraction attempt for that
    paper (including requeue-failed retries), since they'd all read the
    same bad file instead of re-downloading. Check it before promoting.
    """
    try:
        if path.stat().st_size < _MIN_PDF_BYTES:
            return False
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _promote(tmp_source: Path, final_path: Path) -> None:
    """Atomically place a downloaded file at final_path.

    Copies into final_path's own directory under a unique staging name
    first, then os.replace()s it into place. That guarantees the rename is
    same-filesystem (and therefore atomic) regardless of where tmp_source
    came from, so an extraction worker scanning for `{id}.pdf` never
    observes a partially-written file.
    """
    staging = final_path.with_name(f"{final_path.name}.{os.getpid()}.tmp")
    shutil.copy(tmp_source, staging)
    os.replace(staging, final_path)


def _mark_failed(paper_id: int, error: str) -> None:
    """Mark a paper failed, but only if it's still 'relevant'.

    A GPU worker can claim this paper into 'processing' while our download
    was in flight (this script doesn't claim rows — it only reads
    status='relevant'). Guarding on status avoids clobbering a paper that's
    actively being extracted via some other path (e.g. it already has a
    cached file from a previous run and extraction is succeeding right now).
    """
    with get_session() as session:
        updated = store.mark_failed_if_relevant(paper_id, error[:2000], session)
    if updated:
        log.warning("wiley_pdf_download_failed", paper_id=paper_id, error=error)
    else:
        log.info(
            "wiley_pdf_download_failed_but_already_claimed",
            paper_id=paper_id,
            error=error,
        )


def _wiley_candidates(
    rows: list[tuple[int, str | None, str | None]],
) -> list[tuple[int, str, str | None]]:
    return [
        (paper_id, url, discovered_from)
        for paper_id, url, discovered_from in rows
        if url and is_wiley_request(discovered_from, url)
    ]


def run_once(wiley_pdf_dir: Path) -> tuple[int, int, int]:
    """One pass over every 'relevant' Wiley paper. Returns (downloaded, cached, failed)."""
    with get_session() as session:
        candidates = _wiley_candidates(store.relevant_papers(session))

    downloaded = cached = failed = 0
    for paper_id, url, discovered_from in candidates:
        final_path = wiley_pdf_dir / f"{paper_id}.pdf"
        if final_path.exists():
            cached += 1
            continue

        t0 = time.monotonic()
        try:
            tmp_path = download_pdf(url, discovered_from)
        except httpx.HTTPStatusError as exc:
            _mark_failed(paper_id, _describe_http_status_error(exc))
            failed += 1
            continue
        except Exception as exc:
            _mark_failed(paper_id, str(exc))
            failed += 1
            continue

        try:
            if not _looks_like_pdf(tmp_path):
                _mark_failed(
                    paper_id,
                    "Downloaded content doesn't look like a PDF (bad magic bytes or too small)",
                )
                failed += 1
                continue
            _promote(tmp_path, final_path)
            downloaded += 1
            log.info(
                "wiley_pdf_downloaded",
                paper_id=paper_id,
                seconds=round(time.monotonic() - t0, 2),
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    return downloaded, cached, failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-download Wiley TDM PDFs for 'relevant' papers."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single pass over currently-relevant papers, then exit.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=30.0,
        help="Seconds to wait between passes (default: 30).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Override WILEY_PDF_DIR from settings/.env.",
    )
    args = parser.parse_args()

    settings = get_settings()
    wiley_pdf_dir = args.dir if args.dir is not None else Path(settings.wiley_pdf_dir)
    wiley_pdf_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "wiley_download_started",
        dir=str(wiley_pdf_dir),
        once=args.once,
        poll_interval=args.poll_interval,
    )

    try:
        while True:
            downloaded, cached, failed = run_once(wiley_pdf_dir)
            log.info(
                "wiley_download_pass_done",
                downloaded=downloaded,
                cached=cached,
                failed=failed,
            )
            if args.once:
                break
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        log.info("wiley_download_stopped")


if __name__ == "__main__":
    main()
