"""Sample extraction stage — run an extraction adapter over an explicit paper
list and write the results to FILES, never to the database.

Purpose: time and inspect an alternative extraction mode (e.g. scholarlm's
step-by-step "pipeline" mode) on a sample of papers that were already
extracted in production, without touching ``extractions``, ``papers.status``,
``paper_ocr_context``, or the judge queue. The only DB access is read-only
(paper statuses, stored OCR context for an input-drift check).

Output, under ``output_dir / run_id`` (created fresh; an existing directory is
a hard error so a rerun can never overwrite earlier output):

* ``manifest.json``   — run inputs: paper ids, per-paper text sha256/length,
                         chunk_size, adapter class, model_version.
* ``records.jsonl``   — one line per paper: ``paper_id``, ``chunk_index``,
                         ``error`` (str or null), ``records`` (ExtractionResult
                         dicts). Appended and fsynced per chunk, so a killed
                         job keeps every finished chunk.
* ``timing.jsonl``    — one line per chunk: wall seconds, ``step_seconds``
                         (pipeline mode), ``exhausted_calls`` per step,
                         truncated paper ids.
* ``summary.json``    — totals, written last (its absence means the run did
                         not finish).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
import structlog

from coastal_crawler.adapter import DIRECT_EXTRACTION_FORMAT_NAME, IDENTIFICATION_FORMAT_NAME, DocumentOutcome
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session
from coastal_crawler.worker import _sanitize_nul_bytes

log = structlog.get_logger(__name__)


class SampleAdapter(Protocol):
    """MeasurementAdapter plus the per-batch diagnostics this stage persists.
    Both DirectMeasurementAdapter and PipelineMeasurementAdapter satisfy it."""

    truncated_docs: set[int]
    exhausted_calls: dict[str, set[int]]
    step_seconds: dict[str, float]

    def extract_batch(self, ocr_texts: list[str]) -> list[DocumentOutcome]: ...


def read_paper_ids(path: Path) -> list[int]:
    """Read ``paper_id`` from a configs/lists/*.csv file (header row required)."""
    import csv

    with path.open(newline="") as f:
        reader = csv.DictReader(line for line in f if not line.startswith("#"))
        assert reader.fieldnames is not None and "paper_id" in reader.fieldnames, (
            f"{path}: needs a 'paper_id' column, got {reader.fieldnames}"
        )
        return [int(row["paper_id"]) for row in reader]


def _json_default(obj: object) -> object:
    """json.dumps hook: pipeline-mode records carry numpy scalars (scholarlm's
    table-value step, cf. its own NumpyEncoder), which json cannot encode.
    Integer/float scalars convert losslessly; anything else still raises."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def _append_jsonl(path: Path, obj: object) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=_json_default) + "\n")
        f.flush()
        os.fsync(f.fileno())


def run_extract_sample(
    *,
    run_id: str,
    paper_ids: list[int],
    text_dir: Path,
    output_dir: Path,
    adapter: SampleAdapter,
    chunk_size: int,
    now_fn: Callable[[], float] = time.monotonic,
) -> tuple[int, int, int, int]:
    """Extract ``paper_ids`` chunk by chunk and write results under
    ``output_dir / run_id``.

    Every id must be a paper with ``status='extracted'`` and its
    ``text_dir/{id}.txt`` must equal (after NUL sanitization) the OCR context
    stored when production extracted it — i.e. the sample is run on the exact
    text the production run saw. Any mismatch raises before a single model
    call.

    Returns:
        (papers, records, failed, followup_calls_exhausted). ``failed`` counts
        papers whose outcome was an error string; ``followup_calls_exhausted``
        counts exhausted-retry calls in steps that cannot be attributed to a
        paper (see PipelineMeasurementAdapter).
    """
    assert paper_ids, "paper_ids is empty"
    assert len(set(paper_ids)) == len(paper_ids), "paper_ids contains duplicates"
    assert chunk_size > 0, f"chunk_size must be > 0, got {chunk_size}"

    texts: dict[int, str] = {}
    with get_session() as session:
        statuses = store.get_paper_statuses(session, paper_ids)
        assert set(statuses) == set(paper_ids), f"unknown paper ids: {sorted(set(paper_ids) - set(statuses))}"
        not_extracted = {pid: st for pid, st in statuses.items() if st != "extracted"}
        assert not not_extracted, f"sample papers must have status 'extracted': {not_extracted}"
        drifted: list[int] = []
        for pid in paper_ids:
            text = (text_dir / f"{pid}.txt").read_text(encoding="utf-8")  # missing file -> raises
            stored = store.get_paper_ocr_context(session, pid)
            if stored != _sanitize_nul_bytes(text)[0]:
                drifted.append(pid)
            texts[pid] = text
        assert not drifted, (
            f"{len(drifted)} paper(s) whose {text_dir} text differs from the OCR context stored by the "
            f"production extraction (or have none): {drifted[:20]}"
        )

    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True)  # FileExistsError if this run id was already used
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "adapter": type(adapter).__name__,
                "chunk_size": chunk_size,
                "paper_ids": paper_ids,
                "text": {
                    str(pid): {"sha256": hashlib.sha256(t.encode("utf-8")).hexdigest(), "chars": len(t)}
                    for pid, t in texts.items()
                },
            },
            indent=2,
        )
        + "\n"
    )

    n_records = n_failed = n_followup_exhausted = 0
    total_seconds = 0.0
    for chunk_index, start in enumerate(range(0, len(paper_ids), chunk_size)):
        ids = paper_ids[start : start + chunk_size]
        log.info("sample_chunk_started", chunk_index=chunk_index, chunk_size=len(ids))
        t0 = now_fn()
        outcomes = adapter.extract_batch([texts[pid] for pid in ids])
        seconds = round(now_fn() - t0, 2)
        total_seconds += seconds
        assert len(outcomes) == len(ids), f"adapter returned {len(outcomes)} outcomes for {len(ids)} papers"

        for pid, outcome in zip(ids, outcomes):
            if isinstance(outcome, str):
                n_failed += 1
                log.warning("sample_paper_failed", paper_id=pid, error=outcome)
                _append_jsonl(run_dir / "records.jsonl", {"paper_id": pid, "chunk_index": chunk_index, "error": outcome, "records": []})
            else:
                n_records += len(outcome)
                _append_jsonl(
                    run_dir / "records.jsonl",
                    {
                        "paper_id": pid,
                        "chunk_index": chunk_index,
                        "error": None,
                        "records": [r.model_dump(mode="python") for r in outcome],
                    },
                )
        followup = {
            step: sorted(idx)
            for step, idx in adapter.exhausted_calls.items()
        }
        # The gating call's failures are already per-paper errors above; every
        # other step's exhausted calls are unattributable, so count them here.
        gating = {IDENTIFICATION_FORMAT_NAME, DIRECT_EXTRACTION_FORMAT_NAME}
        n_followup_exhausted += sum(len(v) for step, v in followup.items() if step not in gating)
        _append_jsonl(
            run_dir / "timing.jsonl",
            {
                "chunk_index": chunk_index,
                "paper_ids": ids,
                "seconds": seconds,
                "step_seconds": adapter.step_seconds,
                "exhausted_calls": followup,
                "truncated_paper_ids": [ids[i] for i in sorted(adapter.truncated_docs)],
            },
        )
        log.info("sample_chunk_done", chunk_index=chunk_index, seconds=seconds)

    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "papers": len(paper_ids),
                "records": n_records,
                "failed": n_failed,
                "followup_calls_exhausted": n_followup_exhausted,
                "chunk_seconds_total": round(total_seconds, 2),
                "papers_per_hour": round(len(paper_ids) / total_seconds * 3600, 1) if total_seconds > 0 else None,
            },
            indent=2,
        )
        + "\n"
    )
    return len(paper_ids), n_records, n_failed, n_followup_exhausted
