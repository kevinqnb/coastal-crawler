"""Tests for the extraction worker (worker.py).

All tests use the ``worker_db`` fixture, which patches ``get_session`` inside
the worker module to use the test engine.  This avoids needing DATABASE_URL
set in the environment while still exercising the real session/commit logic.

HTTP calls (PDF download) are mocked via pytest-mock.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import ExtractionResult, StubAdapter
from coastal_crawler.db import store
from coastal_crawler.db.models import Extraction, Paper
from coastal_crawler.worker import run_worker, requeue_failed


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:8]


def make_paper(
    *,
    oa_pdf_url: str | None = "https://example.com/paper.pdf",
    status: str = "relevant",
    **kwargs: Any,
) -> dict[str, Any]:
    uid = _uid()
    return {
        "doi": f"10.1/{uid}",
        "openalex_id": f"W{uid}",
        "semantic_scholar_id": None,
        "title": f"Test Paper {uid}",
        "oa_pdf_url": oa_pdf_url,
        "metadata": {},
        "status": status,
        **kwargs,
    }


def make_result(**kwargs: Any) -> ExtractionResult:
    return ExtractionResult(
        schema_name=kwargs.get("schema_name", "test_schema"),
        model_version=kwargs.get("model_version", "v1"),
        data=kwargs.get("data", {"value": 1.0, "units": "m"}),
        confidence=kwargs.get("confidence", 0.9),
        provenance=kwargs.get("provenance", {"page": 1}),
        latitude=kwargs.get("latitude"),
        longitude=kwargs.get("longitude"),
    )


@pytest.fixture
def worker_db(clean_db: Engine, mocker: Any) -> Engine:
    """Patch worker.get_session to use the test engine.

    This lets run_worker() operate against the test database without needing
    DATABASE_URL in the environment.
    """
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

    mocker.patch("coastal_crawler.worker.get_session", _test_get_session)
    return clean_db


@pytest.fixture
def mock_download(mocker: Any) -> MagicMock:
    """Mock httpx.get so no real HTTP requests are made during PDF download.

    Patches coastal_crawler.pdf.httpx.get, since that's where download_pdf()
    actually issues the network request — worker.py itself only imports
    download_pdf, not httpx.
    """
    mock_resp = MagicMock()
    mock_resp.content = b"%PDF-1.4 fake"
    mock_resp.raise_for_status = MagicMock()
    return mocker.patch("coastal_crawler.pdf.httpx.get", return_value=mock_resp)


def _insert(engine: Engine, *paper_dicts: dict[str, Any]) -> None:
    with Session(engine) as s:
        store.upsert_papers(list(paper_dicts), s)
        s.commit()


def _paper(engine: Engine) -> Paper:
    with Session(engine) as s:
        return s.scalars(select(Paper)).one()


def _papers(engine: Engine) -> list[Paper]:
    with Session(engine) as s:
        return list(s.scalars(select(Paper)).all())


def _extractions(engine: Engine) -> list[Extraction]:
    with Session(engine) as s:
        return list(s.scalars(select(Extraction)).all())


# ---------------------------------------------------------------------------
# run_worker — successful extraction
# ---------------------------------------------------------------------------

class TestRunWorkerSuccess:
    def test_empty_queue_returns_zeros(self, worker_db: Engine) -> None:
        extracted, failed = run_worker(batch_size=10, adapter=StubAdapter())
        assert (extracted, failed) == (0, 0)

    def test_extracted_paper_status(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        run_worker(batch_size=10, adapter=StubAdapter())
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "extracted"

    def test_extracted_timestamp_set(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        run_worker(batch_size=10, adapter=StubAdapter())
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.extracted_at is not None

    def test_extraction_results_stored(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        adapter = MagicMock()
        adapter.extract.return_value = [make_result(), make_result()]
        run_worker(batch_size=10, adapter=adapter)
        assert len(_extractions(worker_db)) == 2

    def test_extraction_result_fields(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        result = make_result(
            schema_name="coastal_v1",
            model_version="llm-3",
            data={"depth": 42.0, "units": "m"},
            latitude=51.5,
            longitude=-0.1,
        )
        adapter = MagicMock()
        adapter.extract.return_value = [result]
        run_worker(batch_size=10, adapter=adapter)
        with Session(worker_db) as s:
            ext = s.scalars(select(Extraction)).one()
        assert ext.schema_name == "coastal_v1"
        assert ext.model_version == "llm-3"
        assert ext.data["depth"] == pytest.approx(42.0)
        assert ext.latitude == pytest.approx(51.5)
        assert ext.longitude == pytest.approx(-0.1)

    def test_stub_adapter_produces_no_extractions(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        run_worker(batch_size=10, adapter=StubAdapter())
        assert _extractions(worker_db) == []
        # Paper is still marked extracted
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "extracted"

    def test_default_adapter_is_stub(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        """Calling run_worker without adapter kwarg uses StubAdapter."""
        _insert(worker_db, make_paper())
        extracted, failed = run_worker(batch_size=10)
        assert extracted == 1
        assert failed == 0

    def test_batch_size_respected(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, *[make_paper() for _ in range(5)])
        run_worker(batch_size=3, adapter=StubAdapter())
        with Session(worker_db) as s:
            extracted = s.scalar(
                select(func.count(Paper.id)).where(Paper.status == "extracted")
            )
            unclaimed = s.scalar(
                select(func.count(Paper.id)).where(Paper.status == "relevant")
            )
        assert extracted == 3
        assert unclaimed == 2

    def test_adapter_called_with_path(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        adapter = MagicMock()
        adapter.extract.return_value = []
        run_worker(batch_size=10, adapter=adapter)
        adapter.extract.assert_called_once()
        pdf_path = adapter.extract.call_args[0][0]
        assert isinstance(pdf_path, Path)

    def test_pdf_downloaded_from_oa_url(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper(oa_pdf_url="https://example.com/mypaper.pdf"))
        run_worker(batch_size=10, adapter=StubAdapter())
        mock_download.assert_called_once()
        called_url = mock_download.call_args[0][0]
        assert called_url == "https://example.com/mypaper.pdf"

    def test_temp_file_deleted_after_extraction(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        captured_paths: list[Path] = []

        class CapturingAdapter:
            def extract(self, pdf_path: Path) -> list[ExtractionResult]:
                captured_paths.append(pdf_path)
                return []

        run_worker(batch_size=10, adapter=CapturingAdapter())
        assert len(captured_paths) == 1
        assert not captured_paths[0].exists()

    def test_returns_extracted_failed_counts(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper(), make_paper())
        extracted, failed = run_worker(batch_size=10, adapter=StubAdapter())
        assert extracted == 2
        assert failed == 0


# ---------------------------------------------------------------------------
# run_worker — failure handling
# ---------------------------------------------------------------------------

class TestRunWorkerFailures:
    def test_no_pdf_url_marks_failed(self, worker_db: Engine) -> None:
        _insert(worker_db, make_paper(oa_pdf_url=None))
        extracted, failed = run_worker(batch_size=10, adapter=StubAdapter())
        assert (extracted, failed) == (0, 1)
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "failed"
        assert paper.error is not None

    def test_download_http_error_marks_failed(
        self, worker_db: Engine, mocker: Any
    ) -> None:
        _insert(worker_db, make_paper())
        mocker.patch(
            "coastal_crawler.pdf.httpx.get",
            side_effect=Exception("connection refused"),
        )
        extracted, failed = run_worker(batch_size=10, adapter=StubAdapter())
        assert (extracted, failed) == (0, 1)
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "failed"
        assert "connection refused" in paper.error

    def test_http_status_error_includes_response_body(
        self, worker_db: Engine, mocker: Any
    ) -> None:
        """A non-2xx PDF download should surface the response body in
        paper.error, not just httpx's generic 'Server error ... for url ...'
        summary — this is where Wiley's Apigee gateway hides the actual
        rate-limit diagnostic."""
        _insert(worker_db, make_paper())
        fault_body = (
            '{"fault":{"faultstring":"Rate limit quota violation. '
            'Quota limit  exceeded.","detail":'
            '{"errorcode":"policies.ratelimit.QuotaViolation"}}}'
        )
        request = httpx.Request("GET", "https://api.wiley.com/onlinelibrary/tdm/v1/some-doi")
        response = httpx.Response(500, content=fault_body.encode(), request=request)
        mocker.patch("coastal_crawler.pdf.httpx.get", return_value=response)

        extracted, failed = run_worker(batch_size=10, adapter=StubAdapter())

        assert (extracted, failed) == (0, 1)
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "failed"
        assert "QuotaViolation" in paper.error
        assert "Rate limit quota violation" in paper.error

    def test_http_status_error_empty_body_still_marks_failed(
        self, worker_db: Engine, mocker: Any
    ) -> None:
        """A non-2xx response with no body should still produce a clear
        error, distinguishable from a body having been silently dropped."""
        _insert(worker_db, make_paper())
        request = httpx.Request("GET", "https://example.com/paper.pdf")
        response = httpx.Response(500, content=b"", request=request)
        mocker.patch("coastal_crawler.pdf.httpx.get", return_value=response)

        extracted, failed = run_worker(batch_size=10, adapter=StubAdapter())

        assert (extracted, failed) == (0, 1)
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "failed"
        assert "500" in paper.error
        assert "empty response body" in paper.error

    def test_adapter_error_marks_failed(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        adapter = MagicMock()
        adapter.extract.side_effect = RuntimeError("model crashed")
        extracted, failed = run_worker(batch_size=10, adapter=adapter)
        assert (extracted, failed) == (0, 1)
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.status == "failed"
        assert "model crashed" in paper.error

    def test_adapter_error_no_partial_extractions(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        """An adapter error must not leave orphaned extraction rows."""
        _insert(worker_db, make_paper())
        adapter = MagicMock()
        adapter.extract.side_effect = RuntimeError("oom")
        run_worker(batch_size=10, adapter=adapter)
        assert _extractions(worker_db) == []

    def test_error_text_truncated_to_2000_chars(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        adapter = MagicMock()
        adapter.extract.side_effect = RuntimeError("x" * 5000)
        run_worker(batch_size=10, adapter=adapter)
        with Session(worker_db) as s:
            paper = s.scalars(select(Paper)).one()
        assert paper.error is not None
        assert len(paper.error) <= 2000

    def test_continues_after_single_failure(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        """A failure on one paper must not prevent processing subsequent papers."""
        _insert(worker_db, make_paper(oa_pdf_url=None), make_paper())
        extracted, failed = run_worker(batch_size=10, adapter=StubAdapter())
        assert extracted == 1
        assert failed == 1

    def test_temp_file_deleted_on_adapter_error(
        self, worker_db: Engine, mock_download: MagicMock
    ) -> None:
        _insert(worker_db, make_paper())
        captured_paths: list[Path] = []

        class FailingAdapter:
            def extract(self, pdf_path: Path) -> list[ExtractionResult]:
                captured_paths.append(pdf_path)
                raise RuntimeError("adapter exploded")

        run_worker(batch_size=10, adapter=FailingAdapter())
        assert len(captured_paths) == 1
        assert not captured_paths[0].exists()


# ---------------------------------------------------------------------------
# requeue_failed
# ---------------------------------------------------------------------------

class TestRequeueFailed:
    def test_delegates_to_store(self, worker_db: Engine) -> None:
        _insert(
            worker_db,
            make_paper(status="failed"),
            make_paper(status="failed"),
            make_paper(status="extracted"),
        )
        count = requeue_failed()
        assert count == 2
        with Session(worker_db) as s:
            statuses = sorted(p.status for p in s.scalars(select(Paper)).all())
        assert statuses == ["discovered", "discovered", "extracted"]

    def test_returns_zero_when_nothing_failed(self, worker_db: Engine) -> None:
        _insert(worker_db, make_paper(status="discovered"))
        assert requeue_failed() == 0
