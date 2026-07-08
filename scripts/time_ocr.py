"""Time OCR on a sample of relevant papers.

Downloads PDFs from the DB and times OCRLM.fit() one paper at a time.
Does NOT write to the DB — relevant papers are pre-checked for accessibility
during the filter stage.

Usage:
    uv run python scripts/time_ocr.py
    uv run python scripts/time_ocr.py --n 50 --api-base http://localhost:8081/v1
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time

from olmocr.prompts import build_no_anchoring_v4_yaml_prompt as olmocr_prompt
from sqlalchemy import func, select

from coastal_crawler.config import get_settings
from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Paper
from coastal_crawler.extraction import OCRLM
from coastal_crawler.pdf import download_pdf


def _fetch_paper_urls(n: int) -> list[tuple[int, str, str | None]]:
    with get_session() as session:
        rows = session.execute(
            select(Paper.id, Paper.oa_pdf_url, Paper.discovered_from)
            .where(Paper.status == "relevant")
            .where(Paper.oa_pdf_url.isnot(None))
            .order_by(func.random())
            .limit(n)
        ).all()
    return [(row.id, row.oa_pdf_url, row.discovered_from) for row in rows]




def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=100, help="Number of papers to sample (default: 100)")
    p.add_argument("--api-base", default="http://localhost:8081/v1", help="vLLM server URL")
    p.add_argument("--api-key", default="EMPTY")
    p.add_argument("--model-id", default="allenai/olmOCR-2-7B-1025")
    args = p.parse_args(argv)

    _ = get_settings()  # validates .env is loadable

    print(f"Fetching up to {args.n} relevant papers from DB...")
    papers = _fetch_paper_urls(args.n)
    print(f"Found {len(papers)} papers with PDF URLs.\n")

    if not papers:
        print("No papers to process. Are papers filtered and marked relevant?")
        sys.exit(1)

    doclm = OCRLM(
        model_name=args.model_id,
        ocr_prompt=olmocr_prompt(),
        api_base=args.api_base,
        api_key=args.api_key,
    )

    timings: list[float] = []
    skipped = 0

    for i, (paper_id, url, discovered_from) in enumerate(papers, 1):
        print(f"[{i}/{len(papers)}] paper_id={paper_id}", end=" ", flush=True)
        try:
            pdf_path = download_pdf(url, discovered_from)
        except Exception as exc:
            print(f"SKIP (download failed: {exc})")
            skipped += 1
            continue

        try:
            t0 = time.perf_counter()
            doclm.fit([str(pdf_path)])
            elapsed = time.perf_counter() - t0
            timings.append(elapsed)
            print(f"{elapsed:.1f}s")
        except Exception as exc:
            print(f"SKIP (OCR failed: {exc})")
            skipped += 1
        finally:
            pdf_path.unlink(missing_ok=True)

    print(f"\n--- Results ({len(timings)} papers, {skipped} skipped) ---")
    if not timings:
        print("No successful runs.")
        return

    timings.sort()
    print(f"  mean   {statistics.mean(timings):.1f}s")
    print(f"  median {statistics.median(timings):.1f}s")
    print(f"  min    {timings[0]:.1f}s")
    print(f"  p95    {timings[int(len(timings) * 0.95)]:.1f}s")
    print(f"  max    {timings[-1]:.1f}s")
    print(f"  total  {sum(timings):.0f}s ({sum(timings)/60:.1f} min)")


if __name__ == "__main__":
    main()
