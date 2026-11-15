#!/usr/bin/env python3
"""Timing + descriptive comparison report for an extract_sample run.

Reads <run-dir>/{manifest,summary}.json, timing.jsonl and records.jsonl (see
src/coastal_crawler/extract_sample.py) and, read-only, the production
`extractions` rows for the same papers, and prints a markdown report.

This is DESCRIPTIVE. There is no gold standard here: "overlap" means two modes
produced the same (attribute, value, units) triple for a paper, not that either
is correct. No accuracy claim should be drawn from it.

Timing caveat: papers inside a chunk run concurrently against vLLM, so a
per-paper time is not observable and per-chunk seconds are not additive over
papers. The estimate is therefore throughput (papers/hour over whole chunks),
with the min/max over chunks as a crude range; with few chunks there is no
meaningful confidence interval and none is reported.

Usage:
    source scripts/db_env.sh
    python scripts/sample_report.py --run-dir data/measlm_pipeline_sample/<id> \
        --db-model-version 'doc_lm=...+meas_lm=openai/gpt-oss-120b' --corpus-papers 2769
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select

from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Extraction


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _key(attribute: Any, value: Any, units: Any) -> tuple[str, str, str]:
    try:
        v = f"{float(value):.6g}"
    except (TypeError, ValueError):
        v = str(value).strip().lower()
    return (str(attribute).strip().lower(), v, str(units).strip().lower())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--db-model-version", required=True, help="extractions.model_version of the production rows to compare to")
    ap.add_argument("--corpus-papers", type=int, required=True, help="paper count to extrapolate timing to")
    args = ap.parse_args()

    manifest = json.loads((args.run_dir / "manifest.json").read_text())
    summary = json.loads((args.run_dir / "summary.json").read_text())  # absent => run did not finish
    timing = _jsonl(args.run_dir / "timing.jsonl")
    records = _jsonl(args.run_dir / "records.jsonl")
    ids: list[int] = manifest["paper_ids"]
    assert [r["paper_id"] for r in records] == ids, "records.jsonl does not cover manifest paper_ids in order"
    assert sum(len(t["paper_ids"]) for t in timing) == len(ids)

    # ---- timing
    chunk_tp = [len(t["paper_ids"]) / t["seconds"] * 3600 for t in timing if t["seconds"] > 0]
    total_s = sum(t["seconds"] for t in timing)
    tp = len(ids) / total_s * 3600
    step_tot: Counter[str] = Counter()
    for t in timing:
        step_tot.update(t["step_seconds"])
    print(f"# Sample report: {manifest['run_id']}\n")
    print(f"adapter={manifest['adapter']} chunk_size={manifest['chunk_size']} papers={len(ids)} chunks={len(timing)}\n")
    print("## Timing (model-call time only; excludes server start / tokenizer load)")
    print(f"- total chunk seconds: {total_s:.0f} ({total_s / 3600:.2f} h)")
    print(f"- throughput: {tp:.1f} papers/hour (per-chunk min {min(chunk_tp):.1f}, median {statistics.median(chunk_tp):.1f}, max {max(chunk_tp):.1f})")
    print(f"- extrapolated to {args.corpus_papers} papers: {args.corpus_papers / tp:.1f} h (range {args.corpus_papers / max(chunk_tp):.1f}-{args.corpus_papers / min(chunk_tp):.1f} h from chunk extremes)")
    if step_tot:
        denom = sum(step_tot.values())
        print("- step share of recorded step time: " + ", ".join(f"{k}={v:.0f}s ({100 * v / denom:.0f}%)" for k, v in step_tot.items()))
    print(f"- papers failed: {summary['failed']}; unattributable exhausted follow-up calls: {summary['followup_calls_exhausted']}")
    print(f"- truncated papers: {sorted(i for t in timing for i in t['truncated_paper_ids'])}\n")

    # ---- comparison with production rows
    with get_session() as session:
        rows = session.execute(
            select(Extraction.paper_id, Extraction.data).where(
                Extraction.paper_id.in_(ids), Extraction.model_version == args.db_model_version
            )
        ).all()
    db: dict[int, Counter[tuple[str, str, str]]] = defaultdict(Counter)
    for pid, data in rows:
        db[pid][_key(data.get("attribute"), data.get("value"), data.get("units"))] += 1
    run: dict[int, Counter[tuple[str, str, str]]] = {}
    errors = {r["paper_id"]: r["error"] for r in records if r["error"]}
    for r in records:
        run[r["paper_id"]] = Counter(_key(x["data"].get("attribute"), x["data"].get("value"), x["data"].get("units")) for x in r["records"])

    n_db, n_run = sum(sum(c.values()) for c in db.values()), sum(sum(c.values()) for c in run.values())
    matched = sum(sum((db[p] & run[p]).values()) for p in ids)
    zero_db = {p for p in ids if not db[p]}
    zero_run = {p for p in ids if not run[p] and p not in errors}
    print(f"## Comparison vs production rows (model_version={args.db_model_version!r})")
    print(f"- production records on these papers: {n_db}; this run: {n_run}; identical (attribute,value,units) triples: {matched}")
    print(f"- papers with zero records: production {len(zero_db)}, this run {len(zero_run)} (+{len(errors)} errored)")
    print(f"- zero in production but >0 here: {sorted(p for p in zero_db if run[p])}")
    print(f"- >0 in production but zero here: {sorted(p for p in ids if db[p] and not run[p])}")
    attrs_db = Counter(k[0] for c in db.values() for k in c.elements())
    attrs_run = Counter(k[0] for c in run.values() for k in c.elements())
    print("\n| attribute | production | this run |\n|---|---|---|")
    for a in sorted(set(attrs_db) | set(attrs_run)):
        print(f"| {a} | {attrs_db[a]} | {attrs_run[a]} |")
    print("\nPer-paper counts (paper_id: production/this run): " + ", ".join(f"{p}: {sum(db[p].values())}/{sum(run[p].values())}" for p in ids))


if __name__ == "__main__":
    main()
