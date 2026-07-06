"""Tests for the relevance filter batch runner (relevance_filter.py).

All tests use the ``filter_db`` fixture, which patches ``get_session``
inside the relevance_filter module to use the test engine, and mocks the
LLM boundary (``AbstractFilter.classify``) plus settings so no real
OpenAI-compatible server is required.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.db import store
from coastal_crawler.db.models import Paper
from coastal_crawler.relevance_filter import run_filter


def _uid() -> str:
    return str(uuid.uuid4())[:8]


def make_paper(
    *, abstract: str | None = "an abstract about coastal wetlands", **kwargs: Any
) -> dict[str, Any]:
    uid = _uid()
    return {
        "doi": f"10.1/{uid}",
        "openalex_id": f"W{uid}",
        "semantic_scholar_id": None,
        "title": f"Test Paper {uid}",
        "abstract": abstract,
        "oa_pdf_url": kwargs.pop("oa_pdf_url", None),
        "metadata": {},
        "status": "discovered",
        **kwargs,
    }


def _insert(engine: Engine, *paper_dicts: dict[str, Any]) -> None:
    with Session(engine) as s:
        store.upsert_papers(list(paper_dicts), s)
        s.commit()


_FAKE_SETTINGS = SimpleNamespace(
    filter_model="test-model",
    filter_relevance_prompt="Is this paper about coastal ecosystems?",
    filter_base_url="http://localhost:1234/v1",
    filter_api_key="unused",
    filter_seed=0,
    filter_temperature=0.0,
    filter_top_logprobs=20,
    filter_batch_size=50,
)


@pytest.fixture
def filter_db(clean_db: Engine, mocker: Any) -> Engine:
    """Patch relevance_filter.get_session to use the test engine, and mock
    out the LLM client construction / settings lookup / OpenAI import so
    run_filter() never touches a real network endpoint."""

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

    mocker.patch("coastal_crawler.relevance_filter.get_session", _test_get_session)
    mocker.patch("coastal_crawler.config.get_settings", return_value=_FAKE_SETTINGS)
    mocker.patch("coastal_crawler.relevance_filter.OpenAI")
    return clean_db


class TestRunFilter:
    def test_no_abstract_marks_irrelevant_without_llm_call(
        self, filter_db: Engine, mocker: Any
    ) -> None:
        _insert(filter_db, make_paper(abstract=None))
        classify = mocker.patch(
            "coastal_crawler.relevance_filter.AbstractFilter.classify"
        )

        relevant, irrelevant, errors = run_filter(batch_size=10)

        assert (relevant, irrelevant, errors) == (0, 1, 0)
        classify.assert_not_called()
        with Session(filter_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "irrelevant"
        assert paper.filter_confidence is None

    def test_llm_relevant_marks_relevant(self, filter_db: Engine, mocker: Any) -> None:
        _insert(filter_db, make_paper())
        mocker.patch(
            "coastal_crawler.relevance_filter.AbstractFilter.classify",
            return_value=(True, 0.87),
        )

        relevant, irrelevant, errors = run_filter(batch_size=10)

        assert (relevant, irrelevant, errors) == (1, 0, 0)
        with Session(filter_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "relevant"
        assert paper.filter_confidence == pytest.approx(0.87)

    def test_llm_irrelevant_marks_irrelevant(self, filter_db: Engine, mocker: Any) -> None:
        _insert(filter_db, make_paper())
        mocker.patch(
            "coastal_crawler.relevance_filter.AbstractFilter.classify",
            return_value=(False, 0.12),
        )

        relevant, irrelevant, errors = run_filter(batch_size=10)

        assert (relevant, irrelevant, errors) == (0, 1, 0)
        with Session(filter_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "irrelevant"

    def test_llm_api_error_resets_to_discovered(self, filter_db: Engine, mocker: Any) -> None:
        _insert(filter_db, make_paper())
        mocker.patch(
            "coastal_crawler.relevance_filter.AbstractFilter.classify",
            side_effect=RuntimeError("API unreachable"),
        )

        relevant, irrelevant, errors = run_filter(batch_size=10)

        assert (relevant, irrelevant, errors) == (0, 0, 1)
        with Session(filter_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "discovered"

    def test_pdf_accessibility_never_checked_during_filter(
        self, filter_db: Engine, mocker: Any
    ) -> None:
        """Regression guard: filtering must not touch the network at all,
        even for a paper with no oa_pdf_url."""
        _insert(filter_db, make_paper(oa_pdf_url=None))
        get_mock = mocker.patch("coastal_crawler.pdf.httpx.get")
        mocker.patch(
            "coastal_crawler.relevance_filter.AbstractFilter.classify",
            return_value=(True, 0.9),
        )

        run_filter(batch_size=10)

        get_mock.assert_not_called()
