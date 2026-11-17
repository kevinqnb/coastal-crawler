#!/usr/bin/env python3
"""Build compilations.duckdb from per-compilation extract.py outputs.

Reads each ``<compilation>/extracted/*.parquet`` (written by that
compilation's extract.py), validates the contract in
``coastal_crawler.compilations``, assigns global surrogate keys in sorted
natural-key order, and atomically writes a fresh DuckDB file (full rebuild).
Fails loud on any contract violation; there is no skip path here.

Usage:
    uv run scripts/build_compilations.py --out data/compilations/compilations.duckdb \
        data/compilations/smithsonian-coastal-carbon/extracted [more extracted dirs...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coastal_crawler.compilations import build_compilations


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--allow-factless", action="append", default=[], metavar="SOURCE_KEY",
        help="source_key allowed to have no facts (repeatable); raises if it has any",
    )
    ap.add_argument("extracted_dirs", type=Path, nargs="+")
    args = ap.parse_args()
    counts = build_compilations(args.extracted_dirs, args.out, frozenset(args.allow_factless))
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
