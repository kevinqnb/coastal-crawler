"""Command-line interface for the coastal crawler pipeline."""

from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(
    name="coastal-crawler",
    help="Coastal ecosystem paper discovery and extraction pipeline.",
    no_args_is_help=True,
)


@app.command()
def discover(
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help="Override the watermark date (YYYY-MM-DD). Defaults to the stored watermark.",
    ),
) -> None:
    """Query OpenAlex for new papers and insert them into the database."""
    from datetime import date

    from coastal_crawler.discovery import discover as _discover

    since_date = date.fromisoformat(since) if since else None
    count = _discover(since=since_date)
    typer.echo(f"Inserted {count} new paper(s).")


@app.command(name="filter")
def filter_papers(
    batch_size: int = typer.Option(None, "--batch-size", help="Papers to filter per run. Defaults to FILTER_BATCH_SIZE in .env."),
) -> None:
    """Classify papers as relevant or irrelevant using an LLM."""
    from coastal_crawler.config import get_settings
    from coastal_crawler.relevance_filter import run_filter

    size = batch_size if batch_size is not None else get_settings().filter_batch_size
    relevant, irrelevant, errors = run_filter(batch_size=size)
    typer.echo(
        f"Relevant: {relevant}, irrelevant: {irrelevant}, "
        f"errors (reset for retry): {errors}."
    )


@app.command()
def ocr(
    batch_size: int = typer.Option(10, "--batch-size", help="Papers to process per run."),
    chunk_size: int = typer.Option(
        None, "--chunk-size", help="Papers per OCR GPU call. Defaults to OCR_CHUNK_SIZE in .env."
    ),
) -> None:
    """Claim and OCR a batch of relevant papers, writing text to OCR_DIR."""
    from pathlib import Path

    from coastal_crawler.adapter import build_ocr_adapter
    from coastal_crawler.config import get_settings
    from coastal_crawler.ocr_worker import run_ocr_worker

    settings = get_settings()
    adapter = build_ocr_adapter(settings)
    size = chunk_size if chunk_size is not None else settings.ocr_chunk_size
    ocr_done, failed, requeued = run_ocr_worker(
        batch_size=batch_size,
        adapter=adapter,
        chunk_size=size,
        wiley_pdf_dir=Path(settings.wiley_pdf_dir),
        ocr_dir=Path(settings.ocr_dir),
    )
    message = f"OCR'd {ocr_done}, failed {failed}."
    if requeued:
        message += f" Requeued {requeued} (Wiley PDF not pre-downloaded yet — is scripts/wiley_download.py running?)."
    typer.echo(message)


@app.command()
def extract(
    batch_size: int = typer.Option(10, "--batch-size", help="Papers to process per run."),
    chunk_size: int = typer.Option(
        None, "--chunk-size", help="Papers per extraction GPU call. Defaults to EXTRACTION_CHUNK_SIZE in .env."
    ),
    poll_interval: float = typer.Option(
        60.0, "--poll-interval", help="Seconds to wait between polls when --idle-timeout > 0."
    ),
    idle_timeout: float = typer.Option(
        0.0,
        "--idle-timeout",
        help=(
            "If > 0, keep polling for newly-OCR'd papers and wait up to this many seconds "
            "of no new work before exiting (for running alongside a concurrent 'ocr' job). "
            "Default 0 = process one batch and exit."
        ),
    ),
) -> None:
    """Claim and extract a batch of OCR'd papers."""
    from pathlib import Path

    from coastal_crawler.adapter import build_measurement_adapter
    from coastal_crawler.config import get_settings
    from coastal_crawler.worker import run_worker, run_worker_until_idle

    settings = get_settings()
    adapter = build_measurement_adapter(settings)
    size = chunk_size if chunk_size is not None else settings.extraction_chunk_size
    ocr_dir = Path(settings.ocr_dir)
    if idle_timeout > 0:
        extracted, failed, requeued = run_worker_until_idle(
            batch_size=batch_size,
            adapter=adapter,
            chunk_size=size,
            ocr_dir=ocr_dir,
            poll_interval=poll_interval,
            idle_timeout=idle_timeout,
        )
    else:
        extracted, failed, requeued = run_worker(
            batch_size=batch_size,
            adapter=adapter,
            chunk_size=size,
            ocr_dir=ocr_dir,
        )
    message = f"Extracted {extracted}, failed {failed}."
    if requeued:
        message += f" Requeued {requeued} (OCR text not written yet — is 'coastal-crawler ocr' running?)."
    typer.echo(message)


@app.command()
def judge(
    batch_size: int = typer.Option(
        None, "--batch-size", help="Extraction rows to process per run. Defaults to JUDGE_BATCH_SIZE in .env."
    ),
    poll_interval: float = typer.Option(
        60.0, "--poll-interval", help="Seconds to wait between polls when --idle-timeout > 0."
    ),
    idle_timeout: float = typer.Option(
        0.0,
        "--idle-timeout",
        help=(
            "If > 0, keep polling for newly-pending extraction rows and wait up to this many "
            "seconds of no new work before exiting (for running alongside a concurrent "
            "'extract' job). Default 0 = process one batch and exit."
        ),
    ),
) -> None:
    """Claim and judge a batch of extractions: JudgementLM p_true + probe validity score + attribution."""
    from coastal_crawler.adapter import build_judge
    from coastal_crawler.config import get_settings
    from coastal_crawler.judge_worker import run_judge_worker, run_judge_worker_until_idle

    settings = get_settings()
    components = build_judge(settings)
    size = batch_size if batch_size is not None else settings.judge_batch_size
    if idle_timeout > 0:
        judged, failed, requeued = run_judge_worker_until_idle(
            batch_size=size,
            components=components,
            poll_interval=poll_interval,
            idle_timeout=idle_timeout,
        )
    else:
        judged, failed, requeued = run_judge_worker(batch_size=size, components=components)
    message = f"Judged {judged}, failed {failed}."
    if requeued:
        message += f" Requeued {requeued} (paper OCR context missing)."
    typer.echo(message)


@app.command()
def show(
    paper_ids: Optional[list[int]] = typer.Argument(default=None, help="Paper IDs to inspect. Omit to list by filter."),
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (e.g. relevant, irrelevant, inaccessible, extracted)."),
    inaccessible: Optional[bool] = typer.Option(None, "--inaccessible/--accessible", help="Filter by PDF accessibility (shorthand for --status inaccessible / --status != inaccessible)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max papers to show when listing by filter (default: 20)."),
) -> None:
    """Inspect papers by ID, status, and/or PDF accessibility.

    Examples:

      coastal-crawler show 42 107              # look up specific papers\n
      coastal-crawler show --status irrelevant\n
      coastal-crawler show --status inaccessible\n
      coastal-crawler show --inaccessible\n
      coastal-crawler show --accessible --status relevant\n
      coastal-crawler show --inaccessible -n 50
    """
    from coastal_crawler.db.engine import get_session
    from coastal_crawler.db.models import Paper
    from sqlalchemy import func, select

    if not paper_ids and status_filter is None and inaccessible is None:
        raise typer.BadParameter("Provide at least one paper ID or a --status/--inaccessible filter.")

    with get_session() as session:
        stmt = select(Paper)
        if paper_ids:
            stmt = stmt.where(Paper.id.in_(paper_ids))
        if status_filter is not None:
            stmt = stmt.where(Paper.status == status_filter)
        if inaccessible is True:
            stmt = stmt.where(Paper.status == "inaccessible")
        elif inaccessible is False:
            stmt = stmt.where(Paper.status != "inaccessible")

        if not paper_ids:
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = session.scalar(count_stmt) or 0
            stmt = stmt.order_by(Paper.discovered_at.desc()).limit(limit)

        papers = session.scalars(stmt).all()

        if paper_ids:
            found = {p.id: p for p in papers}
            ordered = [found.get(pid) for pid in paper_ids]
        else:
            showing = len(papers)
            if total > showing:
                typer.echo(f"Showing {showing} of {total:,} papers (use --limit to see more)\n")
            ordered = list(papers)

        for p in ordered:
            if p is None:
                typer.echo("not found\n")
                continue

            confidence = f"{p.filter_confidence:.3f}" if p.filter_confidence is not None else "n/a"
            doi_str = f"doi:{p.doi}" if p.doi else (f"oalex:{p.openalex_id}" if p.openalex_id else "no-id")

            typer.echo(f"[{p.id}] {(p.title or 'untitled')[:80]}")
            typer.echo(f"  {doi_str}")
            typer.echo(f"  status:      {p.status}  (confidence: {confidence})")
            typer.echo(f"  url:         {p.oa_pdf_url or 'none'}")
            typer.echo("")


@app.command(name="show-extractions")
def show_extractions(
    paper_ids: Optional[list[int]] = typer.Argument(default=None, help="Paper IDs to inspect. Omit to list the most recently extracted papers."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max papers to show when listing (default: 20)."),
) -> None:
    """Show extracted papers together with their measurement data.

    Examples:

      coastal-crawler show-extractions               # most recently extracted papers\n
      coastal-crawler show-extractions 42 107         # look up specific papers\n
      coastal-crawler show-extractions -n 50
    """
    from coastal_crawler.db import store
    from coastal_crawler.db.engine import get_session

    with get_session() as session:
        papers = store.papers_with_extractions(session, paper_ids=paper_ids, limit=limit)

        if not papers:
            typer.echo("No extracted papers found.")
            return

        for p in papers:
            if p is None:
                typer.echo("not found\n")
                continue

            doi_str = f"doi:{p.doi}" if p.doi else (f"oalex:{p.openalex_id}" if p.openalex_id else "no-id")
            typer.echo(f"[{p.id}] {(p.title or 'untitled')[:80]}")
            typer.echo(f"  {doi_str}  status:{p.status}")

            if not p.extractions:
                typer.echo("  (no extraction rows)\n")
                continue

            typer.echo(f"  {len(p.extractions)} measurement(s):")
            for e in sorted(p.extractions, key=lambda e: e.id):
                data = e.data or {}
                attribute = data.get("attribute", "?")
                value = data.get("value", "?")
                units = data.get("units") or ""
                entity = data.get("name") or data.get("identifiers") or "?"
                event_date = data.get("date")
                context = " / ".join(str(b) for b in (entity, event_date) if b)
                confidence = f"  (confidence: {e.confidence:.3f})" if e.confidence is not None else ""
                typer.echo(f"    - {attribute}: {value} {units}   [{context}]  model={e.model_version}{confidence}")
            typer.echo("")


@app.command()
def status(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of recent papers to show."),
) -> None:
    """Show paper counts by status and a sample of recently discovered papers."""
    from coastal_crawler.db import store, get_session

    with get_session() as session:
        counts = store.count_by_status(session)
        papers = store.recent_papers(limit, session)

        total = sum(counts.values())
        typer.echo(f"\nTotal papers: {total}")
        for s in (
            "discovered",
            "filtering",
            "relevant",
            "irrelevant",
            "inaccessible",
            "ocr_processing",
            "ocr_done",
            "ocr_failed",
            "processing",
            "extracted",
            "failed",
        ):
            n = counts.get(s, 0)
            if n or s in ("discovered", "relevant", "extracted"):
                typer.echo(f"  {s:<12} {n}")

        if not papers:
            typer.echo("\nNo papers yet.")
            return

        typer.echo(f"\nMost recent {len(papers)} paper(s):\n")
        for p in papers:
            doi_str = f"doi:{p.doi}" if p.doi else (f"oalex:{p.openalex_id}" if p.openalex_id else "no-id")
            title = (p.title or "untitled")[:72]
            abstract_snippet = ""
            if p.abstract:
                abstract_snippet = "  " + p.abstract[:120].replace("\n", " ") + ("…" if len(p.abstract) > 120 else "")
            typer.echo(f"  [{p.status}] {title}")
            typer.echo(f"          {doi_str}")
            if abstract_snippet:
                typer.echo(abstract_snippet)
            typer.echo("")


@app.command()
def requeue_failed() -> None:
    """Reset failed papers back to 'ocr_done' so extraction is retried (skips re-filter and re-OCR)."""
    from coastal_crawler.worker import requeue_failed as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} failed paper(s) for extraction retry.")


@app.command()
def requeue_processing() -> None:
    """Reset papers stuck in 'processing' back to 'ocr_done' (use after a killed extraction job)."""
    from coastal_crawler.worker import requeue_processing as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} stranded paper(s) back to 'ocr_done'.")


@app.command()
def requeue_ocr_processing() -> None:
    """Reset papers stuck in 'ocr_processing' back to 'relevant' (use after a killed OCR job)."""
    from coastal_crawler.ocr_worker import requeue_ocr_processing as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} stranded paper(s) back to 'relevant'.")


@app.command()
def requeue_judge_processing() -> None:
    """Reset extractions stuck in judge_status='judging' back to 'pending' (use after a killed judge job)."""
    from coastal_crawler.judge_worker import requeue_judge_processing as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} stranded extraction(s) back to 'pending'.")


@app.command()
def requeue_ocr_failed() -> None:
    """Reset ocr_failed papers back to 'relevant' so OCR is retried."""
    from coastal_crawler.ocr_worker import requeue_ocr_failed as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} ocr_failed paper(s) for OCR retry.")


@app.command()
def requeue_ocr() -> None:
    """Reset every paper touched by OCR or extraction back to 'relevant' (forces a full re-OCR)."""
    from coastal_crawler.ocr_worker import requeue_ocr as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} paper(s) back to 'relevant' for a full re-OCR.")


@app.command()
def requeue_filtering() -> None:
    """Reset papers stuck in 'filtering' back to 'discovered' (use after a killed job)."""
    from coastal_crawler.db import store
    from coastal_crawler.db.engine import get_session

    with get_session() as session:
        count = store.requeue_filtering(session)
    typer.echo(f"Requeued {count} stranded paper(s) back to 'discovered'.")


@app.command()
def requeue_filtered() -> None:
    """Reset all previously filtered papers (relevant + irrelevant) back to 'discovered' for re-filtering."""
    from coastal_crawler.db import store
    from coastal_crawler.db.engine import get_session

    with get_session() as session:
        count = store.requeue_filtered(session)
    typer.echo(f"Requeued {count} paper(s) for re-filtering.")


@app.command()
def requeue_inaccessible() -> None:
    """Reset inaccessible papers back to 'discovered' for re-filtering.

    Legacy command: the filter no longer performs a PDF-accessibility check
    (see run_filter), so it won't produce new 'inaccessible' rows. PDF
    accessibility is now discovered at extraction time (status='failed').
    This remains useful for clearing out rows that predate that change.
    """
    from coastal_crawler.db import store
    from coastal_crawler.db.engine import get_session

    with get_session() as session:
        count = store.requeue_inaccessible(session)
    typer.echo(f"Requeued {count} inaccessible paper(s) for re-filtering.")


@app.command()
def requeue_irrelevant() -> None:
    """Reset irrelevant papers back to 'discovered' so they can be re-filtered."""
    from coastal_crawler.db import store
    from coastal_crawler.db.engine import get_session

    with get_session() as session:
        count = store.requeue_irrelevant(session)
    typer.echo(f"Requeued {count} irrelevant paper(s) for re-filtering.")


@app.command()
def reset_extractions(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Delete all extraction results (plus attributions and votes) and rewind
    extraction-stage papers to 'ocr_done'.

    Restores the DB to its post-OCR, pre-extraction state: every row in
    'extractions' — and every 'attributions'/'votes' row that references it —
    is deleted, and any paper with status 'extracted', 'processing', or
    'failed' is reset to 'ocr_done' so extraction can be re-run from scratch
    without re-OCRing. OCR text files on disk are left in place.
    Filtering/OCR results ('relevant'/'irrelevant'/'ocr_done' papers not yet
    re-touched by extraction) are left as-is.

    Site-visitor 'votes' are keyed to specific extraction rows and cannot
    survive a full re-extraction (new rows get new ids), so they are deleted
    too.
    """
    from coastal_crawler.db import store
    from coastal_crawler.db.engine import get_session

    if not yes:
        typer.confirm(
            "This will permanently delete ALL extraction results, all "
            "attribution rows, and all site-visitor votes, and reset "
            "extracted/processing/failed papers to 'ocr_done'. Continue?",
            abort=True,
        )

    with get_session() as session:
        attributions, votes, deleted, reset = store.reset_extractions(session)
    typer.echo(
        f"Deleted {deleted} extraction row(s), {attributions} attribution row(s), "
        f"{votes} vote(s); reset {reset} paper(s) back to 'ocr_done'."
    )


if __name__ == "__main__":
    app()
