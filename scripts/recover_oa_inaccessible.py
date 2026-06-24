#!/usr/bin/env python3
"""Recover inaccessible Wiley papers that have an OA PDF via Unpaywall.

For each inaccessible paper with a DOI, queries the Unpaywall API.  If a PDF
URL is found, updates oa_pdf_url and resets status to 'discovered' so the
normal filter run will retry it.

Usage:
    uv run scripts/recover_oa_inaccessible.py [--dry-run]
"""

from __future__ import annotations

import sys
import time

import httpx
import structlog
from sqlalchemy import select, update

from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Paper

log = structlog.get_logger(__name__)

_UNPAYWALL_URL = "https://api.unpaywall.org/v2"
_EMAIL = "quinnk@bu.edu"
_DELAY = 0.1  # 10 req/s — well within Unpaywall's limit


def unpaywall_pdf_url(doi: str, client: httpx.Client) -> str | None:
    """Return the best OA PDF URL from Unpaywall, or None if not OA / no PDF."""
    try:
        resp = client.get(
            f"{_UNPAYWALL_URL}/{doi}",
            params={"email": _EMAIL},
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if not data.get("is_oa"):
            return None
        best = data.get("best_oa_location") or {}
        return best.get("url_for_pdf")
    except Exception as exc:
        log.warning("unpaywall_error", doi=doi, error=str(exc))
        return None


def main(dry_run: bool = False) -> None:
    with get_session() as session:
        rows = session.execute(
            select(Paper.id, Paper.doi)
            .where(Paper.status == "inaccessible", Paper.doi.isnot(None))
        ).all()

    log.info("inaccessible_papers_found", count=len(rows))

    recovered = not_oa = no_pdf = errors = 0

    with httpx.Client(follow_redirects=True) as client:
        for i, (paper_id, doi) in enumerate(rows, 1):
            pdf_url = unpaywall_pdf_url(doi, client)

            if pdf_url is None:
                no_pdf += 1
                log.debug("no_oa_pdf", doi=doi)
            else:
                log.info("oa_pdf_found", doi=doi, url=pdf_url)
                if not dry_run:
                    with get_session() as session:
                        session.execute(
                            update(Paper)
                            .where(Paper.id == paper_id)
                            .values(oa_pdf_url=pdf_url, status="discovered")
                        )
                recovered += 1

            if i % 100 == 0:
                log.info("progress", done=i, total=len(rows), recovered=recovered)

            time.sleep(_DELAY)

    log.info(
        "done",
        total=len(rows),
        recovered=recovered,
        no_oa_pdf=no_pdf,
        dry_run=dry_run,
    )


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
