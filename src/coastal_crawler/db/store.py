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

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from coastal_crawler.adapter import ExtractionResult
from coastal_crawler.db.models import CrawlState, Extraction, Location, Paper, PaperOcrContext, Vote


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


def claim_batch_for_ocr(batch_size: int, session: Session) -> list[Paper]:
    """Atomically claim up to *batch_size* relevant papers for OCR.

    Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so multiple OCR worker processes
    can run concurrently without ever claiming the same row.  Claimed papers
    are immediately set to ``status='ocr_processing'`` within the same open
    transaction — the worker should not commit until OCR is complete (or it
    has written a failure).

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
        paper.status = "ocr_processing"
    session.flush()
    return papers


def mark_ocr_done(paper_id: int, session: Session) -> None:
    """Flip a paper to ``status='ocr_done'`` once its OCR text file is written and closed."""
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="ocr_done")
    )


def mark_ocr_failed(paper_id: int, error: str, session: Session) -> None:
    """Flip a paper to ``status='ocr_failed'`` and record the error text."""
    session.execute(
        update(Paper)
        .where(Paper.id == paper_id)
        .values(status="ocr_failed", error=error)
    )


def reset_ocr_processing_to_relevant(paper_id: int, session: Session) -> bool:
    """Reset one claimed paper from ``ocr_processing`` back to ``relevant``.

    Used when a Wiley paper is claimed for OCR but its PDF hasn't been
    pre-downloaded yet by scripts/wiley_download.py — this isn't a real OCR
    failure, so the paper goes back to the queue instead of ``ocr_failed``.
    Guarded on ``status='ocr_processing'`` so it can't stomp a concurrent
    transition (e.g. another path already marked it ocr_failed).

    Returns:
        True if the row was updated, False if it was no longer 'ocr_processing'.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.id == paper_id, Paper.status == "ocr_processing")
        .values(status="relevant")
    )
    return result.rowcount > 0


def requeue_ocr_processing(session: Session) -> int:
    """Reset all ``ocr_processing`` papers back to ``relevant``.

    Papers are left in ``status='ocr_processing'`` if an OCR job is killed
    mid-batch (e.g. walltime limit, OOM, node preemption). Call this before
    resubmitting to rescue those stranded papers.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "ocr_processing")
        .values(status="relevant")
    )
    return result.rowcount


def requeue_ocr_failed(session: Session) -> int:
    """Reset all ``ocr_failed`` papers back to ``relevant`` for an OCR retry.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "ocr_failed")
        .values(status="relevant", error=None)
    )
    return result.rowcount


def requeue_ocr(session: Session) -> int:
    """Reset every paper touched by OCR or extraction back to ``relevant``.

    Resets ``ocr_done``, ``ocr_failed``, ``processing``, ``extracted``, and
    ``failed`` papers to ``relevant``, clearing ``error``/``extracted_at``.
    Use this to force a full re-OCR (e.g. after changing DOC_LM_MODEL) —
    the OCR text files on disk are left in place and simply get overwritten
    as papers are re-claimed and re-OCR'd.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status.in_(["ocr_done", "ocr_failed", "processing", "extracted", "failed"]))
        .values(status="relevant", error=None, extracted_at=None)
    )
    return result.rowcount


def claim_batch(batch_size: int, session: Session) -> list[Paper]:
    """Atomically claim up to *batch_size* OCR'd papers for extraction.

    Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so multiple worker processes can
    call this concurrently without ever claiming the same row.  Claimed papers
    are immediately set to ``status='processing'`` within the same open
    transaction — the worker should not commit until extraction is complete
    (or it has written a failure).

    Returns:
        List of claimed Paper objects (may be shorter than batch_size if fewer
        ocr_done papers exist).
    """
    stmt = (
        select(Paper)
        .where(Paper.status == "ocr_done")
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


def mark_ocr_failed_if_relevant(paper_id: int, error: str, session: Session) -> bool:
    """Flip a paper to ``ocr_failed`` only if it's still ``relevant``.

    Used by scripts/wiley_download.py, which reads ``relevant`` papers
    without claiming them (no ``FOR UPDATE``) — an OCR worker can claim the
    same paper into ``ocr_processing`` while the download is in flight, so
    an unconditional write here could clobber a paper that's actively being
    OCR'd. The status guard makes the write a no-op if that race is lost,
    instead of unconditional ``mark_ocr_failed``. Targets ``ocr_failed``
    (not ``failed``) since a Wiley download error is an OCR-stage failure —
    landing it in ``failed`` would make ``requeue-failed`` route it to
    ``ocr_done`` where extraction would find no OCR text file.

    Returns:
        True if the row was updated, False if it was no longer 'relevant'.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.id == paper_id, Paper.status == "relevant")
        .values(status="ocr_failed", error=error)
    )
    return result.rowcount > 0


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
    """Reset all ``failed`` papers back to ``ocr_done`` for extraction retry.

    Resets to 'ocr_done' (not 'relevant') so papers skip both re-filtering
    and re-OCR — they were already deemed relevant and OCR'd, and only
    failed during measurement extraction; their OCR text file is still
    valid on disk.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "failed")
        .values(status="ocr_done", error=None)
    )
    return result.rowcount


def requeue_processing(session: Session) -> int:
    """Reset all ``processing`` papers back to ``ocr_done``.

    Papers are left in ``status='processing'`` if an extraction job is
    killed mid-batch (e.g. walltime limit, OOM, node preemption). Call this
    before resubmitting to rescue those stranded papers. Resets to
    'ocr_done' (not 'relevant') since these papers were already OCR'd —
    their OCR text file is still valid on disk, so a retry shouldn't force
    a redundant re-OCR. Matters more once multiple concurrent extraction
    jobs are running (see EFFICIENCY.md item 1) — more jobs means more
    chances of a stranded batch.

    Returns:
        Count of papers requeued.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.status == "processing")
        .values(status="ocr_done")
    )
    return result.rowcount


def reset_extractions(session: Session) -> tuple[int, int]:
    """Wipe all extraction results and rewind extraction-stage papers to 'ocr_done'.

    Deletes every row in ``extractions`` and resets any paper with
    status in ('extracted', 'processing', 'failed') back to 'ocr_done',
    clearing ``extracted_at``/``error``. OCR text files on disk are left in
    place (their status='ocr_done' rows already point at valid files), and
    filtering results ('relevant'/'irrelevant' papers not yet OCR'd) are
    left untouched — restoring the DB to its post-OCR, pre-extraction state.

    Returns:
        (extractions_deleted, papers_reset) counts.
    """
    deleted = session.execute(delete(Extraction)).rowcount
    result = session.execute(
        update(Paper)
        .where(Paper.status.in_(["extracted", "processing", "failed"]))
        .values(status="ocr_done", extracted_at=None, error=None)
    )
    return deleted, result.rowcount


def reset_processing_to_relevant(paper_id: int, session: Session) -> bool:
    """Reset one claimed paper from ``processing`` back to ``relevant``.

    Used when a Wiley paper is claimed for extraction but its PDF hasn't
    been pre-downloaded yet by scripts/wiley_download.py — this isn't a real
    extraction failure, so the paper goes back to the queue instead of
    ``failed``. Guarded on ``status='processing'`` so it can't stomp a
    concurrent transition (e.g. another path already marked it failed).

    Returns:
        True if the row was updated, False if it was no longer 'processing'.
    """
    result = session.execute(
        update(Paper)
        .where(Paper.id == paper_id, Paper.status == "processing")
        .values(status="relevant")
    )
    return result.rowcount > 0


# ---------------------------------------------------------------------------
# Extractions
# ---------------------------------------------------------------------------

def insert_extraction(
    paper_id: int,
    result: ExtractionResult,
    session: Session,
    page_number: int | None = None,
    page_matched: bool | None = None,
) -> Extraction:
    """Insert a single extraction result row.

    Always adds a new row — never overwrites — so re-running with a new model
    version accumulates rows without discarding prior results.

    `page_number`/`page_matched` are the site's find_snippet() heuristic
    result — not part of `ExtractionResult` because they're derived by the
    caller (worker.py) from the paper's OCR text, not produced by the
    extraction adapter itself. Default to None so existing call sites that
    don't compute them still work.

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
        page_number=page_number,
        page_matched=page_matched,
    )
    session.add(extraction)
    session.flush()
    return extraction


def upsert_paper_ocr_context(paper_id: int, context: str, session: Session) -> None:
    """Insert or update the one stored copy of a paper's full OCR text.

    Called once per paper per extraction batch (see worker.py), not once per
    measurement record — see PaperOcrContext's docstring for why this
    replaced embedding `context` in every extraction row's `data`.
    """
    stmt = pg_insert(PaperOcrContext).values(paper_id=paper_id, context=context)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PaperOcrContext.paper_id], set_={"context": stmt.excluded.context}
    )
    session.execute(stmt)


def get_paper_ocr_context(session: Session, paper_id: int) -> str | None:
    """Return the stored OCR text for a paper, or None if not yet recorded."""
    return session.scalar(
        select(PaperOcrContext.context).where(PaperOcrContext.paper_id == paper_id)
    )


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


def relevant_papers(session: Session) -> list[tuple[int, str | None, str | None]]:
    """Return (id, oa_pdf_url, discovered_from) for every ``relevant`` paper.

    Used by scripts/wiley_download.py to find Wiley papers awaiting a
    pre-download. Returns plain column tuples (not ORM objects) since the
    caller reads these fields after the session that produced them has
    closed — mirrors the pattern in scripts/recover_oa_inaccessible.py.
    """
    stmt = select(Paper.id, Paper.oa_pdf_url, Paper.discovered_from).where(
        Paper.status == "relevant"
    )
    return [(pid, url, discovered_from) for pid, url, discovered_from in session.execute(stmt)]


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


# ---------------------------------------------------------------------------
# Site queries (results website — read path)
# ---------------------------------------------------------------------------

def _extraction_rows(
    session: Session,
    *,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
    paper_id: int | None = None,
    page_number: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[Any], int]:
    """Shared narrow+hydrate dedup query behind `list_extractions` and
    `page_extractions`. `export_extractions` is deliberately *not* built on
    this — its `ecosystem_type` filter means something different
    (location-majority, not per-row) than every other caller here, and
    folding that into this shared function would also change
    `list_extractions`/`page_extractions`'s filter semantics. See
    notes/coastal-crawler/builds/2026-08-11-location-export-01.md.
    `limit=None` returns every matching row (unpaginated); a given `limit`
    paginates via `offset`.

    Deduped on (paper_id, attribute, value, units), keeping the highest `id`
    — extraction rows accumulate across re-extraction passes (see
    insert_extraction), so without this the same real-world measurement
    would appear once per pass. `paper_id` narrows to one paper's page
    (site/app.py's paper_view) while reusing this same dedup — a
    re-extracted paper is exactly where duplicate rows are most visible.
    `page_number` (site/app.py's page_view) further narrows within that
    paper; it's applied to the same pre-dedup narrow stage as `paper_id` so
    dedup always happens across the *whole* paper first — filtering to a
    page before deduping could let a row that lost the global dedup
    resurface, making `pages_for_paper`'s per-page count disagree with this
    function's row count for that page.

    Two-stage query, deliberately: every extraction row carries a ~55KB
    embedded copy of its paper's full OCR text (in `data->'context'`), and
    Postgres must detoast that whole blob to evaluate *any* `data->>'...'`
    expression on a row with no supporting index. Computing the dedup key
    (attribute/value/units) across every row in one wide query — even one
    that excludes `context` from its SELECT list — still pays that cost,
    since the key itself is a JSONB expression. So stage 1 works out just
    the winning page's `id`s from a narrow projection (covered by this
    migration's `ix_extractions_dedup_key` index, so it never touches the
    heap), and stage 2 hydrates only those ids with the full fields — see
    migration b1c2d3e4f5a6 and its indexes for the counterpart.

    `title` (substring, case-insensitive, autoescaped — same as
    `search_papers_by_title`) joins `Paper` into the narrow stage; the
    other filters stay join-free when `title` is absent.

    Returns:
        (rows, total_count). Each row exposes .id, .paper_id, .attribute,
        .value, .units, .entity_name, .identifiers, .location, .latitude,
        .longitude, .event_date, .sub_location, .additional_details,
        .ecosystem_type, .judgement, .confidence, .created_at, .title,
        .doi, .authors, .publication_date.
    """
    dedup_key = (
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
    )
    narrow = select(
        Extraction.id,
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
        Extraction.created_at,
    )
    if title:
        narrow = narrow.join(Paper, Paper.id == Extraction.paper_id).where(
            Paper.title.icontains(title, autoescape=True)
        )
    if attribute:
        narrow = narrow.where(Extraction.data["attribute"].astext == attribute)
    if ecosystem_type:
        narrow = narrow.where(Extraction.data["ecosystem_type"].astext == ecosystem_type)
    if paper_id is not None:
        narrow = narrow.where(Extraction.paper_id == paper_id)
    if page_number is not None:
        narrow = narrow.where(Extraction.page_number == page_number)
    deduped_ids = narrow.distinct(*dedup_key).order_by(*dedup_key, Extraction.id.desc()).subquery()

    total = session.execute(select(func.count()).select_from(deduped_ids)).scalar_one()

    page_ids_stmt = select(deduped_ids.c.id).order_by(deduped_ids.c.created_at.desc())
    if limit is not None:
        page_ids_stmt = page_ids_stmt.limit(limit).offset(offset)
    page_ids = list(session.scalars(page_ids_stmt).all())
    if not page_ids:
        return [], total

    hydrate_stmt = (
        select(
            Extraction.id,
            Extraction.paper_id,
            Extraction.data["attribute"].astext.label("attribute"),
            Extraction.data["value"].astext.label("value"),
            Extraction.data["units"].astext.label("units"),
            Extraction.data["name"].astext.label("entity_name"),
            Extraction.data["identifiers"].astext.label("identifiers"),
            Extraction.data["location"].astext.label("location"),
            Extraction.data["latitude"].astext.label("latitude"),
            Extraction.data["longitude"].astext.label("longitude"),
            Extraction.data["date"].astext.label("event_date"),
            Extraction.data["sub_location"].astext.label("sub_location"),
            Extraction.data["additional_details"].astext.label("additional_details"),
            Extraction.data["ecosystem_type"].astext.label("ecosystem_type"),
            Extraction.judgement,
            Extraction.confidence,
            Extraction.created_at,
            Paper.title,
            Paper.doi,
            Paper.authors,
            Paper.publication_date,
        )
        .join(Paper, Paper.id == Extraction.paper_id)
        .where(Extraction.id.in_(page_ids))
        .order_by(Extraction.created_at.desc())
    )
    rows = list(session.execute(hydrate_stmt).all())
    return rows, total


def list_papers_with_extractions(
    session: Session,
    page: int = 1,
    page_size: int = 25,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> tuple[list[Any], int]:
    """Paginated distinct papers with at least one filter-matching
    extraction, for the results website's paper-list view (`GET /`). One
    row per paper: `.id`, `.title`, `.authors`, `.publication_date`, `.doi`,
    `.extraction_count`, `.last_extracted`. Ordered by `last_extracted`
    (`MAX(extractions.created_at)` among that paper's filter-matching rows)
    descending — filter-scoped, unlike `Paper.extracted_at` (used by the
    unrelated `papers_with_extractions`), which has no way to reflect an
    active `attribute`/`ecosystem_type`/`title` filter.

    Same two-stage narrow-then-hydrate shape as `_extraction_rows` (avoids
    detoasting `data`'s embedded OCR-context blob per row) and the same
    `DISTINCT ON (paper_id, attribute, value, units)` dedup key, so
    `extraction_count` matches what `_extraction_rows`/`pages_for_paper`
    would count for that paper under the same filter.
    """
    dedup_key = (
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
    )
    narrow = select(
        Extraction.id,
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
        Extraction.created_at,
    )
    if title:
        narrow = narrow.join(Paper, Paper.id == Extraction.paper_id).where(
            Paper.title.icontains(title, autoescape=True)
        )
    if attribute:
        narrow = narrow.where(Extraction.data["attribute"].astext == attribute)
    if ecosystem_type:
        narrow = narrow.where(Extraction.data["ecosystem_type"].astext == ecosystem_type)
    deduped = narrow.distinct(*dedup_key).order_by(*dedup_key, Extraction.id.desc()).subquery()

    paper_agg = (
        select(
            deduped.c.paper_id,
            func.count().label("extraction_count"),
            func.max(deduped.c.created_at).label("last_extracted"),
        )
        .group_by(deduped.c.paper_id)
        .subquery()
    )

    total = session.execute(select(func.count()).select_from(paper_agg)).scalar_one()

    page_ids_stmt = (
        select(paper_agg.c.paper_id)
        .order_by(paper_agg.c.last_extracted.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    page_paper_ids = list(session.scalars(page_ids_stmt).all())
    if not page_paper_ids:
        return [], total

    hydrate_stmt = (
        select(
            Paper.id,
            Paper.title,
            Paper.authors,
            Paper.publication_date,
            Paper.doi,
            paper_agg.c.extraction_count,
            paper_agg.c.last_extracted,
        )
        .join(paper_agg, paper_agg.c.paper_id == Paper.id)
        .where(Paper.id.in_(page_paper_ids))
        .order_by(paper_agg.c.last_extracted.desc())
    )
    rows = list(session.execute(hydrate_stmt).all())
    return rows, total


def list_extractions(
    session: Session,
    page: int = 1,
    page_size: int = 25,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
    paper_id: int | None = None,
) -> tuple[list[Any], int]:
    """Paginated (measurement, paper) rows for the results website's list view.

    See `_extraction_rows` for the dedup/query shape this wraps. `title`
    combines with `attribute`/`ecosystem_type` via AND, same as those two
    combine with each other today.
    """
    return _extraction_rows(
        session,
        title=title,
        attribute=attribute,
        ecosystem_type=ecosystem_type,
        paper_id=paper_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )


def pages_for_paper(
    session: Session,
    paper_id: int,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> list[Any]:
    """Rows of (page_number, count) for one paper's extractions, grouped and
    ordered by page. Each row supports `[0]`/`[1]` (or `.page_number`/
    `.count` via SQLAlchemy's Row) access to (page_number: int | None,
    count: int) — typed `list[Any]` to match `_extraction_rows`' row typing
    elsewhere in this module.

    Reads the stored `page_number` column directly — no live find_snippet()
    calls (see migration e4f5a6b7c8d9 / worker.py). Uses the same
    `DISTINCT ON (paper_id, attribute, value, units)` dedup key as
    `_extraction_rows` so a paper re-extracted with a new model version
    doesn't double-count a page. `page_number=None` (find_snippet() found no
    page tags at all) sorts last.
    """
    dedup_key = (
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
    )
    narrow = select(
        Extraction.id,
        Extraction.page_number,
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
    ).where(Extraction.paper_id == paper_id)
    if attribute:
        narrow = narrow.where(Extraction.data["attribute"].astext == attribute)
    if ecosystem_type:
        narrow = narrow.where(Extraction.data["ecosystem_type"].astext == ecosystem_type)
    deduped = narrow.distinct(*dedup_key).order_by(*dedup_key, Extraction.id.desc()).subquery()

    stmt = (
        select(deduped.c.page_number, func.count())
        .group_by(deduped.c.page_number)
        .order_by(deduped.c.page_number.asc().nulls_last())
    )
    return list(session.execute(stmt).all())


def page_extractions(
    session: Session,
    paper_id: int,
    page_number: int,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> list[Any]:
    """All (measurement, paper) rows on one page of one paper, filter-scoped
    — the results website's page-detail view (site/app.py's page_view).

    Thin wrapper over `_extraction_rows` with `paper_id`+`page_number` set —
    see that function's docstring for why `page_number` is applied there
    (pre-dedup) rather than as a separate post-dedup filter: it keeps this
    row count consistent with `pages_for_paper`'s per-page count for the
    same paper. Same dedup and row shape as `list_extractions`, unpaginated
    (one page's measurement count is small).
    """
    rows, _total = _extraction_rows(
        session,
        title=title,
        attribute=attribute,
        ecosystem_type=ecosystem_type,
        paper_id=paper_id,
        page_number=page_number,
    )
    return rows


def export_extractions(
    session: Session,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> list[Any]:
    """Every (measurement, paper) row matching the given filters, unpaginated
    — for the results website's location-centric CSV export
    (`/export.csv`). Self-contained (not built on `_extraction_rows` — see
    that function's docstring for why), same
    `DISTINCT ON (paper_id, attribute, value, units)` dedup key/shape (one
    row per individual measurement, highest `id` wins across re-extraction
    passes) and the same two-stage narrow-then-hydrate query as
    `_extraction_rows` (avoids detoasting each row's ~55KB embedded
    OCR-context JSONB blob for rows that lose the dedup).

    `title`/`attribute` filter the same way `_extraction_rows` does
    (per-paper substring match / per-row exact match). `ecosystem_type`
    filters by *location* membership, not the row's own
    `data->>'ecosystem_type'`: it selects every extraction row belonging to
    a location where `ecosystem_type` is among that location's top-tied
    ecosystem types (see `_location_top_ecosystem_types`) — so a matching
    row's own `ecosystem_type` value can be null, or something else
    entirely, and it still exports as long as its location matches. Rows
    with no resolved `location_id` never match a specific `ecosystem_type`
    filter value (nothing to match against) but still export when no
    `ecosystem_type` filter is given.

    Each row exposes `.location_id`, `.location_name`, `.location_latitude`,
    `.location_longitude` (from a `LEFT OUTER JOIN locations` — null for an
    unresolved row, not excluded) alongside the raw per-record
    `.entity_name`, `.identifiers`, `.location_description` (renamed from
    the raw `location` field to avoid colliding with `.location_name`) —
    see notes/coastal-crawler/builds/2026-08-11-location-export-01.md for
    why both the raw and canonical fields are kept.
    """
    dedup_key = (
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
    )
    narrow = select(
        Extraction.id,
        Extraction.paper_id,
        Extraction.data["attribute"].astext,
        Extraction.data["value"].astext,
        Extraction.data["units"].astext,
        Extraction.created_at,
    )
    if title:
        narrow = narrow.join(Paper, Paper.id == Extraction.paper_id).where(
            Paper.title.icontains(title, autoescape=True)
        )
    if attribute:
        narrow = narrow.where(Extraction.data["attribute"].astext == attribute)
    if ecosystem_type:
        top_types = _location_top_ecosystem_types().subquery()
        matching_location_ids = select(top_types.c.location_id).where(
            top_types.c.ecosystem_type == ecosystem_type
        )
        narrow = narrow.where(Extraction.location_id.in_(matching_location_ids))
    deduped_ids = narrow.distinct(*dedup_key).order_by(*dedup_key, Extraction.id.desc()).subquery()

    ids_stmt = select(deduped_ids.c.id).order_by(deduped_ids.c.created_at.desc())
    ids = list(session.scalars(ids_stmt).all())
    if not ids:
        return []

    hydrate_stmt = (
        select(
            Extraction.id,
            Extraction.paper_id,
            Extraction.data["attribute"].astext.label("attribute"),
            Extraction.data["value"].astext.label("value"),
            Extraction.data["units"].astext.label("units"),
            Location.id.label("location_id"),
            Location.name.label("location_name"),
            Location.latitude.label("location_latitude"),
            Location.longitude.label("location_longitude"),
            Extraction.data["name"].astext.label("entity_name"),
            Extraction.data["identifiers"].astext.label("identifiers"),
            Extraction.data["location"].astext.label("location_description"),
            Extraction.data["date"].astext.label("event_date"),
            Extraction.data["sub_location"].astext.label("sub_location"),
            Extraction.data["additional_details"].astext.label("additional_details"),
            Extraction.data["ecosystem_type"].astext.label("ecosystem_type"),
            Extraction.judgement,
            Extraction.confidence,
            Extraction.created_at,
            Paper.title,
            Paper.doi,
            Paper.authors,
            Paper.publication_date,
        )
        .join(Paper, Paper.id == Extraction.paper_id)
        .outerjoin(Location, Location.id == Extraction.location_id)
        .where(Extraction.id.in_(ids))
        .order_by(Extraction.created_at.desc())
    )
    return list(session.execute(hydrate_stmt).all())


def list_ecosystem_types(session: Session) -> list[str]:
    """Distinct non-null ecosystem types present across all extractions, for the site's facet filter."""
    stmt = (
        select(Extraction.data["ecosystem_type"].astext)
        .where(Extraction.data["ecosystem_type"].astext.is_not(None))
        .distinct()
        .order_by(Extraction.data["ecosystem_type"].astext)
    )
    return list(session.scalars(stmt).all())


def get_paper(session: Session, paper_id: int) -> Paper | None:
    """Return one paper by id, for the site's paper-detail page header."""
    return session.get(Paper, paper_id)


def search_papers_by_title(session: Session, query: str, limit: int) -> list[Any]:
    """Title-search papers that have at least one extraction, for the site's search box.

    `icontains`/`istartswith` are used with `autoescape=True` so a user typing
    a literal `%` or `_` doesn't turn into a SQL wildcard — SQLAlchemy's
    plain `.ilike()` does not escape these. Excludes papers with zero
    extractions (e.g. still `discovered`/`irrelevant`/`ocr_failed`) since a
    search result is meant to lead to recorded measurements. Ranked
    prefix-match-first, then shortest title, so results don't look like they
    reshuffle randomly across keystrokes — there's no trigram index backing
    this (see WEBSITE.md; deliberately skipped at the current ~250-paper
    scale).
    """
    prefix_rank = case((Paper.title.istartswith(query, autoescape=True), 0), else_=1)
    stmt = (
        select(Paper.id, Paper.title, Paper.authors, Paper.publication_date, Paper.doi)
        .where(Paper.title.icontains(query, autoescape=True))
        .where(Paper.extractions.any())
        .order_by(prefix_rank, func.length(Paper.title))
        .limit(limit)
    )
    return list(session.execute(stmt).all())


def get_extraction(session: Session, extraction_id: int) -> Extraction | None:
    """Return one extraction with its full `data` (including OCR `context`) and parent paper.

    Unlike `list_extractions`, `context` is kept here — the detail page needs
    it to locate an OCR-text snippet for this specific measurement.
    """
    stmt = (
        select(Extraction)
        .options(selectinload(Extraction.paper))
        .where(Extraction.id == extraction_id)
    )
    return session.scalars(stmt).first()


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------

def record_vote(
    session: Session,
    extraction_id: int,
    vote: bool,
    voter_hash: str | None,
) -> str | None:
    """Insert a vote and recompute the extraction's majority `judgement`.

    A tie leaves `judgement` unresolved (None) rather than picking a side.

    Returns:
        The recomputed judgement ('valid', 'invalid', or None on a tie).
    """
    session.add(Vote(extraction_id=extraction_id, vote=vote, voter_hash=voter_hash))
    session.flush()

    result = session.execute(
        select(Vote.vote, func.count())
        .where(Vote.extraction_id == extraction_id)
        .group_by(Vote.vote)
    )
    counts: dict[bool, int] = {v: c for v, c in result}
    valid_count, invalid_count = counts.get(True, 0), counts.get(False, 0)
    if valid_count > invalid_count:
        judgement = "valid"
    elif invalid_count > valid_count:
        judgement = "invalid"
    else:
        judgement = None

    session.execute(
        update(Extraction).where(Extraction.id == extraction_id).values(judgement=judgement)
    )
    return judgement


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _location_ecosystem_type_counts() -> Any:
    """(location_id, ecosystem_type, count) per location per non-null
    ecosystem_type recorded among its extractions — raw `COUNT(*)`,
    deliberately not deduped by (paper_id, attribute, value, units) the way
    `_extraction_rows`/`pages_for_paper` dedupe, so a paper re-extracted
    multiple times (extraction rows accumulate across model-version
    reruns, see insert_extraction) contributes once per extraction pass
    rather than once. See
    notes/coastal-crawler/builds/2026-08-11-location-resolution-01.md.

    Shared base subquery for two different collapses: `location_majority_ecosystem_type`
    picks one ecosystem_type per location (a single display answer, ties
    broken arbitrarily-but-deterministically); `_location_top_ecosystem_types`
    keeps every ecosystem_type tied for first (for filter *inclusion*,
    where an arbitrary tiebreak would silently misclassify a tied location
    — see notes/coastal-crawler/builds/2026-08-11-location-export-01.md).
    """
    return (
        select(
            Extraction.location_id,
            Extraction.data["ecosystem_type"].astext.label("ecosystem_type"),
            func.count().label("count"),
        )
        .where(Extraction.location_id.is_not(None))
        .where(Extraction.data["ecosystem_type"].astext.is_not(None))
        .group_by(Extraction.location_id, Extraction.data["ecosystem_type"].astext)
    )


def location_majority_ecosystem_type(session: Session) -> list[Any]:
    """Return one row per location: (location_id, ecosystem_type, count) for
    the ecosystem_type with the highest raw `COUNT(*)` across that
    location's extraction rows (ties broken by ecosystem_type ascending,
    for determinism).

    Locations with no non-null `ecosystem_type` among their extractions
    (or no extractions at all) don't appear in the result.
    """
    counts = _location_ecosystem_type_counts().subquery()
    stmt = (
        select(counts.c.location_id, counts.c.ecosystem_type, counts.c.count)
        .distinct(counts.c.location_id)
        .order_by(counts.c.location_id, counts.c.count.desc(), counts.c.ecosystem_type.asc())
    )
    return list(session.execute(stmt).all())


def _location_top_ecosystem_types() -> Any:
    """(location_id, ecosystem_type) for every ecosystem_type tied for the
    highest count at that location — one row for a clear majority, more
    than one for a tie. Used by `export_extractions`'s `ecosystem_type`
    filter: a tied location matches every type it's tied on (your call,
    see notes/coastal-crawler/builds/2026-08-11-location-export-01.md) —
    unlike `location_majority_ecosystem_type`, which picks a single
    display answer via an ascending tiebreak, this must not silently
    exclude a location from a filter value it's genuinely tied on.
    """
    counts = _location_ecosystem_type_counts().subquery()
    max_count = (
        select(counts.c.location_id, func.max(counts.c.count).label("max_count"))
        .group_by(counts.c.location_id)
        .subquery()
    )
    return select(counts.c.location_id, counts.c.ecosystem_type).join(
        max_count,
        (counts.c.location_id == max_count.c.location_id) & (counts.c.count == max_count.c.max_count),
    )
