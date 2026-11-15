#!/usr/bin/env python3
"""Draw a seeded random sample of paper ids into a configs/lists/*.csv file.

Read-only on the database. The sample frame is every paper currently in
--status (default: extracted), sorted by id, then sampled WITHOUT replacement
with random.Random(--seed) — so the same frame + seed + n always yields the
same list. The seed, n, frame size and date are recorded in '#' header lines
of the output (read_paper_ids() skips them). Refuses to overwrite.

Usage:
    source scripts/db_env.sh   # or run inside a job with DATABASE_URL set
    python scripts/make_sample_list.py --seed 342 --n 100 \
        --out configs/lists/2026-11-02-measlm-pipeline-sample-01.csv
"""

from __future__ import annotations

import argparse
import random
from datetime import date
from pathlib import Path

from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument("--status", required=True, help="papers.status that defines the sample frame")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    assert not args.out.exists(), f"{args.out} already exists; refusing to overwrite"
    with get_session() as session:
        frame = sorted(store.list_paper_ids_with_status([args.status], session))
    assert len(frame) >= args.n, f"frame has {len(frame)} papers, fewer than n={args.n}"
    sample = sorted(random.Random(args.seed).sample(frame, args.n))
    assert len(set(sample)) == args.n

    args.out.write_text(
        f"# seed={args.seed} n={args.n} status={args.status} frame_size={len(frame)} drawn={date.today().isoformat()}\n"
        "paper_id\n" + "".join(f"{pid}\n" for pid in sample)
    )
    print(f"wrote {args.n} of {len(frame)} '{args.status}' papers to {args.out}")


if __name__ == "__main__":
    main()
