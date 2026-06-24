"""Wiley discovery source via CrossRef API.

Discovery:  CrossRef REST API (https://api.crossref.org/works) filtered by
            ISSN and publication date.  No API key required; polite-pool
            access via User-Agent header.
Extraction: Wiley TDM API (https://api.wiley.com/onlinelibrary/tdm/v2/articles/{doi})
            requires WILEY_API_KEY at download time.

Pagination: cursor-based (cursor=* on first page, then next-cursor from response)
Config:     WILEY_ISSNS (required), WILEY_API_KEY (required for extraction only)
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import httpx
import structlog
from sqlalchemy.orm import Session

from coastal_crawler.config import Settings
from coastal_crawler.db import store
from coastal_crawler.sources.http import get_with_retry

log = structlog.get_logger(__name__)

_CROSSREF_URL = "https://api.crossref.org/works"
_WILEY_TDM_URL = "https://api.wiley.com/onlinelibrary/tdm/v1/articles"
_PAGE_SIZE = 200
_SELECT = "DOI,title,abstract,published,link"
_DELAY = 0.5  # seconds between requests (~2 req/s, well within CrossRef polite pool)


class WileySource:
    source_name: str = "wiley"

    def __init__(self, settings: Settings) -> None:
        if not settings.wiley_issns:
            raise ValueError(
                "WILEY_ISSNS must contain at least one ISSN to enable the Wiley source."
            )
        if not settings.wiley_api_key:
            log.warning(
                "wiley_no_tdm_key",
                reason="WILEY_API_KEY not set — Wiley papers will be discovered but extraction will fail",
            )
        self.settings = settings

    def fetch_since(self, watermark: date | None, session: Session) -> int:
        """Cursor-paginate CrossRef works filtered by ISSN and date.

        Commits after each page so a restart resumes from the last watermark.

        Returns:
            Total count of newly inserted rows across all pages.
        """
        params: dict[str, Any] = {
            "filter": self._build_filter(watermark),
            "select": _SELECT,
            "rows": _PAGE_SIZE,
            "cursor": "*",
        }
        headers = {"User-Agent": "coastal-crawler/0.1"}

        total_fetched = 0
        total_with_pdf = 0
        total_inserted = 0
        page = 0
        with httpx.Client(timeout=30) as client:
            while True:
                resp = get_with_retry(client, _CROSSREF_URL, params, headers, _DELAY)
                body = resp.json().get("message", {})

                items = body.get("items", [])
                page += 1
                total_fetched += len(items)

                if items:
                    records = [r for r in (_map_item(i) for i in items) if r["oa_pdf_url"]]
                    total_with_pdf += len(records)
                    n = store.upsert_papers(records, session)
                    total_inserted += n

                    max_date = _max_pub_date(items)
                    if max_date:
                        store.set_watermark(self.source_name, max_date, session)
                    session.commit()

                next_cursor = body.get("next-cursor")
                if not next_cursor or len(items) < _PAGE_SIZE:
                    break
                params["cursor"] = next_cursor
                time.sleep(_DELAY)

        log.info(
            "wiley_done",
            pages=page,
            fetched=total_fetched,
            with_pdf=total_with_pdf,
            inserted=total_inserted,
            skipped_no_pdf=total_fetched - total_with_pdf,
            skipped_duplicate=total_with_pdf - total_inserted,
        )
        return total_inserted

    def _build_filter(self, watermark: date | None) -> str:
        parts = ["type:journal-article", "has-full-text:true"]
        for issn in self.settings.wiley_issns:
            parts.append(f"issn:{_normalize_issn(issn)}")
        if watermark:
            parts.append(f"from-pub-date:{watermark.isoformat()}")
        return ",".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_issn(issn: str) -> str:
    """Ensure ISSN is in XXXX-XXXX format; insert hyphen if the user omitted it."""
    cleaned = issn.strip().replace("-", "")
    return f"{cleaned[:4]}-{cleaned[4:]}" if len(cleaned) == 8 else issn.strip()


def _map_item(item: dict[str, Any]) -> dict[str, Any]:
    doi = item.get("DOI") or None
    titles = item.get("title") or []
    return {
        "doi": doi,
        "openalex_id": None,
        "semantic_scholar_id": None,
        "title": titles[0] if titles else None,
        "abstract": _strip_jats(item.get("abstract")),
        "oa_pdf_url": _extract_tdm_url(item.get("link") or [], doi),
        "discovered_from": "wiley",
        "metadata": {},
        "status": "discovered",
    }


def _extract_tdm_url(links: list[dict[str, Any]], doi: str | None) -> str | None:
    """Return the text-mining link from CrossRef, falling back to constructing the TDM URL."""
    from coastal_crawler.pdf import normalize_pdf_url
    for link in links:
        if link.get("intended-application") == "text-mining":
            url = link.get("URL")
            return normalize_pdf_url(url) if url else None
    if doi:
        return f"{_WILEY_TDM_URL}/{doi}"
    return None


def _strip_jats(abstract: str | None) -> str | None:
    """Strip JATS XML tags from CrossRef abstracts (e.g. <jats:p>, <jats:abstract>)."""
    if not abstract:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", abstract)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _max_pub_date(items: list[dict[str, Any]]) -> date | None:
    dates = [d for i in items if (d := _pub_date(i))]
    return max(dates) if dates else None


def _pub_date(item: dict[str, Any]) -> date | None:
    parts = (item.get("published") or {}).get("date-parts", [[]])[0]
    try:
        return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except (IndexError, ValueError, TypeError):
        return None
