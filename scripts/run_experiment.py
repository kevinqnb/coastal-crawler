#!/usr/bin/env python3
"""Adapter for the shared experiment contract (see notes/hub/conventions.md).

Translates a configs/<id>.yaml into a call to whichever pipeline stage
entry point this repo already has — run_filter() / run_ocr_worker() /
run_worker() / run_judge_worker() — and writes the standardized run
directory. It does not reimplement any pipeline logic; it only wires
config -> existing function call -> standardized output.

Usage:
    python scripts/run_experiment.py configs/<id>.yaml

Config `params` (free-form per the contract, but this adapter reads):
    stage:         "filter" | "ocr" | "extract" | "judge"   (required)
    batch_size:    int, passed straight through to the stage function.
                   Defaults to that stage's existing default (Settings'
                   FILTER_BATCH_SIZE for filter; 10 for ocr/extract;
                   JUDGE_BATCH_SIZE for judge — same as each CLI command's
                   --batch-size default).
    chunk_size:    int, passed straight through. Defaults to the stage's
                   OCR_CHUNK_SIZE / EXTRACTION_CHUNK_SIZE setting. Not used
                   by judge (one extraction row at a time — see
                   judge_worker.py).
    poll_interval: float, extract and judge only. Default 60.0 (CLI default).
    idle_timeout:  float, extract and judge only. Default 0.0 = single batch,
                   no polling loop (CLI default).
    env:           mapping of environment variables to set for this run
                   before Settings is constructed — e.g. FILTER_MODEL,
                   FILTER_RELEVANCE_PROMPT, DOC_LM_MODEL, MEAS_LM_MODEL,
                   MEAS_LM_ENTITY_IDENTIFICATION_PROMPT, JUDGE_MODEL. This
                   is how a config reaches values that normally live in the
                   shared .env, without touching it. The config's top-level
                   `seed` is applied to the stage's own <ROLE>_SEED
                   (FILTER_SEED / DOC_LM_SEED / MEAS_LM_SEED / JUDGE_SEED)
                   unless `env` already sets it.

Metrics shim: run_filter/run_ocr_worker/run_worker/run_judge_worker each
already return a 3-tuple of counts (e.g. relevant/irrelevant/errors). That
tuple is written to metrics.json verbatim under the names in STAGE_METRICS
below — no retry/quality metric exists anywhere in this repo today
(confirmed by reading relevance_filter.py/ocr_worker.py/worker.py/
judge_worker.py), so metrics.json reports batch throughput/outcome counts,
not an accuracy/quality score.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# stage -> (metric names for the 3-tuple each run_* function returns,
#           <ROLE>_SEED env var to receive the config's top-level `seed`)
STAGE_METRICS: dict[str, tuple[str, str, str]] = {
    "filter": ("relevant", "irrelevant", "errors"),
    "ocr": ("ocr_done", "failed", "requeued"),
    "extract": ("extracted", "failed", "requeued"),
    "judge": ("judged", "failed", "requeued"),
}
STAGE_SEED_ENV: dict[str, str] = {
    "filter": "FILTER_SEED",
    "ocr": "DOC_LM_SEED",
    "extract": "MEAS_LM_SEED",
    "judge": "JUDGE_SEED",
}


class _Tee:
    """Duplicates writes to multiple streams (console + log.txt)."""

    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_stage(stage: str, params: dict[str, Any]) -> tuple[int, int, int]:
    """Call this stage's existing entry point. No pipeline logic lives here."""
    from coastal_crawler.config import get_settings

    settings = get_settings()

    if stage == "filter":
        from coastal_crawler.relevance_filter import run_filter

        batch_size = params.get("batch_size", settings.filter_batch_size)
        return run_filter(batch_size=batch_size)

    if stage == "ocr":
        from coastal_crawler.adapter import build_ocr_adapter
        from coastal_crawler.ocr_worker import run_ocr_worker

        adapter = build_ocr_adapter(settings)
        batch_size = params.get("batch_size", 10)
        chunk_size = params.get("chunk_size", settings.ocr_chunk_size)
        return run_ocr_worker(
            batch_size=batch_size,
            adapter=adapter,
            chunk_size=chunk_size,
            wiley_pdf_dir=Path(settings.wiley_pdf_dir),
            ocr_dir=Path(settings.ocr_dir),
        )

    if stage == "extract":
        from coastal_crawler.adapter import build_measurement_adapter
        from coastal_crawler.worker import run_worker, run_worker_until_idle

        adapter = build_measurement_adapter(settings)
        batch_size = params.get("batch_size", 10)
        chunk_size = params.get("chunk_size", settings.extraction_chunk_size)
        ocr_dir = Path(settings.ocr_dir)
        idle_timeout = params.get("idle_timeout", 0.0)
        if idle_timeout > 0:
            return run_worker_until_idle(
                batch_size=batch_size,
                adapter=adapter,
                chunk_size=chunk_size,
                ocr_dir=ocr_dir,
                poll_interval=params.get("poll_interval", 60.0),
                idle_timeout=idle_timeout,
            )
        return run_worker(batch_size=batch_size, adapter=adapter, chunk_size=chunk_size, ocr_dir=ocr_dir)

    if stage == "judge":
        from coastal_crawler.adapter import build_judge
        from coastal_crawler.judge_worker import run_judge_worker, run_judge_worker_until_idle

        components = build_judge(settings)
        batch_size = params.get("batch_size", settings.judge_batch_size)
        idle_timeout = params.get("idle_timeout", 0.0)
        if idle_timeout > 0:
            return run_judge_worker_until_idle(
                batch_size=batch_size,
                components=components,
                poll_interval=params.get("poll_interval", 60.0),
                idle_timeout=idle_timeout,
            )
        return run_judge_worker(batch_size=batch_size, components=components)

    raise ValueError(f"params.stage must be one of {sorted(STAGE_METRICS)}, got {stage!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_path", type=Path)
    args = parser.parse_args()

    config_path = args.config_path.resolve()
    config = yaml.safe_load(config_path.read_text())

    run_id = config["id"]
    project = config["project"]
    params = config.get("params", {})

    stage = params.get("stage")
    if stage not in STAGE_METRICS:
        raise SystemExit(f"params.stage must be one of {sorted(STAGE_METRICS)}, got {stage!r}")

    runs_root = os.environ.get("RUNS_ROOT")
    if not runs_root:
        raise SystemExit("RUNS_ROOT must be set in the environment (see notes/hub/SETUP.md).")

    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "figures").mkdir(exist_ok=True)
    (run_dir / "artifacts").mkdir(exist_ok=True)

    # Verbatim copy of the config as submitted.
    (run_dir / "config.snapshot.yaml").write_text(config_path.read_text())

    # params.env seeds this run's environment before Settings is built, so
    # a config can override FILTER_MODEL/DOC_LM_MODEL/etc. without touching
    # the shared .env. The config's top-level `seed` fills the stage's own
    # <ROLE>_SEED unless params.env already set it explicitly.
    for key, value in params.get("env", {}).items():
        os.environ[key] = str(value)
    seed_env = STAGE_SEED_ENV[stage]
    os.environ.setdefault(seed_env, str(config.get("seed", 0)))

    started_at = _now_iso()
    t0 = time.monotonic()
    status = "success"
    metrics: dict[str, int] = {}

    with open(run_dir / "log.txt", "w") as log_file:
        tee_out, tee_err = _Tee(sys.stdout, log_file), _Tee(sys.stderr, log_file)
        try:
            with redirect_stdout(tee_out), redirect_stderr(tee_err):
                result = _run_stage(stage, params)
                metrics = dict(zip(STAGE_METRICS[stage], result))
        except Exception:
            status = "failed"
            traceback.print_exc(file=tee_err)

    finished_at = _now_iso()

    try:
        config_path_field = str(config_path.relative_to(REPO_ROOT))
    except ValueError:
        config_path_field = str(config_path)

    run_json = {
        "id": run_id,
        "project": project,
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "host": socket.gethostname(),
        "job_id": os.environ.get("JOB_ID", "local"),
        "config_path": config_path_field,
    }
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n")
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    print(f"[{stage}] {run_id}: {status} ({round(time.monotonic() - t0, 1)}s) -> {run_dir}")

    if status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
