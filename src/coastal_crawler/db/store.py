"""Data-access layer — all SQL that touches the database lives here.

Every function accepts an open ``Session``; callers own commit/rollback.
This keeps transactions composable: a discovery source can insert papers
and advance its watermark in a single atomic commit.

Column-name note: the ORM attribute ``Paper.paper_metadata`` maps to the
DB column ``metadata``.  When passing raw dicts to ``upsert_papers`` use
the **column** name (``"metadata"``), not the attribute name.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from coastal_crawler.adapter import ExtractionResult
from coastal_crawler.db.models import CrawlState, Extraction, Paper


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------

def upsert_papers(records: list[dict[str, Any]], session: Session) -> int:
    """Batch-insert paper records with conflict-safe cross-source deduplication.

    ``doi`` is the primary dedup key — a paper already known from any source
    is silently skipped when the same DOI arrives from another source.
    For papers without a DOI, dedup falls back to the source-specific ID.

    Conflict resolution order:
    1. Records that have a ``doi``     → conflict on ``doi``
    2. Records with no ``doi`` but an ``openalex_id`` → conflict on ``openalex_id``
    3. Records with neither, but a ``semantic_scholar_id`` → conflict on that
    4. Records with no identifier at all are dropped.

    Wiley papers always carry a DOI so they always land in bucket 1.

    Returns:
        Count of newly inserted rows (duplicates silently skipped).
    """
    if not records:
        return 0

    doi_records = [r for r in records if r.get("doi")]
    no_doi_oa = [r for r in records if not r.get("doi") and r.get("openalex_id")]
    no_doi_s2 = [
        r
        for r in records
        if not r.get("doi")
        and not r.get("openalex_id")
        and r.get("semantic_scholar_id")
    ]

    inserted = 0

    # doi_records may also carry an openalex_id; specifying only `doi` as the
    # conflict target would let PostgreSQL crash on the openalex_id unique
    # constraint when a paper was previously stored without a DOI.  Omitting
    # index_elements makes ON CONFLICT DO NOTHING catch any unique violation.
    if doi_records:
        stmt = pg_insert(Paper.__table__).values(doi_records).on_conflict_do_nothing()
        inserted += session.execute(stmt).rowcount

    for batch, conflict_col in (
        (no_doi_oa, "openalex_id"),
        (no_doi_s2, "semantic_scholar_id"),
    ):
        if not batch:
            continue
        stmt = pg_insert(Paper.__table__).values(batch).on_conflict_do_nothing(
            index_elements=[conflict_col]
        )
        inserted += session.execute(stmt).rowcount

    return inserted


def claim_batch_for_filter(batch_size: int, session: Session) -> list[Paper]:
    """Atomically claim up to *batch_size* discovered papers for relevance filtering.

    Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so concurrent filter workers never
    claim the same row.  Claimed papers are set to ``status='filtering'``.

    Returns:
        List of claimed Paper objects.
    """
    stmt = (
        select(Paper)
        .where(Paper.status == "discovered")
        .with_for_update(skip_locked=True)
        .limit(batch_size)
    )
    papers = list(session.scalars(stmt).all())
    for paper in papers:
        paper.status = "filtering"
    session.flush()
    return papers


def mark_relevant(paper_id: int, confidence: float | None, session: Session) -> None:
    """Flip a filtered paper to ``status='relevant'`` and store the confidence score."""
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="relevant", filter_confidence=confidence)
    )


def mark_irrelevant(paper_id: int, confidence: float | None, session: Session) -> None:
    """Flip a filtered paper to ``status='irrelevant'`` and store the confidence score."""
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="irrelevant", filter_confidence=confidence)
    )


def reset_to_discovered(paper_id: int, session: Session) -> None:
    """Reset a paper from ``status='filtering'`` back to ``status='discovered'``.

    Used when the filter API call fails — the paper will be retried on the
    next filter run rather than being permanently rejected.
    """
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="discovered")
    )


def requeue_filtering(session: Session) -> int:
    """Reset all ``filtering`` papers back to ``discovered``.

    Papers are left in ``status='filtering'`` if a filter job is killed
    mid-batch (e.g. walltime limit). Call this before resubmitting to rescue
    those stranded papers.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "filtering")
        .values(status="discovered")
    )
    return result.rowcount


def requeue_filtered(session: Session) -> int:
    """Reset all ``relevant`` and ``irrelevant`` papers back to ``discovered``.

    Clears ``filter_confidence`` so the next run scores them fresh.
    Use this to re-run the filter after updating the prompt or model.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status.in_(["relevant", "irrelevant"]))
        .values(status="discovered", filter_confidence=None)
    )
    return result.rowcount


def requeue_irrelevant(session: Session) -> int:
    """Reset all ``irrelevant`` papers back to ``discovered`` for re-filtering.

    Clears ``filter_confidence`` so the next run scores them fresh.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "irrelevant")
        .values(status="discovered", filter_confidence=None)
    )
    return result.rowcount


def claim_batch(batch_size: int, session: Session) -> list[Paper]:
    """Atomically claim up to *batch_size* relevant papers for extraction.

    Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so multiple worker processes can
    call this concurrently without ever claiming the same row.  Claimed papers
    are immediately set to ``status='processing'`` within the same open
    transaction — the worker should not commit until extraction is complete
    (or it has written a failure).

    Returns:
        List of claimed Paper objects (may be shorter than batch_size if fewer
        relevant papers exist).
    """
    stmt = (
        select(Paper)
        .where(Paper.status == "relevant")
        .with_for_update(skip_locked=True)
        .limit(batch_size)
    )
    papers = list(session.scalars(stmt).all())
    for paper in papers:
        paper.status = "processing"
    session.flush()
    return papers


def mark_extracted(paper_id: int, session: Session) -> None:
    """Flip a paper to ``status='extracted'`` and stamp ``extracted_at``."""
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="extracted", extracted_at=datetime.now(timezone.utc))
    )


def mark_failed(paper_id: int, error: str, session: Session) -> None:
    """Flip a paper to ``status='failed'`` and record the error text."""
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="failed", error=error)
    )


def mark_inaccessible(paper_id: int, session: Session) -> None:
    """Flip a paper to ``status='inaccessible'`` when its PDF URL cannot be reached."""
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="inaccessible")
    )


def requeue_inaccessible(session: Session) -> int:
    """Reset all ``inaccessible`` papers back to ``discovered`` for re-filtering.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "inaccessible")
        .values(status="discovered")
    )
    return result.rowcount


def requeue_failed(session: Session) -> int:
    """Reset all ``failed`` papers back to ``relevant`` for extraction retry.

    Resets to 'relevant' (not 'discovered') so papers skip re-filtering —
    they were already deemed relevant and only failed during PDF extraction.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "failed")
        .values(status="relevant", error=None)
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Extractions
# ---------------------------------------------------------------------------

def insert_extraction(
    paper_id: int,
    result: ExtractionResult,
    session: Session,
) -> Extraction:
    """Insert a single extraction result row.

    Always adds a new row — never overwrites — so re-running with a new model
    version accumulates rows without discarding prior results.

    Returns:
        The flushed (but not yet committed) Extraction object.
    """
    extraction = Extraction(
        paper_id=paper_id,
        schema_name=result.schema_name,
        model_version=result.model_version,
        data=result.data,
        confidence=result.confidence,
        provenance=result.provenance,
        latitude=result.latitude,
        longitude=result.longitude,
    )
    session.add(extraction)
    session.flush()
    return extraction


# ---------------------------------------------------------------------------
# Crawl state / watermarks
# ---------------------------------------------------------------------------

def get_watermark(source: str, session: Session) -> date | None:
    """Return the stored watermark date for *source*, or ``None`` if not yet set."""
    state = session.get(CrawlState, source)
    return state.watermark if state else None


def set_watermark(source: str, watermark: date, session: Session) -> None:
    """Upsert the watermark for *source*.

    Safe to call on every page of a paginated crawl — the INSERT … ON CONFLICT
    DO UPDATE means the first call creates the row and subsequent calls
    advance it.
    """
    stmt = (
        pg_insert(CrawlState.__table__)
        .values(source=source, watermark=watermark)
        .on_conflict_do_update(
            index_elements=["source"],
            set_={
                "watermark": text("GREATEST(crawl_state.watermark, EXCLUDED.watermark)"),
                "updated_at": text("now()"),
            },
        )
    )
    session.execute(stmt)


# ---------------------------------------------------------------------------
# Convenience queries (used by CLI / monitoring)
# ---------------------------------------------------------------------------

def count_by_status(session: Session) -> dict[str, int]:
    """Return a dict mapping each status value to its paper count."""
    rows = session.execute(
        select(Paper.status, func.count(Paper.id)).group_by(Paper.status)
    ).all()
    return {status: count for status, count in rows}


def recent_papers(limit: int, session: Session) -> list[Paper]:
    """Return the most recently discovered papers, newest first."""
    stmt = select(Paper).order_by(Paper.discovered_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def papers_with_extractions(
    session: Session,
    paper_ids: list[int] | None = None,
    limit: int = 20,
) -> list[Paper | None]:
    """Return papers together with their extraction rows, eagerly loaded.

    With no ``paper_ids``, returns the most recently extracted papers
    (``status == 'extracted'``), newest first, capped at ``limit``.
    With ``paper_ids``, returns one entry per requested ID in that order
    (``None`` for any ID not found), regardless of status — useful for
    checking a paper that failed partway through extraction. ``limit`` is
    ignored in this mode.
    """
    stmt = select(Paper).options(selectinload(Paper.extractions))
    if paper_ids:
        papers = list(session.scalars(stmt.where(Paper.id.in_(paper_ids))).all())
        by_id = {p.id: p for p in papers}
        return [by_id.get(pid) for pid in paper_ids]

    stmt = stmt.where(Paper.status == "extracted").order_by(Paper.extracted_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())
