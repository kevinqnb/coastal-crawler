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


@app.command()
def extract(
    batch_size: int = typer.Option(10, "--batch-size", help="Papers to process per run."),
) -> None:
    """Claim and extract a batch of discovered papers."""
    from coastal_crawler.worker import run_worker

    extracted, failed = run_worker(batch_size=batch_size)
    typer.echo(f"Extracted {extracted}, failed {failed}.")


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
        for s in ("discovered", "processing", "extracted", "failed"):
            n = counts.get(s, 0)
            if n or s in ("discovered", "extracted"):
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
    """Reset failed papers back to 'discovered' so they can be retried."""
    from coastal_crawler.worker import requeue_failed as _requeue

    count = _requeue()
    typer.echo(f"Requeued {count} failed paper(s).")


if __name__ == "__main__":
    app()
