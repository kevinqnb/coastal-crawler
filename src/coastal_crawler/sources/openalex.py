"""OpenAlex discovery source.

API:        https://api.openalex.org/works
Auth:       API key via ?api_key= param (optional; increases rate limits)
            Register at https://openalex.org/register
Rate limit: varies by tier; see https://docs.openalex.org/api-access
Pagination: cursor-based — cursor=* on first page, cursor=<next_cursor> thereafter
Filters:    is_oa:true, topics.id:<T1>|<T2>, from_publication_date:<date>
Config:     OPENALEX_API_KEY (optional), OPENALEX_TOPIC_IDS (optional)
"""

from __future__ import annotations

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

_BASE_URL = "https://api.openalex.org/works"
_PAGE_SIZE = 200
_SELECT = "id,doi,title,abstract_inverted_index,open_access,publication_date"

_DELAY_NO_KEY = 0.15   # seconds between requests without a key (polite pool: 10 req/s)
_DELAY_WITH_KEY = 0.05  # seconds between requests with a key


class OpenAlexSource:
    source_name: str = "openalex"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._delay = _DELAY_WITH_KEY if settings.openalex_api_key else _DELAY_NO_KEY

    def fetch_since(self, watermark: date | None, session: Session) -> int:
        """Cursor-paginate OpenAlex works filtered by concept IDs and date.

        Commits after each page so a restart resumes from the last watermark
        rather than re-fetching all prior pages.

        Returns:
            Total count of newly inserted rows across all pages.
        """
        params: dict[str, Any] = {
            "filter": self._build_filter(watermark),
            "select": _SELECT,
            "per-page": _PAGE_SIZE,
            "cursor": "*",
        }
        if self.settings.openalex_api_key:
            params["api_key"] = self.settings.openalex_api_key

        headers = {"User-Agent": "coastal-crawler/0.1"}

        total_fetched = 0
        total_with_pdf = 0
        total_inserted = 0
        page = 0
        with httpx.Client(timeout=120) as client:
            while True:
                resp = get_with_retry(client, _BASE_URL, params, headers, self._delay)
                body = resp.json()

                results = body.get("results", [])
                page += 1
                total_fetched += len(results)

                if results:
                    records = [r for r in (_map_result(r) for r in results) if r["oa_pdf_url"]]
                    total_with_pdf += len(records)
                    n = store.upsert_papers(records, session)
                    total_inserted += n

                    max_date = _max_pub_date(results)
                    if max_date:
                        store.set_watermark(self.source_name, max_date, session)
                    session.commit()

                next_cursor = body.get("meta", {}).get("next_cursor")
                if not next_cursor:
                    break
                params["cursor"] = next_cursor
                time.sleep(self._delay)

        log.info(
            "openalex_done",
            pages=page,
            fetched=total_fetched,
            with_pdf=total_with_pdf,
            inserted=total_inserted,
            skipped_no_pdf=total_fetched - total_with_pdf,
            skipped_duplicate=total_with_pdf - total_inserted,
        )
        return total_inserted

    def _build_filter(self, watermark: date | None) -> str:
        parts = ["is_oa:true"]
        if self.settings.openalex_topic_ids:
            topics = "|".join(self.settings.openalex_topic_ids)
            parts.append(f"topics.id:{topics}")
        if watermark:
            parts.append(f"from_publication_date:{watermark.isoformat()}")
        return ",".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plain text from OpenAlex's inverted-index abstract format."""
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for word, indices in inverted_index.items():
        for i in indices:
            positions[i] = word
    if not positions:
        return None
    return " ".join(positions[k] for k in sorted(positions))


def _normalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    prefix = "https://doi.org/"
    return raw[len(prefix):] if raw.startswith(prefix) else raw


def _normalize_openalex_id(raw: str | None) -> str | None:
    """'https://openalex.org/W123' → 'W123'."""
    if not raw:
        return None
    return raw.rsplit("/", 1)[-1]


def _map_result(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "doi": _normalize_doi(r.get("doi")),
        "openalex_id": _normalize_openalex_id(r.get("id")),
        "semantic_scholar_id": None,
        "title": r.get("title"),
        "abstract": _reconstruct_abstract(r.get("abstract_inverted_index")),
        "oa_pdf_url": (r.get("open_access") or {}).get("oa_url"),
        "discovered_from": "openalex",
        "metadata": {},
        "status": "discovered",
    }


def _max_pub_date(results: list[dict[str, Any]]) -> date | None:
    dates = []
    for r in results:
        raw = r.get("publication_date")
        if raw:
            try:
                dates.append(date.fromisoformat(raw))
            except ValueError:
                pass
    return max(dates) if dates else None
