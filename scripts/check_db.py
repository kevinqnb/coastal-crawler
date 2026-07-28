#!/usr/bin/env python3
"""Fail fast if DATABASE_URL isn't reachable.

Shared preflight for the SGE job scripts (submit_ocr_job.sh,
submit_extract_job.sh) — without it, a stale/unreachable DATABASE_URL still
burns several minutes starting a vLLM server before the coastal-crawler CLI
makes its first query and fails. Mirrors wait_for_health.sh's role as a
small, reusable helper shared by both job scripts.

Usage:
    python3 scripts/check_db.py
"""

from __future__ import annotations

import os
import sys

import psycopg2

try:
    psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10).close()
except Exception as exc:
    print(f"ERROR: cannot connect to DATABASE_URL ({exc})", file=sys.stderr)
    sys.exit(1)
