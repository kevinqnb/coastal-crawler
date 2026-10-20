"""Tests for the representation stage (repr_worker.py).

Uses StubReprAdapter (regex matching via scholarlm's own
``find_key_term_occurrences``, tiny hand-checkable embeddings) — no GPU or
model weights. DB tests use the real test Postgres via ``clean_db``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import ReprResult, StubReprAdapter
from coastal_crawler.db import store
from coastal_crawler.db.models import Paper, PaperRepresentation
from coastal_crawler.repr_worker import (
    build_index,
    requeue_repr_failed,
    requeue_repr_processing,
    run_repr_worker,
    seed_repr,
    terms_hash,
)

TERMS = ["tidal flat", "estuary", "mangrove"]

# Hand-built documents with known occurrence counts per term:
#   p1: "Tidal flats" (plural, capitalised) + "tidal flat" + "Estuary" -> 2 tidal flat, 1 estuary
#   p2: no terms at all ("mangroves" -> 1 mangrove actually; see below)
#   p3: zero term mentions
DOCS = {
    "a": "Tidal flats border the estuary. Another tidal flat is nearby. The Estuary is wide.",
    "b": "Dense mangroves line the shore; pondweed is not a term, nor is estuarine.",
    "c": "Nothing relevant here at all.",
}
# a: tidal flat x2, estuary x2 (case-insensitive); b: mangrove x1 (plural);
# "estuarine" must NOT match (whole-word + simple plural only); c: 0.
EXPECTED = {"a": 4, "b": 1, "c": 0}


def _uid() -> str:
    return str(uuid.uuid4())[:8]


def _paper(status: str = "table_clean_done") -> dict[str, Any]:
    uid = _uid()
    return {
        "doi": f"10.1/{uid}",
        "openalex_id": f"W{uid}",
        "semantic_scholar_id": None,
        "title": f"T {uid}",
        "metadata": {},
        "status": status,
    }


@pytest.fixture
def repr_db(clean_db: Engine, mocker: Any) -> Engine:
    @contextmanager  # type: ignore[misc]
    def _test_get_session():
        session = Session(clean_db)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    mocker.patch("coastal_crawler.repr_worker.get_session", _test_get_session)
    return clean_db


def _make_corpus(engine: Engine, tmp_path: Path) -> tuple[dict[str, int], Path]:
    """Insert 3 papers and write their text files; returns {doc key: paper_id}."""
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    ids: dict[str, int] = {}
    with Session(engine) as s:
        store.upsert_papers([_paper() for _ in DOCS], s)
        s.commit()
        papers = list(s.scalars(select(Paper).order_by(Paper.id)).all())
    for (key, text), paper in zip(DOCS.items(), papers):
        ids[key] = paper.id
        (text_dir / f"{paper.id}.txt").write_text(text, encoding="utf-8")
    return ids, text_dir


class TestStubAdapter:
    @pytest.mark.parametrize("key,n", list(EXPECTED.items()))
    def test_known_occurrence_counts(self, key: str, n: int) -> None:
        res = StubReprAdapter().collect(key, DOCS[key], TERMS)
        assert sorted(res.representations) == [0, 32]
        assert all(r.shape == (n, 4) for r in res.representations.values())
        assert len(res.labels) == n

    def test_spans_point_at_the_matched_text(self) -> None:
        res = StubReprAdapter().collect("a", DOCS["a"], TERMS)
        surfaces = [DOCS["a"][s:e].lower() for s, e in zip(res.char_starts, res.char_ends)]
        assert surfaces == ["tidal flats", "estuary", "tidal flat", "estuary"]


class TestSeedAndRun:
    def test_end_to_end_counts_match_known_answers(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        repr_dir = tmp_path / "repr"
        n = seed_repr("run1", "stub-model", TERMS, str(text_dir), ["table_clean_done"], None)
        assert n == 3
        # Idempotent re-seed inserts nothing.
        assert seed_repr("run1", "stub-model", TERMS, str(text_dir), ["table_clean_done"], None) == 0

        done, failed, zero = run_repr_worker(
            "run1", StubReprAdapter(), TERMS, str(text_dir), str(repr_dir), "float32", batch_size=2
        )
        assert (done, failed, zero) == (3, 0, 1)

        with Session(repr_db) as s:
            rows = {r.paper_id: r for r in s.scalars(select(PaperRepresentation)).all()}
            statuses = {p.id: p.status for p in s.scalars(select(Paper)).all()}
        for key, pid in ids.items():
            assert rows[pid].status == "done"
            assert rows[pid].n_occurrences == EXPECTED[key]
            with np.load(rows[pid].shard_path) as z:
                assert z["layers"].tolist() == [0, 32]
                for k in ("rep_layer_00", "rep_layer_32"):
                    assert z[k].dtype == np.float32
                    assert z[k].shape == (EXPECTED[key], 4)
                # stub value = char_start/1000 + layer, so layer 32 - layer 0 == 32 exactly
                np.testing.assert_allclose(z["rep_layer_32"] - z["rep_layer_00"], 32.0, rtol=1e-5)
        # papers.status is never touched by this stage.
        assert set(statuses.values()) == {"table_clean_done"}

        total = build_index("run1", str(repr_dir))
        assert total == sum(EXPECTED.values())

    def test_two_runs_do_not_collide(self, repr_db: Engine, tmp_path: Path) -> None:
        _, text_dir = _make_corpus(repr_db, tmp_path)
        assert seed_repr("r1", "m", TERMS, str(text_dir), ["table_clean_done"], None) == 3
        assert seed_repr("r2", "m", TERMS, str(text_dir), ["table_clean_done"], None) == 3

    def test_max_papers_seeds_lowest_ids_only(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        assert seed_repr("run1", "m", TERMS, str(text_dir), ["table_clean_done"], 2) == 2
        with Session(repr_db) as s:
            seeded = sorted(r.paper_id for r in s.scalars(select(PaperRepresentation)).all())
        assert seeded == sorted(ids.values())[:2]

    def test_seed_refuses_when_text_file_missing(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        (text_dir / f"{ids['a']}.txt").unlink()
        with pytest.raises(RuntimeError, match="refusing to seed"):
            seed_repr("run1", "m", TERMS, str(text_dir), ["table_clean_done"], None)

    def test_missing_text_file_at_run_time_marks_failed(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        seed_repr("run1", "m", TERMS, str(text_dir), ["table_clean_done"], None)
        (text_dir / f"{ids['b']}.txt").unlink()
        done, failed, _ = run_repr_worker(
            "run1", StubReprAdapter(), TERMS, str(text_dir), str(tmp_path / "repr"), "float32", 10
        )
        assert (done, failed) == (2, 1)
        with Session(repr_db) as s:
            row = s.scalars(select(PaperRepresentation).where(PaperRepresentation.paper_id == ids["b"])).one()
        assert row.status == "failed" and "text file missing" in (row.error or "")

    def test_build_index_refuses_unfinished_run(self, repr_db: Engine, tmp_path: Path) -> None:
        _, text_dir = _make_corpus(repr_db, tmp_path)
        seed_repr("run1", "m", TERMS, str(text_dir), ["table_clean_done"], None)
        with pytest.raises(AssertionError, match="unfinished"):
            build_index("run1", str(tmp_path / "repr"))


class _BadAdapter:
    hidden_size = 4
    layers = [0, 32]

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def collect(self, doc_id: str, text: str, terms: list[str]) -> ReprResult:
        reps = {0: np.ones((1, 4), dtype=np.float32), 32: np.ones((1, 4), dtype=np.float32)}
        n_trunc = 0
        if self.kind == "nan":
            reps[32][0, 0] = np.nan
        elif self.kind == "overflow":
            reps[0][0, 0] = 1e6  # finite in fp32, inf in fp16
        elif self.kind == "wrongdim":
            reps[32] = np.ones((1, 3), dtype=np.float32)
        elif self.kind == "missinglayer":
            del reps[0]
        elif self.kind == "truncated":
            n_trunc = 1
        return ReprResult(
            representations=reps,
            labels=np.asarray(["estuary"], dtype="U"),
            char_starts=np.asarray([0], dtype=np.int64),
            char_ends=np.asarray([7], dtype=np.int64),
            token_indices=np.asarray([1], dtype=np.int64),
            n_truncated=n_trunc,
            hidden_size=4,
        )


class TestValidationFailures:
    @pytest.mark.parametrize(
        "kind,fragment",
        [
            ("nan", "non-finite values in layer 32"),
            ("overflow", "after cast"),
            ("wrongdim", "shape"),
            ("missinglayer", "configured"),
            ("truncated", "tail-truncated"),
        ],
    )
    def test_marks_failed_never_writes_shard(
        self, repr_db: Engine, tmp_path: Path, kind: str, fragment: str
    ) -> None:
        # "overflow" casts to float16 to trigger the post-cast finiteness check.
        _, text_dir = _make_corpus(repr_db, tmp_path)
        seed_repr("run1", "m", TERMS, str(text_dir), ["table_clean_done"], None)
        repr_dir = tmp_path / "repr"
        done, failed, _ = run_repr_worker(
            "run1", _BadAdapter(kind), TERMS, str(text_dir), str(repr_dir), "float16", 10
        )
        assert (done, failed) == (0, 3)
        assert list(repr_dir.rglob("*.npz")) == []
        with Session(repr_db) as s:
            errors = [r.error or "" for r in s.scalars(select(PaperRepresentation)).all()]
        assert all(fragment in e for e in errors)


class TestRequeue:
    def test_requeue_failed_and_processing(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        seed_repr("run1", "m", TERMS, str(text_dir), ["table_clean_done"], None)
        with Session(repr_db) as s:
            claimed = store.claim_batch_for_repr("run1", 2, s)
            store.mark_repr_failed(claimed[0].id, "boom", s)
            s.commit()
        assert requeue_repr_processing("run1") == 1
        assert requeue_repr_failed("run1") == 1
        with Session(repr_db) as s:
            assert store.repr_status_counts("run1", s) == {"pending": 3}


def test_terms_hash_is_order_independent() -> None:
    assert terms_hash(["a", "b"]) == terms_hash(["b", "a"])
    assert terms_hash(["a", "b"]) != terms_hash(["a", "c"])


def test_parse_layers() -> None:
    from coastal_crawler.adapter import parse_layers

    assert parse_layers("32,0") == [0, 32]
    for bad in ("", "0,0", "-1", "a"):
        with pytest.raises(ValueError):
            parse_layers(bad)


def test_run_experiment_knows_repr_stage() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_experiment", Path(__file__).resolve().parent.parent / "scripts" / "run_experiment.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.STAGE_METRICS["repr"] == ("repr_done", "failed", "zero_occurrence")
    assert mod.STAGE_SEED_ENV["repr"] == "REPR_LM_SEED"
