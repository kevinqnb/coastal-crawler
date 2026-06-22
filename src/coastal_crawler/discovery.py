"""Multi-source paper discovery orchestrator."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import structlog

from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session

if TYPE_CHECKING:
    from coastal_crawler.sources.base import DiscoverySource

log = structlog.get_logger(__name__)


def discover(since: date | None = None) -> int:
    """Query all enabled discovery sources for new coastal-ecosystem papers.

    Each source reads and writes its own row in ``crawl_state`` so they
    advance independently; a missed run self-heals by covering the wider
    gap on the next run.

    Args:
        since: Override the watermark for all sources (useful for back-fills).
               Defaults to each source's own stored watermark.

    Returns:
        Total count of newly inserted rows across all sources.
    """
    from coastal_crawler.config import get_settings
    from coastal_crawler.sources.openalex import OpenAlexSource
    from coastal_crawler.sources.semantic_scholar import SemanticScholarSource
    from coastal_crawler.sources.wiley import WileySource

    settings = get_settings()
    registry = {
        "openalex": OpenAlexSource,
        "semantic_scholar": SemanticScholarSource,
        "wiley": WileySource,
    }

    total = 0
    for source_name in settings.enabled_sources:
        cls = registry.get(source_name)
        if cls is None:
            log.warning("unknown_source", source=source_name)
            continue

        try:
            source: DiscoverySource = cls(settings)
        except ValueError as exc:
            log.warning("source_init_failed", source=source_name, error=str(exc))
            continue

        with get_session() as session:
            watermark = since if since is not None else store.get_watermark(source_name, session)
            log.info("discovery_start", source=source_name, watermark=watermark)
            try:
                n = source.fetch_since(watermark, session)
            except Exception as exc:
                log.error("discovery_failed", source=source_name, error=str(exc))
                raise
            total += n
            log.info("discovery_done", source=source_name, inserted=n)

    return total
