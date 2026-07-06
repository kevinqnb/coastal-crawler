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
def extract(
    batch_size: int = typer.Option(10, "--batch-size", help="Papers to process per run."),
) -> None:
    """Claim and extract a batch of relevant papers."""
    from coastal_crawler.adapter import build_scholarlm_adapter
    from coastal_crawler.config import get_settings
    from coastal_crawler.worker import run_worker

    adapter = build_scholarlm_adapter(get_settings())
    extracted, failed = run_worker(batch_size=batch_size, adapter=adapter)
    typer.echo(f"Extracted {extracted}, failed {failed}.")


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
        for s in ("discovered", "filtering", "relevant", "irrelevant", "inaccessible", "processing", "extracted", "failed"):
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
    """Reset failed papers back to 'relevant' so extraction is retried (skips re-filter)."""
    from coastal_crawler.worker import requeue_failed as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} failed paper(s) for extraction retry.")


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


if __name__ == "__main__":
    app()
