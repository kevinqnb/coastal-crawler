#!/usr/bin/env python3
"""One-off backfill of page_number/page_matched for existing extraction rows.

Migration e4f5a6b7c8d9 added extractions.page_number/page_matched, but
worker.py only populates them for *newly inserted* extractions going
forward (see 2026-08-11-page-number-persistence-01) — existing rows are
NULL. This runs the same site/snippets.py find_snippet() heuristic
site/app.py's detail_view already calls live, once per row, and writes the
result.

OCR text is resolved the same way detail_view does: paper_ocr_context.context
first, falling back to the pre-migration embedded data['context'] copy for
rows that predate PaperOcrContext and were never backfilled into it.
find_snippet() is total — it never raises, even on empty text — so every
matching row gets updated; a row with no OCR text anywhere resolves to
page_number=None/page_matched=False (a correctly-recorded "unknown", not a
skipped row).

Usage:
    uv run scripts/backfill_page_numbers.py [--dry-run]
"""

from __future__ import annotations

import sys

import structlog
from sqlalchemy import select, update

from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Extraction, PaperOcrContext
from coastal_crawler.site.snippets import find_snippet

log = structlog.get_logger(__name__)


def main(dry_run: bool = False) -> None:
    with get_session() as session:
        rows = session.execute(
            select(Extraction.id, Extraction.data, PaperOcrContext.context)
            .outerjoin(PaperOcrContext, PaperOcrContext.paper_id == Extraction.paper_id)
            .where(Extraction.page_number.is_(None))
        ).all()

    log.info("extractions_to_backfill", count=len(rows))

    matched = unmatched = 0

    for i, (extraction_id, data, context) in enumerate(rows, 1):
        data = data or {}
        ocr_text = context or data.get("context", "")
        snippet = find_snippet(ocr_text, data.get("value"), data.get("attribute"), data.get("units"))

        if snippet.matched:
            matched += 1
        else:
            unmatched += 1

        if not dry_run:
            with get_session() as session:
                session.execute(
                    update(Extraction)
                    .where(Extraction.id == extraction_id)
                    .values(page_number=snippet.page_number, page_matched=snippet.matched)
                )

        if i % 500 == 0:
            log.info("progress", done=i, total=len(rows), matched=matched, unmatched=unmatched)

    log.info("done", total=len(rows), matched=matched, unmatched=unmatched, dry_run=dry_run)


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
