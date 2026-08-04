#!/usr/bin/env python3
"""One-off backfill of authors/publication_date for existing papers.

Migration f6a7b8c9d0e1 added papers.authors/publication_date, but source
mappers only populate them for *newly discovered* papers going forward —
existing rows are NULL. This queries the public Crossref API (no key
required) by DOI to fill in the gap for papers already in the DB.

Usage:
    uv run scripts/backfill_authors.py [--dry-run]
"""

from __future__ import annotations

import sys
import time
from datetime import date

import httpx
import structlog
from sqlalchemy import select, update

from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Paper

log = structlog.get_logger(__name__)

_CROSSREF_URL = "https://api.crossref.org/works"
_EMAIL = "quinnk@bu.edu"
_DELAY = 0.5  # ~2 req/s, within Crossref's polite pool


def _extract_authors(authors: list[dict] | None) -> list[str] | None:
    if not authors:
        return None
    names = []
    for a in authors:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p)
        if name:
            names.append(name)
    return names or None


def _extract_pub_date(message: dict) -> str | None:
    parts = (message.get("published") or {}).get("date-parts", [[]])[0]
    if not parts:
        return None
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    try:
        return date(year, month, day).isoformat()
    except (ValueError, TypeError):
        return None


def fetch_metadata(doi: str, client: httpx.Client) -> tuple[list[str] | None, str | None] | None:
    """Return (authors, publication_date_iso) for *doi*, or None on lookup failure."""
    try:
        resp = client.get(f"{_CROSSREF_URL}/{doi}", params={"mailto": _EMAIL}, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        message = resp.json().get("message", {})
    except Exception as exc:
        log.debug("crossref_error", doi=doi, error=str(exc))
        return None
    return _extract_authors(message.get("author")), _extract_pub_date(message)


def main(dry_run: bool = False) -> None:
    with get_session() as session:
        rows = session.execute(
            select(Paper.id, Paper.doi).where(
                Paper.doi.isnot(None), Paper.authors.is_(None)
            )
        ).all()

    log.info("papers_to_backfill", count=len(rows))

    updated = skipped = 0

    with httpx.Client() as client:
        for i, (paper_id, doi) in enumerate(rows, 1):
            result = fetch_metadata(doi, client)

            if result is None or (result[0] is None and result[1] is None):
                skipped += 1
            else:
                authors, pub_date = result
                if not dry_run:
                    with get_session() as session:
                        session.execute(
                            update(Paper)
                            .where(Paper.id == paper_id)
                            .values(authors=authors, publication_date=pub_date)
                        )
                updated += 1

            if i % 50 == 0:
                log.info("progress", done=i, total=len(rows), updated=updated)

            time.sleep(_DELAY)

    log.info("done", total=len(rows), updated=updated, skipped=skipped, dry_run=dry_run)


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
