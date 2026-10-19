"""Tests for the table-clean worker (table_clean_worker.py).

All tests use the ``table_clean_db`` fixture, which patches ``get_session``
inside the table_clean_worker module to use the test engine. HTTP calls (PDF
re-download) and ``process_pdf`` (page rendering) are mocked via
pytest-mock — no real PDF/GPU dependency is exercised here.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import StubTableCleanAdapter
from coastal_crawler.db import store
from coastal_crawler.db.models import Paper
from coastal_crawler.table_clean_worker import (
    requeue_table_clean_failed,
    requeue_table_clean_processing,
    run_table_clean_worker,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:8]


def make_paper(
    *,
    oa_pdf_url: str | None = "https://example.com/paper.pdf",
    status: str = "ocr_done",
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


class FakeTableCleanAdapter:
    """Returns each input document with a fixed suffix appended — distinct
    from the input (unlike StubTableCleanAdapter's echo), so tests that check
    for a "real" cleaning pass don't accidentally trip the worker's
    every-table-bearing-page-unchanged fail-loud guard."""

    def __init__(self, suffix: str = " [cleaned]") -> None:
        self.suffix = suffix
        self.context_length_exceeded_docs: set[int] = set()

    def clean_tables_batch(self, documents: list[str], processed_pdf_dirs: list[str]) -> list[str]:
        return [d + self.suffix for d in documents]


@pytest.fixture
def table_clean_db(clean_db: Engine, mocker: Any) -> Engine:
    """Patch table_clean_worker.get_session to use the test engine."""
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

    mocker.patch("coastal_crawler.table_clean_worker.get_session", _test_get_session)
    return clean_db


@pytest.fixture
def mock_download(mocker: Any) -> MagicMock:
    """Mock httpx.get so no real HTTP requests are made during PDF re-download."""
    mock_resp = MagicMock()
    mock_resp.content = b"%PDF-1.4 fake"
    mock_resp.raise_for_status = MagicMock()
    return mocker.patch("coastal_crawler.pdf.httpx.get", return_value=mock_resp)


@pytest.fixture
def mock_process_pdf(mocker: Any) -> MagicMock:
    """Mock scholarlm.utils.pdf.process_pdf (page rendering) — returns a
    single fake base64 page by default, enough for single-page OCR text."""
    return mocker.patch(
        "coastal_crawler.table_clean_worker.process_pdf",
        return_value=["fakebase64page0"],
    )


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


def _write_ocr(ocr_dir: Path, paper_id: int, text: str = "some ocr text") -> None:
    ocr_dir.mkdir(parents=True, exist_ok=True)
    (ocr_dir / f"{paper_id}.txt").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# run_table_clean_worker — successful table-cleaning
# ---------------------------------------------------------------------------

class TestRunTableCleanWorkerSuccess:
    def test_empty_queue_returns_zeros(self, table_clean_db: Engine, tmp_path: Path) -> None:
        result = run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),
            ocr_dir=tmp_path / "ocr",
            table_clean_dir=tmp_path / "clean",
            scratch_dir=tmp_path / "scratch",
        )
        assert result == (0, 0, 0)

    def test_table_clean_done_paper_status(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        _write_ocr(ocr_dir, _paper(table_clean_db).id)
        run_table_clean_worker(
            batch_size=10, adapter=FakeTableCleanAdapter(), ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )
        assert _paper(table_clean_db).status == "table_clean_done"

    def test_cleaned_text_written_to_file(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        paper_id = _paper(table_clean_db).id
        _write_ocr(ocr_dir, paper_id, text="hello world")
        run_table_clean_worker(
            batch_size=10, adapter=FakeTableCleanAdapter(), ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )
        text_path = clean_dir / f"{paper_id}.txt"
        assert text_path.exists()
        assert text_path.read_text(encoding="utf-8") == "hello world [cleaned]"

    def test_scratch_dir_cleaned_up_after_success(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        paper_id = _paper(table_clean_db).id
        _write_ocr(ocr_dir, paper_id)
        run_table_clean_worker(
            batch_size=10, adapter=FakeTableCleanAdapter(), ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )
        assert not (scratch_dir / str(paper_id)).exists()

    def test_adapter_called_with_documents_and_scratch_dirs(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        paper_id = _paper(table_clean_db).id
        _write_ocr(ocr_dir, paper_id, text="hello world")
        adapter = MagicMock()
        adapter.context_length_exceeded_docs = set()
        adapter.clean_tables_batch.return_value = ["hello world [cleaned]"]

        run_table_clean_worker(
            batch_size=10, adapter=adapter, ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )

        adapter.clean_tables_batch.assert_called_once()
        documents, processed_pdf_dirs = adapter.clean_tables_batch.call_args[0]
        assert documents == ["hello world"]
        assert processed_pdf_dirs == [str(scratch_dir / str(paper_id))]

    def test_page_images_written_as_b64_files(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        paper_id = _paper(table_clean_db).id
        _write_ocr(ocr_dir, paper_id, text="no page tags here")

        class _CapturingAdapter:
            context_length_exceeded_docs: set[int] = set()

            def clean_tables_batch(self, documents: list[str], processed_pdf_dirs: list[str]) -> list[str]:
                b64_file = Path(processed_pdf_dirs[0]) / "0.b64"
                assert b64_file.read_text(encoding="utf-8") == "fakebase64page0"
                return [d + " [cleaned]" for d in documents]

        run_table_clean_worker(
            batch_size=10, adapter=_CapturingAdapter(), ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )
        assert _paper(table_clean_db).status == "table_clean_done"

    def test_returns_table_clean_done_and_failed_counts(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper(), make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        for p in _papers(table_clean_db):
            _write_ocr(ocr_dir, p.id)
        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10, adapter=FakeTableCleanAdapter(), ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )
        assert (table_clean_done, failed) == (2, 0)


# ---------------------------------------------------------------------------
# run_table_clean_worker — per-paper rendering failure must not abort the
# whole chunk (the behavior scholarlm's _clean_tables itself gets wrong —
# see table_clean_worker.py's module docstring)
# ---------------------------------------------------------------------------

class TestRunTableCleanWorkerPartialChunkFailure:
    def test_one_papers_render_failure_does_not_fail_the_other(
        self, table_clean_db: Engine, mock_download: MagicMock, mocker: Any, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper(), make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        papers = _papers(table_clean_db)
        for p in papers:
            _write_ocr(ocr_dir, p.id)

        # Fail rendering for exactly the first paper processed, succeed for
        # the rest — proves the worker excludes a bad paper from the shared
        # adapter call rather than letting one failure take down the batch.
        calls = {"n": 0}

        def _process_pdf(pdf_path: str, target_longest_dim: int = 2048) -> list[str]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("corrupt PDF")
            return ["fakebase64page0"]

        mocker.patch("coastal_crawler.table_clean_worker.process_pdf", side_effect=_process_pdf)

        adapter = FakeTableCleanAdapter()
        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10, adapter=adapter, ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )

        assert (table_clean_done, failed) == (1, 1)
        statuses = sorted(p.status for p in _papers(table_clean_db))
        assert statuses == ["table_clean_done", "table_clean_failed"]

    def test_page_count_mismatch_fails_only_that_paper(
        self, table_clean_db: Engine, mock_download: MagicMock, mocker: Any, tmp_path: Path
    ) -> None:
        """A paper whose OCR text references a page beyond what the
        (re-downloaded) PDF actually renders is excluded from the
        adapter call and marked failed — not silently cleaned against the
        wrong/missing page image."""
        _insert(table_clean_db, make_paper(), make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        papers = _papers(table_clean_db)
        # First paper's OCR text references page 5, but process_pdf below
        # only ever returns 1 rendered page -> mismatch for this paper.
        _write_ocr(ocr_dir, papers[0].id, text='<page number="5">stuff</page>')
        _write_ocr(ocr_dir, papers[1].id, text='<page number="0">stuff</page>')
        mocker.patch(
            "coastal_crawler.table_clean_worker.process_pdf", return_value=["fakebase64page0"]
        )

        adapter = FakeTableCleanAdapter()
        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10, adapter=adapter, ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )

        assert (table_clean_done, failed) == (1, 1)
        with Session(table_clean_db) as s:
            mismatched = s.scalars(select(Paper).where(Paper.id == papers[0].id)).one()
        assert mismatched.status == "table_clean_failed"
        assert "page count mismatch" in mismatched.error.lower()


# ---------------------------------------------------------------------------
# run_table_clean_worker — fail-loud guard: every table-bearing paper
# returned unchanged is treated as a broken endpoint, not coincidence
# ---------------------------------------------------------------------------

class TestRunTableCleanWorkerUnchangedGuard:
    def test_all_table_bearing_papers_unchanged_raises_for_whole_chunk(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        paper_id = _paper(table_clean_db).id
        _write_ocr(ocr_dir, paper_id, text='<page number="0"><table number="1">x</table></page>')

        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),  # echoes input unchanged
            ocr_dir=ocr_dir,
            table_clean_dir=clean_dir,
            scratch_dir=scratch_dir,
        )

        assert (table_clean_done, failed) == (0, 1)
        paper = _paper(table_clean_db)
        assert paper.status == "table_clean_failed"
        assert "unchanged" in paper.error.lower()

    def test_non_table_bearing_paper_unchanged_is_fine(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        """A paper with no <table> tags at all is expected to come back
        unchanged — that's not evidence of a broken endpoint."""
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        paper_id = _paper(table_clean_db).id
        _write_ocr(ocr_dir, paper_id, text='<page number="0">no tables on this page</page>')

        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),
            ocr_dir=ocr_dir,
            table_clean_dir=clean_dir,
            scratch_dir=scratch_dir,
        )

        assert (table_clean_done, failed) == (1, 0)
        assert _paper(table_clean_db).status == "table_clean_done"


# ---------------------------------------------------------------------------
# run_table_clean_worker — context-length-exceeded
# ---------------------------------------------------------------------------

class TestRunTableCleanWorkerContextLengthExceeded:
    def test_context_length_exceeded_marks_failed_not_done(
        self, table_clean_db: Engine, mock_download: MagicMock, mock_process_pdf: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir, clean_dir, scratch_dir = tmp_path / "ocr", tmp_path / "clean", tmp_path / "scratch"
        paper_id = _paper(table_clean_db).id
        _write_ocr(ocr_dir, paper_id, text="some plain ocr text")

        adapter = MagicMock()
        adapter.context_length_exceeded_docs = {0}
        adapter.clean_tables_batch.return_value = ["some plain ocr text"]

        run_table_clean_worker(
            batch_size=10, adapter=adapter, ocr_dir=ocr_dir,
            table_clean_dir=clean_dir, scratch_dir=scratch_dir,
        )

        paper = _paper(table_clean_db)
        assert paper.status == "table_clean_failed"
        assert "context length" in paper.error.lower()


# ---------------------------------------------------------------------------
# run_table_clean_worker — Wiley pre-download cache
# ---------------------------------------------------------------------------

class TestRunTableCleanWorkerWileyCache:
    def test_wiley_pdf_missing_requeues_to_ocr_done(
        self, table_clean_db: Engine, mocker: Any, tmp_path: Path
    ) -> None:
        _insert(
            table_clean_db,
            make_paper(
                discovered_from="wiley",
                oa_pdf_url="https://api.wiley.com/onlinelibrary/tdm/v1/articles/10.1/xyz",
            ),
        )
        http_get = mocker.patch("coastal_crawler.pdf.httpx.get")

        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),
            wiley_pdf_dir=tmp_path / "wiley",
            ocr_dir=tmp_path / "ocr",
            table_clean_dir=tmp_path / "clean",
            scratch_dir=tmp_path / "scratch",
        )

        assert (table_clean_done, failed, requeued) == (0, 0, 1)
        http_get.assert_not_called()
        assert _paper(table_clean_db).status == "ocr_done"


# ---------------------------------------------------------------------------
# run_table_clean_worker — failure handling
# ---------------------------------------------------------------------------

class TestRunTableCleanWorkerFailures:
    def test_no_pdf_url_marks_table_clean_failed(self, table_clean_db: Engine, tmp_path: Path) -> None:
        _insert(table_clean_db, make_paper(oa_pdf_url=None))
        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),
            ocr_dir=tmp_path / "ocr",
            table_clean_dir=tmp_path / "clean",
            scratch_dir=tmp_path / "scratch",
        )
        assert (table_clean_done, failed, requeued) == (0, 1, 0)
        assert _paper(table_clean_db).status == "table_clean_failed"

    def test_missing_ocr_text_marks_table_clean_failed(
        self, table_clean_db: Engine, mock_download: MagicMock, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),
            ocr_dir=tmp_path / "ocr",  # no file written
            table_clean_dir=tmp_path / "clean",
            scratch_dir=tmp_path / "scratch",
        )
        assert (table_clean_done, failed, requeued) == (0, 1, 0)
        paper = _paper(table_clean_db)
        assert paper.status == "table_clean_failed"
        assert "ocr text missing" in paper.error.lower()

    def test_download_http_error_marks_table_clean_failed(
        self, table_clean_db: Engine, mocker: Any, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        mocker.patch("coastal_crawler.pdf.httpx.get", side_effect=Exception("connection refused"))
        table_clean_done, failed, requeued = run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),
            ocr_dir=tmp_path / "ocr",
            table_clean_dir=tmp_path / "clean",
            scratch_dir=tmp_path / "scratch",
        )
        assert (table_clean_done, failed, requeued) == (0, 1, 0)
        paper = _paper(table_clean_db)
        assert paper.status == "table_clean_failed"
        assert "connection refused" in paper.error

    def test_error_text_truncated_to_2000_chars(
        self, table_clean_db: Engine, mock_download: MagicMock, mocker: Any, tmp_path: Path
    ) -> None:
        _insert(table_clean_db, make_paper())
        ocr_dir = tmp_path / "ocr"
        _write_ocr(ocr_dir, _paper(table_clean_db).id)
        mocker.patch(
            "coastal_crawler.table_clean_worker.process_pdf",
            side_effect=RuntimeError("x" * 5000),
        )
        run_table_clean_worker(
            batch_size=10,
            adapter=StubTableCleanAdapter(),
            ocr_dir=ocr_dir,
            table_clean_dir=tmp_path / "clean",
            scratch_dir=tmp_path / "scratch",
        )
        paper = _paper(table_clean_db)
        assert paper.error is not None
        assert len(paper.error) <= 2000


# ---------------------------------------------------------------------------
# requeue_table_clean_processing / requeue_table_clean_failed
# ---------------------------------------------------------------------------

class TestRequeueTableCleanProcessing:
    def test_delegates_to_store(self, table_clean_db: Engine) -> None:
        _insert(
            table_clean_db,
            make_paper(status="table_clean_processing"),
            make_paper(status="table_clean_processing"),
            make_paper(status="table_clean_done"),
        )
        count = requeue_table_clean_processing()
        assert count == 2
        statuses = sorted(p.status for p in _papers(table_clean_db))
        assert statuses == ["ocr_done", "ocr_done", "table_clean_done"]


class TestRequeueTableCleanFailed:
    def test_delegates_to_store(self, table_clean_db: Engine) -> None:
        _insert(table_clean_db, make_paper(status="table_clean_failed"), make_paper(status="table_clean_done"))
        count = requeue_table_clean_failed()
        assert count == 1
        statuses = sorted(p.status for p in _papers(table_clean_db))
        assert statuses == ["ocr_done", "table_clean_done"]
