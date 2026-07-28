"""Tests for the OCR worker (ocr_worker.py).

All tests use the ``ocr_db`` fixture, which patches ``get_session`` inside
the ocr_worker module to use the test engine.  This avoids needing
DATABASE_URL set in the environment while still exercising the real
session/commit logic.

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

from coastal_crawler.adapter import StubOCRAdapter
from coastal_crawler.db import store
from coastal_crawler.db.models import Paper
from coastal_crawler.ocr_worker import requeue_ocr, requeue_ocr_failed, requeue_ocr_processing, run_ocr_worker


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


class FakeOCRAdapter:
    """Returns a fixed non-empty OCR text per PDF — StubOCRAdapter's "" would
    be treated as an OCR failure, which is wrong for the "success" tests."""

    def __init__(self, text: str = "some ocr text") -> None:
        self.text = text

    def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
        return [self.text for _ in pdf_paths]


@pytest.fixture
def ocr_db(clean_db: Engine, mocker: Any) -> Engine:
    """Patch ocr_worker.get_session to use the test engine.

    This lets run_ocr_worker() operate against the test database without
    needing DATABASE_URL in the environment.
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

    mocker.patch("coastal_crawler.ocr_worker.get_session", _test_get_session)
    return clean_db


@pytest.fixture
def mock_download(mocker: Any) -> MagicMock:
    """Mock httpx.get so no real HTTP requests are made during PDF download.

    Patches coastal_crawler.pdf.httpx.get, since that's where download_pdf()
    actually issues the network request — ocr_worker.py itself only imports
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


# ---------------------------------------------------------------------------
# run_ocr_worker — successful OCR
# ---------------------------------------------------------------------------

class TestRunOcrWorkerSuccess:
    def test_empty_queue_returns_zeros(self, ocr_db: Engine, tmp_path: Path) -> None:
        ocr_done, failed, requeued = run_ocr_worker(batch_size=10, adapter=StubOCRAdapter(), ocr_dir=tmp_path)
        assert (ocr_done, failed, requeued) == (0, 0, 0)

    def test_ocr_done_paper_status(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        run_ocr_worker(batch_size=10, adapter=FakeOCRAdapter(), ocr_dir=tmp_path)
        paper = _paper(ocr_db)
        assert paper.status == "ocr_done"

    def test_ocr_text_written_to_file(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        run_ocr_worker(batch_size=10, adapter=FakeOCRAdapter(text="hello world"), ocr_dir=tmp_path)
        paper = _paper(ocr_db)
        text_path = tmp_path / f"{paper.id}.txt"
        assert text_path.exists()
        assert text_path.read_text(encoding="utf-8") == "hello world"

    def test_no_tmp_residue_after_success(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        run_ocr_worker(batch_size=10, adapter=FakeOCRAdapter(), ocr_dir=tmp_path)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_batch_size_respected(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, *[make_paper() for _ in range(5)])
        run_ocr_worker(batch_size=3, adapter=FakeOCRAdapter(), ocr_dir=tmp_path)
        with Session(ocr_db) as s:
            done = s.scalar(select(func.count(Paper.id)).where(Paper.status == "ocr_done"))
            unclaimed = s.scalar(select(func.count(Paper.id)).where(Paper.status == "relevant"))
        assert done == 3
        assert unclaimed == 2

    def test_adapter_called_with_paths(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        adapter = MagicMock()
        adapter.ocr_batch.return_value = ["text"]
        run_ocr_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        adapter.ocr_batch.assert_called_once()
        pdf_paths = adapter.ocr_batch.call_args[0][0]
        assert isinstance(pdf_paths, list)
        assert len(pdf_paths) == 1
        assert isinstance(pdf_paths[0], Path)

    def test_pdf_downloaded_from_oa_url(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper(oa_pdf_url="https://example.com/mypaper.pdf"))
        run_ocr_worker(batch_size=10, adapter=FakeOCRAdapter(), ocr_dir=tmp_path)
        mock_download.assert_called_once()
        called_url = mock_download.call_args[0][0]
        assert called_url == "https://example.com/mypaper.pdf"

    def test_temp_file_deleted_after_ocr(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        captured_paths: list[Path] = []

        class CapturingAdapter:
            def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
                captured_paths.extend(pdf_paths)
                return ["text" for _ in pdf_paths]

        run_ocr_worker(batch_size=10, adapter=CapturingAdapter(), ocr_dir=tmp_path)
        assert len(captured_paths) == 1
        assert not captured_paths[0].exists()

    def test_returns_ocr_done_and_failed_counts(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper(), make_paper())
        ocr_done, failed, requeued = run_ocr_worker(batch_size=10, adapter=FakeOCRAdapter(), ocr_dir=tmp_path)
        assert ocr_done == 2
        assert failed == 0


# ---------------------------------------------------------------------------
# run_ocr_worker — Wiley pre-download cache (wiley_pdf_dir)
# ---------------------------------------------------------------------------

class TestRunOcrWorkerWileyCache:
    def test_wiley_pdf_read_from_cache_not_deleted(
        self, ocr_db: Engine, mocker: Any, tmp_path: Path
    ) -> None:
        """A pre-downloaded Wiley PDF is read from wiley_pdf_dir and survives
        the run — it's a shared cache, not a per-worker temp file."""
        _insert(
            ocr_db,
            make_paper(
                discovered_from="wiley",
                oa_pdf_url="https://api.wiley.com/onlinelibrary/tdm/v1/articles/10.1/xyz",
            ),
        )
        paper_id = _paper(ocr_db).id
        wiley_dir = tmp_path / "wiley"
        wiley_dir.mkdir()
        ocr_dir = tmp_path / "ocr"
        cached = wiley_dir / f"{paper_id}.pdf"
        cached.write_bytes(b"%PDF-1.4 fake")
        http_get = mocker.patch("coastal_crawler.pdf.httpx.get")

        ocr_done, failed, requeued = run_ocr_worker(
            batch_size=10, adapter=FakeOCRAdapter(), wiley_pdf_dir=wiley_dir, ocr_dir=ocr_dir
        )

        assert (ocr_done, failed, requeued) == (1, 0, 0)
        http_get.assert_not_called()
        assert cached.exists()

    def test_wiley_pdf_missing_requeues_to_relevant(
        self, ocr_db: Engine, mocker: Any, tmp_path: Path
    ) -> None:
        """A Wiley paper claimed before its PDF is pre-downloaded goes back
        to 'relevant' (not 'ocr_failed') and isn't counted as an OCR
        failure."""
        _insert(
            ocr_db,
            make_paper(
                discovered_from="wiley",
                oa_pdf_url="https://api.wiley.com/onlinelibrary/tdm/v1/articles/10.1/xyz",
            ),
        )
        http_get = mocker.patch("coastal_crawler.pdf.httpx.get")

        ocr_done, failed, requeued = run_ocr_worker(
            batch_size=10, adapter=FakeOCRAdapter(), wiley_pdf_dir=tmp_path / "wiley", ocr_dir=tmp_path / "ocr"
        )

        assert (ocr_done, failed, requeued) == (0, 0, 1)
        http_get.assert_not_called()
        paper = _paper(ocr_db)
        assert paper.status == "relevant"

    def test_non_wiley_paper_still_downloads_when_wiley_pdf_dir_set(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        """wiley_pdf_dir only changes behavior for Wiley-sourced papers."""
        _insert(ocr_db, make_paper(oa_pdf_url="https://example.com/paper.pdf"))

        ocr_done, failed, requeued = run_ocr_worker(
            batch_size=10, adapter=FakeOCRAdapter(), wiley_pdf_dir=tmp_path / "wiley", ocr_dir=tmp_path / "ocr"
        )

        assert (ocr_done, failed, requeued) == (1, 0, 0)
        mock_download.assert_called_once()


# ---------------------------------------------------------------------------
# run_ocr_worker — failure handling
# ---------------------------------------------------------------------------

class TestRunOcrWorkerFailures:
    def test_no_pdf_url_marks_ocr_failed(self, ocr_db: Engine, tmp_path: Path) -> None:
        _insert(ocr_db, make_paper(oa_pdf_url=None))
        ocr_done, failed, requeued = run_ocr_worker(batch_size=10, adapter=StubOCRAdapter(), ocr_dir=tmp_path)
        assert (ocr_done, failed, requeued) == (0, 1, 0)
        paper = _paper(ocr_db)
        assert paper.status == "ocr_failed"
        assert paper.error is not None

    def test_download_http_error_marks_ocr_failed(self, ocr_db: Engine, mocker: Any, tmp_path: Path) -> None:
        _insert(ocr_db, make_paper())
        mocker.patch(
            "coastal_crawler.pdf.httpx.get",
            side_effect=Exception("connection refused"),
        )
        ocr_done, failed, requeued = run_ocr_worker(batch_size=10, adapter=StubOCRAdapter(), ocr_dir=tmp_path)
        assert (ocr_done, failed, requeued) == (0, 1, 0)
        paper = _paper(ocr_db)
        assert paper.status == "ocr_failed"
        assert "connection refused" in paper.error

    def test_http_status_error_includes_response_body(self, ocr_db: Engine, mocker: Any, tmp_path: Path) -> None:
        """A non-2xx PDF download should surface the response body in
        paper.error — this is where Wiley's Apigee gateway hides the actual
        rate-limit diagnostic."""
        _insert(ocr_db, make_paper())
        fault_body = (
            '{"fault":{"faultstring":"Rate limit quota violation. '
            'Quota limit  exceeded.","detail":'
            '{"errorcode":"policies.ratelimit.QuotaViolation"}}}'
        )
        request = httpx.Request("GET", "https://api.wiley.com/onlinelibrary/tdm/v1/some-doi")
        response = httpx.Response(500, content=fault_body.encode(), request=request)
        mocker.patch("coastal_crawler.pdf.httpx.get", return_value=response)

        ocr_done, failed, requeued = run_ocr_worker(batch_size=10, adapter=StubOCRAdapter(), ocr_dir=tmp_path)

        assert (ocr_done, failed, requeued) == (0, 1, 0)
        paper = _paper(ocr_db)
        assert paper.status == "ocr_failed"
        assert "QuotaViolation" in paper.error

    def test_adapter_error_marks_ocr_failed(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        adapter = MagicMock()
        adapter.ocr_batch.side_effect = RuntimeError("model crashed")
        ocr_done, failed, requeued = run_ocr_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        assert (ocr_done, failed, requeued) == (0, 1, 0)
        paper = _paper(ocr_db)
        assert paper.status == "ocr_failed"
        assert "model crashed" in paper.error

    def test_error_text_truncated_to_2000_chars(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        adapter = MagicMock()
        adapter.ocr_batch.side_effect = RuntimeError("x" * 5000)
        run_ocr_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        paper = _paper(ocr_db)
        assert paper.error is not None
        assert len(paper.error) <= 2000

    def test_continues_after_single_failure(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        """A failure on one paper must not prevent processing subsequent papers."""
        _insert(ocr_db, make_paper(oa_pdf_url=None), make_paper())
        ocr_done, failed, requeued = run_ocr_worker(batch_size=10, adapter=FakeOCRAdapter(), ocr_dir=tmp_path)
        assert ocr_done == 1
        assert failed == 1

    def test_empty_ocr_text_marks_ocr_failed(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        """Empty/whitespace-only OCR output must not be silently written as
        a valid ocr_done file — that would cause extraction to silently
        yield zero measurements."""
        _insert(ocr_db, make_paper())
        run_ocr_worker(batch_size=10, adapter=FakeOCRAdapter(text="   \n\t "), ocr_dir=tmp_path)
        paper = _paper(ocr_db)
        assert paper.status == "ocr_failed"
        assert "empty" in paper.error.lower()
        assert list(tmp_path.glob("*.txt")) == []

    def test_temp_file_deleted_on_adapter_error(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(ocr_db, make_paper())
        captured_paths: list[Path] = []

        class FailingAdapter:
            def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
                captured_paths.extend(pdf_paths)
                raise RuntimeError("adapter exploded")

        run_ocr_worker(batch_size=10, adapter=FailingAdapter(), ocr_dir=tmp_path)
        assert len(captured_paths) == 1
        assert not captured_paths[0].exists()


# ---------------------------------------------------------------------------
# run_ocr_worker — chunking (cross-paper batching of the GPU calls)
# ---------------------------------------------------------------------------

class TestRunOcrWorkerChunking:
    def test_multiple_chunks_process_every_paper(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        """chunk_size smaller than the claimed batch must still process every paper."""
        _insert(ocr_db, *[make_paper() for _ in range(5)])
        adapter = MagicMock()
        adapter.ocr_batch.side_effect = lambda pdf_paths: ["text" for _ in pdf_paths]

        ocr_done, failed, requeued = run_ocr_worker(
            batch_size=10, adapter=adapter, chunk_size=2, ocr_dir=tmp_path
        )

        assert (ocr_done, failed, requeued) == (5, 0, 0)
        # 5 papers at chunk_size=2 -> three ocr_batch calls sized 2, 2, 1.
        assert adapter.ocr_batch.call_count == 3
        call_sizes = sorted(len(call.args[0]) for call in adapter.ocr_batch.call_args_list)
        assert call_sizes == [1, 2, 2]

    def test_chunk_failure_marks_only_that_chunk_ocr_failed(
        self, ocr_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        """An ocr_batch exception for one chunk must not fail papers in other chunks."""
        _insert(ocr_db, *[make_paper() for _ in range(4)])
        adapter = MagicMock()
        call_count = 0

        def _side_effect(pdf_paths: list[Path]) -> list[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("gpu oom")
            return ["text" for _ in pdf_paths]

        adapter.ocr_batch.side_effect = _side_effect

        ocr_done, failed, requeued = run_ocr_worker(
            batch_size=10, adapter=adapter, chunk_size=2, ocr_dir=tmp_path
        )

        assert (ocr_done, failed, requeued) == (2, 2, 0)
        assert adapter.ocr_batch.call_count == 2


# ---------------------------------------------------------------------------
# requeue_ocr_processing / requeue_ocr_failed / requeue_ocr
# ---------------------------------------------------------------------------

class TestRequeueOcrProcessing:
    def test_delegates_to_store(self, ocr_db: Engine) -> None:
        _insert(
            ocr_db,
            make_paper(status="ocr_processing"),
            make_paper(status="ocr_processing"),
            make_paper(status="ocr_done"),
        )
        count = requeue_ocr_processing()
        assert count == 2
        statuses = sorted(p.status for p in _papers(ocr_db))
        assert statuses == ["ocr_done", "relevant", "relevant"]


class TestRequeueOcrFailed:
    def test_delegates_to_store(self, ocr_db: Engine) -> None:
        _insert(ocr_db, make_paper(status="ocr_failed"), make_paper(status="ocr_done"))
        count = requeue_ocr_failed()
        assert count == 1
        statuses = sorted(p.status for p in _papers(ocr_db))
        assert statuses == ["ocr_done", "relevant"]


class TestRequeueOcr:
    def test_delegates_to_store(self, ocr_db: Engine) -> None:
        _insert(ocr_db, make_paper(status="ocr_done"), make_paper(status="failed"))
        count = requeue_ocr()
        assert count == 2
        statuses = sorted(p.status for p in _papers(ocr_db))
        assert statuses == ["relevant", "relevant"]
