"""Tests for the judge worker (judge_worker.py).

Like test_worker.py, all tests use a ``judge_worker_db`` fixture that patches
``get_session`` inside the judge_worker module to use the test engine.

There is no stub/fake JudgementLM shipped in adapter.py (see JudgeComponents'
docstring — "tests inject plain fakes with matching .generate()/.attribute()
methods instead"). FakeTokenizer below is a minimal word-level stand-in for a
HuggingFace tokenizer — the real ``scholarlm.judgementlm.tokenize()`` is
called directly by both judge_worker.py and FakeAttributionMethod (not
mocked), so the fake only needs to satisfy that function's actual tokenizer
contract (``apply_chat_template``, ``__call__(text, return_offsets_mapping=True,
add_special_tokens=False)``, ``decode``) — exercising the same
tokenize()/context_token_indices consistency check judge_worker.py performs
against a real model would.
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from scholarlm.judgementlm import tokenize
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import ExtractionResult, JudgeComponents
from coastal_crawler.db import store
from coastal_crawler.db.models import Attribution, Extraction, Paper
from coastal_crawler.judge_worker import requeue_judge_processing, run_judge_worker


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:8]


def make_paper(*, status: str = "extracted", **kwargs: Any) -> dict[str, Any]:
    uid = _uid()
    return {
        "doi": f"10.1/{uid}",
        "openalex_id": f"W{uid}",
        "semantic_scholar_id": None,
        "title": f"Test Paper {uid}",
        "oa_pdf_url": None,
        "metadata": {},
        "status": status,
        **kwargs,
    }


def make_result(**kwargs: Any) -> ExtractionResult:
    return ExtractionResult(
        schema_name=kwargs.get("schema_name", "test_schema"),
        model_version=kwargs.get("model_version", "v1"),
        data=kwargs.get("data", {"attribute": "salinity", "value": "28.4", "units": "ppt"}),
        confidence=kwargs.get("confidence"),
        provenance=kwargs.get("provenance"),
        latitude=kwargs.get("latitude"),
        longitude=kwargs.get("longitude"),
    )


@pytest.fixture
def judge_worker_db(clean_db: Engine, mocker: Any) -> Engine:
    """Patch judge_worker.get_session to use the test engine."""

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

    mocker.patch("coastal_crawler.judge_worker.get_session", _test_get_session)
    return clean_db


def _insert_paper_with_pending_extraction(
    engine: Engine, ocr_context: str | None = "some ocr text", **result_kwargs: Any
) -> tuple[int, int]:
    """Insert a paper with a committed paper_ocr_context row (unless
    ocr_context is None) and one pending extraction. Returns (paper_id,
    extraction_id)."""
    with Session(engine) as s:
        store.upsert_papers([make_paper()], s)
        paper = s.scalars(select(Paper).order_by(Paper.id.desc())).first()
        if ocr_context is not None:
            store.upsert_paper_ocr_context(paper.id, ocr_context, s)
        extraction = store.insert_extraction(paper.id, make_result(**result_kwargs), s)
        s.commit()
        return paper.id, extraction.id


def _extraction(engine: Engine, extraction_id: int) -> Extraction:
    with Session(engine) as s:
        return s.get(Extraction, extraction_id)


def _attributions(engine: Engine, extraction_id: int) -> list[Attribution]:
    with Session(engine) as s:
        return list(
            s.scalars(select(Attribution).where(Attribution.extraction_id == extraction_id))
        )


class FakeTokenizer:
    """Minimal word-level stand-in for a HuggingFace tokenizer — enough to
    satisfy scholarlm.judgementlm.tokenize()'s actual contract."""

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._next_id = 0

    def _id_for(self, word: str) -> int:
        if word not in self._vocab:
            self._vocab[word] = self._next_id
            self._next_id += 1
        return self._vocab[word]

    def apply_chat_template(
        self, chat: list[dict[str, str]], tokenize: bool = False, add_generation_prompt: bool = True
    ) -> str:
        return chat[0]["content"]

    def __call__(
        self, text: str, return_offsets_mapping: bool = True, add_special_tokens: bool = False
    ) -> dict[str, list[Any]]:
        input_ids = []
        offsets = []
        for m in re.finditer(r"\S+", text):
            input_ids.append(self._id_for(m.group()))
            offsets.append((m.start(), m.end()))
        return {"input_ids": input_ids, "offset_mapping": offsets}

    def decode(self, ids: list[int]) -> str:
        reverse = {v: k for k, v in self._vocab.items()}
        return reverse[ids[0]]


class FakeJudge:
    def __init__(self, p_true: float = 0.8) -> None:
        self.tokenizer = FakeTokenizer()
        self.use_chat_template = True
        self.answer_cue = None
        self._p_true = p_true

    def generate(self, instructions: str, context: str, query: str) -> dict[str, float]:
        return {"p_true": self._p_true, "p_false": 1 - self._p_true}


class FakeAttributionMethod:
    """Derives context_token_indices via the real tokenize() (same function
    judge_worker.py calls independently), so a correctly-behaving fake
    naturally satisfies judge_worker.py's consistency check."""

    def __init__(self, judge: FakeJudge, probe_output: float | None = None, bad_indices: bool = False) -> None:
        self.judge = judge
        self.probe_output = probe_output
        self.bad_indices = bad_indices

    def attribute(self, instructions: str, context: str, query: str) -> dict[str, Any]:
        _, _, context_token_indices, _ = tokenize(
            instructions,
            context,
            query,
            self.judge.tokenizer,
            use_chat_template=self.judge.use_chat_template,
            answer_cue=self.judge.answer_cue,
        )
        if self.bad_indices:
            context_token_indices = [i + 999 for i in context_token_indices]
        scores = [0.1 * i for i in range(len(context_token_indices))]
        result: dict[str, Any] = {"scores": scores, "context_token_indices": context_token_indices}
        if self.probe_output is not None:
            result["probe_output"] = self.probe_output
        return result


def make_components(
    p_true: float = 0.8, probe_output: float = 0.55, bad_probe_indices: bool = False
) -> JudgeComponents:
    judge = FakeJudge(p_true=p_true)
    return JudgeComponents(
        judge=judge,  # type: ignore[arg-type]
        attribution_methods={
            "contrastive_gradient": FakeAttributionMethod(judge),  # type: ignore[arg-type]
            "probe": FakeAttributionMethod(judge, probe_output=probe_output, bad_indices=bad_probe_indices),  # type: ignore[arg-type]
        },
        instructions_prompt="Judge whether the measurement is supported.",
    )


# ---------------------------------------------------------------------------
# run_judge_worker
# ---------------------------------------------------------------------------

class TestRunJudgeWorkerValidation:
    def test_no_components_raises(self, judge_worker_db: Engine) -> None:
        with pytest.raises(RuntimeError, match="components must be provided"):
            run_judge_worker(batch_size=10, components=None)


class TestRunJudgeWorkerSuccess:
    def test_empty_queue_returns_zeros(self, judge_worker_db: Engine) -> None:
        judged, failed, requeued = run_judge_worker(batch_size=10, components=make_components())
        assert (judged, failed, requeued) == (0, 0, 0)

    def test_judged_extraction_sets_status_and_scores(self, judge_worker_db: Engine) -> None:
        _, extraction_id = _insert_paper_with_pending_extraction(judge_worker_db)
        judged, failed, requeued = run_judge_worker(
            batch_size=10, components=make_components(p_true=0.73, probe_output=0.41)
        )
        assert (judged, failed, requeued) == (1, 0, 0)
        ext = _extraction(judge_worker_db, extraction_id)
        assert ext.judge_status == "judged"
        assert ext.confidence == pytest.approx(0.73)
        assert ext.probe_score == pytest.approx(0.41)

    def test_inserts_one_attribution_row_per_method(self, judge_worker_db: Engine) -> None:
        _, extraction_id = _insert_paper_with_pending_extraction(judge_worker_db)
        run_judge_worker(batch_size=10, components=make_components())
        rows = _attributions(judge_worker_db, extraction_id)
        assert {r.method for r in rows} == {"contrastive_gradient", "probe"}
        for row in rows:
            assert len(row.scores) == len(row.token_indices) == len(row.tokens)
            assert row.snippet

    def test_batch_size_respected(self, judge_worker_db: Engine) -> None:
        for _ in range(5):
            _insert_paper_with_pending_extraction(judge_worker_db)
        judged, failed, requeued = run_judge_worker(batch_size=3, components=make_components())
        assert judged == 3
        with Session(judge_worker_db) as s:
            remaining = [
                e for e in s.scalars(select(Extraction)).all() if e.judge_status == "pending"
            ]
        assert len(remaining) == 2


class TestRunJudgeWorkerMissingOcrContext:
    def test_missing_ocr_context_requeues_to_pending(self, judge_worker_db: Engine) -> None:
        _, extraction_id = _insert_paper_with_pending_extraction(judge_worker_db, ocr_context=None)
        judged, failed, requeued = run_judge_worker(batch_size=10, components=make_components())
        assert (judged, failed, requeued) == (0, 0, 1)
        ext = _extraction(judge_worker_db, extraction_id)
        assert ext.judge_status == "pending"


class TestRunJudgeWorkerFailures:
    def test_none_data_marks_judge_failed(self, judge_worker_db: Engine) -> None:
        paper_id, _ = _insert_paper_with_pending_extraction(judge_worker_db)
        with Session(judge_worker_db) as s:
            extraction = Extraction(
                paper_id=paper_id,
                schema_name="test_schema",
                model_version="v1",
                data=None,
                judge_status="pending",
            )
            s.add(extraction)
            s.commit()
            extraction_id = extraction.id

        judged, failed, requeued = run_judge_worker(batch_size=10, components=make_components())
        assert failed >= 1
        ext = _extraction(judge_worker_db, extraction_id)
        assert ext.judge_status == "judge_failed"
        assert "no data" in ext.judge_error

    def test_generate_error_marks_judge_failed(self, judge_worker_db: Engine) -> None:
        _, extraction_id = _insert_paper_with_pending_extraction(judge_worker_db)
        components = make_components()
        components.judge.generate = MagicMock(side_effect=RuntimeError("gpu oom"))  # type: ignore[method-assign]

        judged, failed, requeued = run_judge_worker(batch_size=10, components=components)
        assert (judged, failed, requeued) == (0, 1, 0)
        ext = _extraction(judge_worker_db, extraction_id)
        assert ext.judge_status == "judge_failed"
        assert "gpu oom" in ext.judge_error

    def test_attribution_index_mismatch_marks_judge_failed(self, judge_worker_db: Engine) -> None:
        """If an attribution method's context_token_indices don't match the
        worker's own independent tokenize() call, that's a fail-loud
        consistency error, not silently stored mismatched data."""
        _, extraction_id = _insert_paper_with_pending_extraction(judge_worker_db)
        components = make_components(bad_probe_indices=True)

        judged, failed, requeued = run_judge_worker(batch_size=10, components=components)
        assert (judged, failed, requeued) == (0, 1, 0)
        ext = _extraction(judge_worker_db, extraction_id)
        assert ext.judge_status == "judge_failed"
        assert "context_token_indices" in ext.judge_error
        # No attribution rows persisted on a mid-loop failure.
        assert _attributions(judge_worker_db, extraction_id) == []

    def test_does_not_affect_other_extractions(self, judge_worker_db: Engine) -> None:
        paper_id, _ = _insert_paper_with_pending_extraction(judge_worker_db)
        with Session(judge_worker_db) as s:
            bad_extraction = Extraction(
                paper_id=paper_id,
                schema_name="test_schema",
                model_version="v1",
                data=None,
                judge_status="pending",
            )
            s.add(bad_extraction)
            s.commit()

        judged, failed, requeued = run_judge_worker(batch_size=10, components=make_components())
        assert judged == 1
        assert failed == 1


# ---------------------------------------------------------------------------
# requeue_judge_processing
# ---------------------------------------------------------------------------

class TestRequeueJudgeProcessing:
    def test_delegates_to_store(self, judge_worker_db: Engine) -> None:
        paper_id, extraction_id = _insert_paper_with_pending_extraction(judge_worker_db)
        with Session(judge_worker_db) as s:
            store.claim_batch_for_judge(10, s)
            s.commit()

        count = requeue_judge_processing()
        assert count == 1
        ext = _extraction(judge_worker_db, extraction_id)
        assert ext.judge_status == "pending"

    def test_returns_zero_when_nothing_judging(self, judge_worker_db: Engine) -> None:
        assert requeue_judge_processing() == 0
