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

from coastal_crawler.adapter import ReprResult, SkipReason, StubReprAdapter
from coastal_crawler.db import store
from coastal_crawler.db.models import Paper, PaperRepresentation
from coastal_crawler.repr_worker import (
    build_index,
    requeue_repr_failed,
    requeue_repr_processing,
    run_isolated_terms,
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


def _seed(
    run_id: str,
    model: str,
    text_dir: Path,
    max_papers: int | None,
    adapter: Any = None,
    skip_overflow: bool = False,
    skip_clean_errors: bool = False,
) -> int:
    return seed_repr(
        run_id, model, TERMS, str(text_dir), ["table_clean_done"], max_papers,
        adapter=adapter or StubReprAdapter(),
        skip_overflow=skip_overflow,
        skip_clean_errors=skip_clean_errors,
        skip_manifest=text_dir.parent / f"{run_id}_skipped.json",
    )


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
        n = _seed("run1", "stub-model", text_dir, None)
        assert n == 3
        # Idempotent re-seed inserts nothing.
        assert _seed("run1", "stub-model", text_dir, None) == 0

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
        assert _seed("r1", "m", text_dir, None) == 3
        assert _seed("r2", "m", text_dir, None) == 3

    def test_max_papers_seeds_lowest_ids_only(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        assert _seed("run1", "m", text_dir, 2) == 2
        with Session(repr_db) as s:
            seeded = sorted(r.paper_id for r in s.scalars(select(PaperRepresentation)).all())
        assert seeded == sorted(ids.values())[:2]

    def test_seed_refuses_when_text_file_missing(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        (text_dir / f"{ids['a']}.txt").unlink()
        with pytest.raises(RuntimeError, match="refusing to seed"):
            _seed("run1", "m", text_dir, None)

    def test_missing_text_file_at_run_time_marks_failed(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _make_corpus(repr_db, tmp_path)
        _seed("run1", "m", text_dir, None)
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
        _seed("run1", "m", text_dir, None)
        with pytest.raises(AssertionError, match="unfinished"):
            build_index("run1", str(tmp_path / "repr"))


class _BadAdapter:
    hidden_size = 4
    layers = [0, 32]

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def skip_reason(self, doc_id: str, text: str, terms: list[str]) -> SkipReason | None:
        return None

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
            token_first_indices=np.asarray([1], dtype=np.int64),
            n_pooled_tokens=np.asarray([1], dtype=np.int64),
            paragraph_indices=np.asarray([0], dtype=np.int64),
            window_starts=np.asarray([0], dtype=np.int64),
            window_ends=np.asarray([20], dtype=np.int64),
            n_truncated=n_trunc,
            hidden_size=4,
            cleaned_sha256="0" * 64,
            reference_heading_found=None,
            kept_fraction_after_references=None,
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
        _seed("run1", "m", text_dir, None)
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
        _seed("run1", "m", text_dir, None)
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
    assert mod.STAGE_METRICS["repr"][:3] == ("repr_done", "failed", "zero_occurrence")
    assert mod.STAGE_METRICS["repr"][3:] == ("skipped_overflow", "skipped_clean_error")
    assert "repr_terms" in mod.STAGE_METRICS and mod.STAGE_SEED_ENV["repr_terms"] == "REPR_LM_SEED"
    assert mod.STAGE_SEED_ENV["repr"] == "REPR_LM_SEED"


# ---------------------------------------------------------------------------
# Skipping, cleaned-text matching, windows, isolated terms
# ---------------------------------------------------------------------------

SKIP_DOCS = {
    # ok: 2 mentions, in separate paragraphs
    "ok": "The estuary is wide.\n\nFiller paragraph.\n\nA mangrove grows.",
    # long window: radius 0 -> the 12-word paragraph holding the match is over max_tokens=8
    "long": "Filler.\n\nthe estuary was very wide and deep and long and cold and so on\n\nFiller.",
    # unclosed table -> clean_ocr_text asserts
    "badtable": "Intro text. <table><tr><td>estuary</td></tr> and more text about the estuary.",
    # term appears ONLY inside a table: zero occurrences once cleaned
    "tableonly": "Intro text.\n\n<table><tr><td>mangrove</td></tr></table>\n\nOutro text.",
}


def _skip_corpus(engine: Engine, tmp_path: Path) -> tuple[dict[str, int], Path]:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    with Session(engine) as s:
        store.upsert_papers([_paper() for _ in SKIP_DOCS], s)
        s.commit()
        papers = list(s.scalars(select(Paper).order_by(Paper.id)).all())
    ids = {}
    for (key, text), paper in zip(SKIP_DOCS.items(), papers):
        ids[key] = paper.id
        (text_dir / f"{paper.id}.txt").write_text(text, encoding="utf-8")
    return ids, text_dir


class TestSkipping:
    def _adapter(self) -> StubReprAdapter:
        return StubReprAdapter(context_radius=0, max_tokens=8)

    def test_skip_reasons_by_kind(self) -> None:
        a = self._adapter()
        assert a.skip_reason("ok", SKIP_DOCS["ok"], TERMS) is None
        long = a.skip_reason("long", SKIP_DOCS["long"], TERMS)
        assert long is not None and long.kind == "overflow"
        bad = a.skip_reason("badtable", SKIP_DOCS["badtable"], TERMS)
        assert bad is not None and bad.kind == "clean_error"
        assert a.skip_reason("tableonly", SKIP_DOCS["tableonly"], TERMS) is None

    def test_seed_excludes_flagged_kinds_and_writes_manifest(self, repr_db: Engine, tmp_path: Path) -> None:
        import json

        ids, text_dir = _skip_corpus(repr_db, tmp_path)
        n = _seed("run1", "m", text_dir, None, self._adapter(), skip_overflow=True, skip_clean_errors=True)
        assert n == 2  # ok + tableonly
        with Session(repr_db) as s:
            seeded = sorted(r.paper_id for r in s.scalars(select(PaperRepresentation)).all())
        assert seeded == sorted([ids["ok"], ids["tableonly"]])
        m = json.loads((text_dir.parent / "run1_skipped.json").read_text())
        assert m["n_eligible"] == 2 and m["n_skipped"] == {"overflow": 1, "clean_error": 1}
        assert m["skipped"][str(ids["long"])]["kind"] == "overflow"
        assert m["skipped"][str(ids["badtable"])]["kind"] == "clean_error"

    @pytest.mark.parametrize("flags", [(False, True), (True, False)])
    def test_unflagged_kind_aborts_seed(self, repr_db: Engine, tmp_path: Path, flags: tuple[bool, bool]) -> None:
        _, text_dir = _skip_corpus(repr_db, tmp_path)
        with pytest.raises(RuntimeError, match="refusing to seed"):
            _seed("run1", "m", text_dir, None, self._adapter(), skip_overflow=flags[0], skip_clean_errors=flags[1])
        with Session(repr_db) as s:
            assert s.scalars(select(PaperRepresentation)).all() == []

    def test_max_papers_counts_eligible_papers(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _skip_corpus(repr_db, tmp_path)
        # order is ok, long, badtable, tableonly; the cap of 2 must skip past the ineligible ones.
        n = _seed("run1", "m", text_dir, 2, self._adapter(), skip_overflow=True, skip_clean_errors=True)
        assert n == 2

    def test_full_run_with_windows_and_table_only_paper(self, repr_db: Engine, tmp_path: Path) -> None:
        ids, text_dir = _skip_corpus(repr_db, tmp_path)
        adapter = StubReprAdapter(context_radius=0, max_tokens=8)
        _seed("run1", "m", text_dir, None, adapter, skip_overflow=True, skip_clean_errors=True)
        done, failed, zero = run_repr_worker(
            "run1", adapter, TERMS, str(text_dir), str(tmp_path / "repr"), "float32", 10
        )
        # tableonly: the term sits in a table that cleaning removes -> zero occurrences, not a crash.
        assert (done, failed, zero) == (2, 0, 1)
        with Session(repr_db) as s:
            row = s.scalars(select(PaperRepresentation).where(PaperRepresentation.paper_id == ids["ok"])).one()
        with np.load(row.shard_path) as z:
            assert z["labels"].tolist() == ["estuary", "mangrove"]
            # radius 0: each window is exactly its own paragraph (hand-checked offsets).
            text = SKIP_DOCS["ok"]
            assert z["window_starts"].tolist() == [0, text.index("A mangrove")]
            assert z["window_ends"].tolist() == [len("The estuary is wide."), len(text)]
            assert z["paragraph_indices"].tolist() == [0, 2]
            assert str(z["cleaned_sha256"]) == __import__("hashlib").sha256(text.encode()).hexdigest()
            assert int(z["reference_heading_found"]) == -1  # references not removed in this stub

        total = build_index("run1", str(tmp_path / "repr"))
        assert total == 2
        import duckdb

        cols = {r[0] for r in duckdb.sql(f"DESCRIBE SELECT * FROM '{tmp_path / 'repr' / 'run1' / 'index.parquet'}'").fetchall()}
        assert {"window_start", "paragraph_index", "n_pooled_tokens", "cleaned_sha256", "reference_heading_found"} <= cols


class TestIsolatedTerms:
    def test_one_row_per_term_in_input_order(self, tmp_path: Path) -> None:
        out = tmp_path / "iso" / "isolated_terms.npz"
        n = run_isolated_terms(StubReprAdapter(), TERMS, out, "float32")
        assert n == 3
        with np.load(out) as z:
            assert z["terms"].tolist() == TERMS
            assert z["layers"].tolist() == [0, 32]
            assert z["rep_layer_32"].shape == (3, 4)
            # stub value = char_start/1000 + layer and every isolated term starts at 0 -> exactly the layer
            np.testing.assert_array_equal(z["rep_layer_32"], np.full((3, 4), 32.0, dtype=np.float32))
            assert z["n_pooled_tokens"].tolist() == [1, 1, 1]
