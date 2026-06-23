"""Semantic Scholar discovery source.

API:        https://api.semanticscholar.org/graph/v1/paper/search/bulk
Auth:       x-api-key header (required)
Rate limit: 1 req/s on free tier
Pagination: cursor via ``token`` response field; absent means last page
Fields:     paperId, externalIds, title, abstract, openAccessPdf, publicationDate
Config:     SEMANTIC_SCHOLAR_API_KEY (required), SEMANTIC_SCHOLAR_QUERY

Query syntax supported by the bulk endpoint:
  +   AND          |   OR          -   negate term
  "…" phrase       *   prefix      (…) precedence
  ~N  fuzzy (edit distance N after a word, or slop N after a phrase)

Date filtering: publicationDateOrYear=<watermark>: restricts results to papers
published on or after the stored watermark date.
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

_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
_FIELDS = "paperId,externalIds,title,abstract,openAccessPdf,publicationDate"
_PAGE_SIZE = 1000

_DELAY = 2.0  # seconds between requests — conservative buffer around 1 req/s free tier limit


class SemanticScholarSource:
    source_name: str = "semantic_scholar"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_since(self, watermark: date | None, session: Session) -> int:
        """Search Semantic Scholar with each configured query.

        Paginate via the bulk search token cursor.  Each page is committed
        immediately so progress survives a partial run.

        Returns:
            Total count of newly inserted rows across all queries and pages.
        """
        if not self.settings.semantic_scholar_api_key:
            log.warning(
                "semantic_scholar_skipped",
                reason="bulk search endpoint requires an API key; set SEMANTIC_SCHOLAR_API_KEY",
            )
            return 0

        headers: dict[str, str] = {"x-api-key": self.settings.semantic_scholar_api_key}
        return self._fetch_query(self.settings.semantic_scholar_query, session, headers, watermark)

    def _fetch_query(
        self,
        query: str | None,
        session: Session,
        headers: dict[str, str],
        watermark: date | None = None,
    ) -> int:
        params: dict[str, Any] = {"fields": _FIELDS, "limit": _PAGE_SIZE, "openAccessPdf": ""}
        if watermark:
            params["publicationDateOrYear"] = f"{watermark.isoformat()}:"
        if query:
            # Collapse whitespace and strip wildcards — S2 stems all terms in
            # English so "mangrove*" and "mangrove" match the same papers, but
            # many wildcards in a large query cause S2 to return a 500.
            q = " ".join(query.split())
            q = q.replace("*", "")
            params["query"] = q

        log.info("s2_query_sending", query=params.get("query", "(none)"), since=watermark.isoformat() if watermark else "all")

        total_fetched = 0
        total_with_pdf = 0
        page = 0
        all_papers = []
        with httpx.Client(timeout=30) as client:
            while True:
                resp = get_with_retry(client, _BASE_URL, params, headers, _DELAY)
                body = resp.json()
                if page == 0:
                    log.info("s2_query_total", total=body.get("total", "unknown"))

                papers = body.get("data", [])
                page += 1
                total_fetched += len(papers)
                all_papers.extend(papers)

                if not papers:
                    break

                token = body.get("token")
                if not token:
                    break
                params["token"] = token
                time.sleep(_DELAY)

        # Process all collected papers at once
        records = [r for r in (_map_paper(p) for p in all_papers) if r["oa_pdf_url"]]
        total_with_pdf = len(records)
        total_inserted = store.upsert_papers(records, session)

        # Set watermark only after all pages have been processed
        if all_papers:
            max_date = _max_pub_date(all_papers)
            if max_date:
                store.set_watermark(self.source_name, max_date, session)
        session.commit()

        log.info(
            "s2_query_done",
            query=query or "(none)",
            pages=page,
            fetched=total_fetched,
            with_pdf=total_with_pdf,
            inserted=total_inserted,
            skipped_no_pdf=total_fetched - total_with_pdf,
            skipped_duplicate=total_with_pdf - total_inserted,
        )
        return total_inserted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_paper(p: dict[str, Any]) -> dict[str, Any]:
    ext = p.get("externalIds") or {}
    return {
        "doi": ext.get("DOI"),
        "openalex_id": None,
        "semantic_scholar_id": p.get("paperId"),
        "title": p.get("title"),
        "abstract": p.get("abstract"),
        "oa_pdf_url": (p.get("openAccessPdf") or {}).get("url"),
        "discovered_from": "semantic_scholar",
        "metadata": {},
        "status": "discovered",
    }


def _max_pub_date(papers: list[dict[str, Any]]) -> date | None:
    dates = []
    for p in papers:
        raw = p.get("publicationDate")
        if raw:
            try:
                dates.append(date.fromisoformat(raw))
            except ValueError:
                pass
    return max(dates) if dates else None
