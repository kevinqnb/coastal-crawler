"""Representation stage — collects last-layer term embeddings per paper.

Reads each paper's cleaned/OCR text from ``text_dir/{paper_id}.txt``, runs
``scholarlm.RepresentationLM`` (via a ``ReprAdapter``) for the configured term
list, and writes one atomic shard per paper:
``repr_dir/<run_id>/shards/{paper_id}.npz`` (arrays ``rep_layer_NN`` per collected
layer, a ``layers`` array, and the provenance arrays). Queue state lives in the
``paper_representations`` table (``papers.status`` is never touched), so this
stage runs alongside extraction without competing for its rows.

``repr-index`` (``build_index``) consolidates the shards' metadata into
``repr_dir/<run_id>/index.parquet`` and reconciles it against the DB.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any
from pathlib import Path

import duckdb
import numpy as np
import structlog

from coastal_crawler.adapter import ReprAdapter, ReprResult
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session

log = structlog.get_logger(__name__)


class ReprValidationError(Exception):
    """A per-paper result violated a pipeline-boundary invariant; recorded on the row, not skipped."""


def terms_hash(terms: list[str]) -> str:
    """Order-independent short hash of the term list, recorded on every queue row."""
    return hashlib.sha256("\n".join(sorted(terms)).encode("utf-8")).hexdigest()[:16]


def read_terms(terms_file: str) -> list[str]:
    """One term per line (blank lines ignored); duplicates are an error."""
    terms = [ln.strip() for ln in Path(terms_file).read_text().splitlines() if ln.strip()]
    if not terms:
        raise ValueError(f"no terms in {terms_file}")
    if len(set(terms)) != len(terms):
        raise ValueError(f"duplicate terms in {terms_file}")
    return terms


def shard_dir(repr_dir: str, run_id: str) -> Path:
    return Path(repr_dir) / run_id / "shards"


def _validate_and_cast(
    result: ReprResult, terms: list[str], hidden_size: int, layers: list[int], store_dtype: str
) -> dict[int, np.ndarray]:
    """Boundary checks on one paper's result; returns per-layer reps cast to *store_dtype*."""
    if sorted(result.representations) != sorted(layers):
        raise ReprValidationError(f"layers {sorted(result.representations)} != configured {sorted(layers)}")
    n = len(result.labels)
    for name in ("char_starts", "char_ends", "token_indices"):
        if len(getattr(result, name)) != n:
            raise ReprValidationError(f"{name} length {len(getattr(result, name))} != n {n}")
    if result.n_truncated != 0:
        raise ReprValidationError(
            f"document was tail-truncated by RepresentationLM (n_truncated={result.n_truncated}); "
            "occurrences past the cutoff were dropped"
        )
    unknown = set(result.labels.tolist()) - set(terms)
    if unknown:
        raise ReprValidationError(f"labels not in term list: {sorted(unknown)}")
    cast: dict[int, np.ndarray] = {}
    for L in sorted(layers):
        reps = np.asarray(result.representations[L])
        if reps.shape != (n, hidden_size):
            raise ReprValidationError(f"layer {L} representations shape {reps.shape}, expected ({n}, {hidden_size})")
        if not np.isfinite(reps).all():
            raise ReprValidationError(f"non-finite values in layer {L} representations before cast")
        cast[L] = reps.astype(np.dtype(store_dtype))
        if not np.isfinite(cast[L]).all():
            raise ReprValidationError(f"non-finite values in layer {L} after cast to {store_dtype} (overflow)")
    return cast


def _write_shard_atomic(path: Path, result: ReprResult, reps: dict[int, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    arrays: dict[str, Any] = {
        "layers": np.asarray(sorted(reps), dtype=np.int64),
        "labels": np.asarray(result.labels, dtype="U"),
        "char_starts": np.asarray(result.char_starts, dtype=np.int64),
        "char_ends": np.asarray(result.char_ends, dtype=np.int64),
        "token_indices": np.asarray(result.token_indices, dtype=np.int64),
    }
    arrays.update({f"rep_layer_{L:02d}": reps[L] for L in sorted(reps)})
    with open(tmp, "wb") as f:
        np.savez(f, **arrays)
    os.replace(tmp, path)


def seed_repr(
    run_id: str,
    model_name: str,
    terms: list[str],
    text_dir: str,
    source_statuses: list[str],
    max_papers: int | None,
) -> int:
    """Create ``pending`` queue rows for every paper in *source_statuses* with a non-empty text file.

    Papers in those statuses WITHOUT a usable text file are an error, not a
    skip: the seed aborts listing them. Returns rows inserted; asserts the
    run's total row count equals the number of qualifying papers.
    """
    with get_session() as session:
        ids = store.list_paper_ids_with_status(source_statuses, session)
        if max_papers is not None:
            ids = ids[:max_papers]  # lowest ids first: deterministic subset for smoke/tiny rungs
        missing = [
            i for i in ids
            if not (Path(text_dir) / f"{i}.txt").is_file()
            or (Path(text_dir) / f"{i}.txt").stat().st_size == 0
        ]
        if missing:
            raise RuntimeError(
                f"{len(missing)} paper(s) in statuses {source_statuses} have no non-empty "
                f"{text_dir}/<id>.txt (first few: {missing[:5]}); refusing to seed."
            )
        inserted = store.seed_repr_queue(run_id, model_name, terms_hash(terms), ids, session)
        total = sum(store.repr_status_counts(run_id, session).values())
        assert total == len(ids), f"queue has {total} rows for {run_id}, expected {len(ids)}"
    log.info("repr_seeded", run_id=run_id, inserted=inserted, total=len(ids))
    return inserted


def run_repr_worker(
    run_id: str,
    adapter: ReprAdapter,
    terms: list[str],
    text_dir: str,
    repr_dir: str,
    store_dtype: str,
    batch_size: int,
) -> tuple[int, int, int]:
    """Process ``pending`` rows of *run_id* until none remain.

    Returns ``(repr_done, failed, zero_occurrence)`` — the last counts papers
    that were processed successfully but mention none of the terms.
    """
    done = failed = zero = 0
    out_dir = shard_dir(repr_dir, run_id)
    while True:
        with get_session() as session:
            claimed = [(r.id, r.paper_id) for r in store.claim_batch_for_repr(run_id, batch_size, session)]
        if not claimed:
            break
        for row_id, paper_id in claimed:
            text_path = Path(text_dir) / f"{paper_id}.txt"
            try:
                if not text_path.is_file():
                    raise ReprValidationError(f"text file missing: {text_path}")
                text = text_path.read_text(encoding="utf-8")
                if not text.strip():
                    raise ReprValidationError(f"text file empty: {text_path}")
                result = adapter.collect(str(paper_id), text, terms)
                reps = _validate_and_cast(result, terms, adapter.hidden_size, adapter.layers, store_dtype)
                shard = out_dir / f"{paper_id}.npz"
                _write_shard_atomic(shard, result, reps)
            except (ReprValidationError, AssertionError) as e:
                log.error("repr_failed", paper_id=paper_id, error=str(e))
                with get_session() as session:
                    store.mark_repr_failed(row_id, f"{type(e).__name__}: {e}", session)
                failed += 1
                continue
            with get_session() as session:
                store.mark_repr_done(row_id, len(result.labels), str(shard), session)
            done += 1
            zero += int(len(result.labels) == 0)
    log.info("repr_worker_finished", run_id=run_id, done=done, failed=failed, zero_occurrence=zero)
    return done, failed, zero


def requeue_repr_processing(run_id: str) -> int:
    with get_session() as session:
        return store.requeue_repr(run_id, "processing", session)


def requeue_repr_failed(run_id: str) -> int:
    with get_session() as session:
        return store.requeue_repr(run_id, "failed", session)


def build_index(run_id: str, repr_dir: str) -> int:
    """Write ``repr_dir/<run_id>/index.parquet`` from the shards of every ``done`` row.

    Columns: occurrence_id, paper_id, term, char_start, char_end, token_index,
    shard, row. Asserts the run has no unfinished rows, each shard's length
    equals its DB ``n_occurrences``, and the index row count equals the sum.
    Returns the number of occurrences indexed.
    """
    from sqlalchemy import select

    from coastal_crawler.db.models import PaperRepresentation

    with get_session() as session:
        counts = store.repr_status_counts(run_id, session)
        rows = session.execute(
            select(
                PaperRepresentation.paper_id,
                PaperRepresentation.shard_path,
                PaperRepresentation.n_occurrences,
            )
            .where(PaperRepresentation.run_id == run_id, PaperRepresentation.status == "done")
            .order_by(PaperRepresentation.paper_id)
        ).all()
    not_done = {s: n for s, n in counts.items() if s != "done"}
    assert not not_done, f"run {run_id} has unfinished rows {not_done}; refusing to index"
    assert rows, f"run {run_id} has no done rows"

    cols: dict[str, list[Any]] = {k: [] for k in ("paper_id", "term", "char_start", "char_end", "token_index", "shard", "row")}
    for paper_id, shard_path, n_occ in rows:
        with np.load(shard_path) as z:
            n = len(z["labels"])
            assert n == n_occ, f"paper {paper_id}: shard has {n} rows, DB says {n_occ}"
            layer_keys = [f"rep_layer_{L:02d}" for L in z["layers"].tolist()]
            assert layer_keys and all(z[k].shape[0] == n for k in layer_keys), f"paper {paper_id}: layer array length != {n}"
            cols["paper_id"] += [paper_id] * n
            cols["term"] += z["labels"].tolist()
            cols["char_start"] += z["char_starts"].tolist()
            cols["char_end"] += z["char_ends"].tolist()
            cols["token_index"] += z["token_indices"].tolist()
            cols["shard"] += [shard_path] * n
            cols["row"] += list(range(n))
    total = len(cols["paper_id"])
    assert total == sum(n for _, _, n in rows), "index rows != sum of DB n_occurrences"

    idx = {"occurrence_id": np.arange(total, dtype=np.int64)} | {
        k: np.asarray(v, dtype=object if k in ("term", "shard") else np.int64)
        for k, v in cols.items()
    }
    out = Path(repr_dir) / run_id / "index.parquet"
    tmp = out.with_name(out.name + ".tmp")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM idx) TO '{tmp}' (FORMAT PARQUET)")
    os.replace(tmp, out)
    check = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()
    assert check is not None and check[0] == total
    log.info("repr_index_written", run_id=run_id, path=str(out), occurrences=total)
    return total
