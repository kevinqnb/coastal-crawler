"""DiscoverySource protocol — the interface each source must satisfy."""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session


@runtime_checkable
class DiscoverySource(Protocol):
    """A single paper discovery backend.

    Each source is responsible for:
      - Fetching papers published since a given watermark date.
      - Inserting new rows into ``papers`` via INSERT ... ON CONFLICT DO NOTHING
        (idempotent; cross-source dedup happens on ``doi``).
      - Committing after each page and advancing ``crawl_state`` so a crash
        only loses the current page, not the whole run.
    """

    source_name: str  # "openalex" | "semantic_scholar" | "wiley"

    def fetch_since(self, watermark: date | None, session: Session) -> int:
        """Fetch papers published on or after *watermark* and insert new ones.

        Args:
            watermark: Start of the date window. None means fetch from the
                       beginning (first run or explicit full back-fill).
            session:   Open SQLAlchemy session; caller owns commit/rollback.

        Returns:
            Count of newly inserted rows (duplicates silently skipped).
        """
        ...
