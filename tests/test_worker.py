"""Tests for the extraction worker (worker.py).

All tests use the ``worker_db`` fixture, which patches ``get_session``
inside the worker module to use the test engine.  This avoids needing
DATABASE_URL set in the environment while still exercising the real
session/commit logic.

Unlike the OCR worker, there are no network calls here — extraction reads
OCR text directly from disk (``ocr_dir``), so no download mocking is needed.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import ExtractionResult, StubMeasurementAdapter
from coastal_crawler.db import store
from coastal_crawler.db.models import Extraction, Paper
from coastal_crawler.ocr_worker import run_ocr_worker
from coastal_crawler.worker import (
    _sanitize_nul_bytes,
    _sanitize_nul_in_json,
    requeue_failed,
    requeue_processing,
    run_worker,
    run_worker_until_idle,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:8]


def make_paper(*, status: str = "ocr_done", **kwargs: Any) -> dict[str, Any]:
    uid = _uid()
    return {
        "doi": f"10.1/{uid}",
        "openalex_id": f"W{uid}",
        "semantic_scholar_id": None,
        "title": f"Test Paper {uid}",
        "oa_pdf_url": "https://example.com/paper.pdf",
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
def both_stages_db(clean_db: Engine, mocker: Any) -> Engine:
    """Patch get_session in both worker.py and ocr_worker.py to the same
    test engine, for tests that run the OCR stage then the extraction stage
    against the same database (see TestOcrToExtractionIntegration)."""
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
    mocker.patch("coastal_crawler.ocr_worker.get_session", _test_get_session)
    return clean_db


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


def _write_ocr(ocr_dir: Path, paper_id: int, text: str = "some ocr text") -> None:
    ocr_dir.mkdir(parents=True, exist_ok=True)
    (ocr_dir / f"{paper_id}.txt").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# NUL-byte sanitization (2026-08-20-extraction-hardening-01) — pure helpers,
# no DB. Postgres/psycopg2 reject a literal \x00 in text and jsonb columns,
# so a NUL anywhere in OCR text or a measurement dict would otherwise fail
# the whole paper.
# ---------------------------------------------------------------------------

class TestSanitizeNulBytes:
    def test_no_nul_returns_input_unchanged_and_zero_count(self) -> None:
        text = "salinity 35 psu\npage 2"
        out, count = _sanitize_nul_bytes(text)
        assert out == text
        assert count == 0

    def test_each_nul_replaced_with_replacement_char(self) -> None:
        out, count = _sanitize_nul_bytes("a\x00b\x00c")
        assert out == "a�b�c"
        assert count == 2

    def test_replacement_is_offset_preserving(self) -> None:
        """1 char in -> 1 char out, so downstream character offsets
        (find_snippet page matching, page-tag boundaries) stay valid."""
        text = "0123\x00567\x009"
        out, _ = _sanitize_nul_bytes(text)
        assert len(out) == len(text)
        for i, ch in enumerate(text):
            if ch != "\x00":
                assert out[i] == ch
        assert out.index("�") == 4

    def test_json_helper_recurses_into_nested_dicts_and_lists(self) -> None:
        value = {
            "value": "1.0",
            "units": "m\x00g/L",
            "notes": ["clean", "dir\x00ty", {"deep": "x\x00y"}],
            "page_number": 3,
            "flag": None,
        }
        out, count = _sanitize_nul_in_json(value)
        assert count == 3
        assert out["units"] == "m�g/L"
        assert out["notes"][1] == "dir�ty"
        assert out["notes"][2]["deep"] == "x�y"
        assert out["page_number"] == 3
        assert out["flag"] is None

    def test_json_helper_clean_value_unchanged_zero_count(self) -> None:
        value = {"a": ["b", "c"], "d": 1}
        out, count = _sanitize_nul_in_json(value)
        assert out == value
        assert count == 0


# ---------------------------------------------------------------------------
# run_worker — successful extraction
# ---------------------------------------------------------------------------

class TestRunWorkerSuccess:
    def test_empty_queue_returns_zeros(self, worker_db: Engine, tmp_path: Path) -> None:
        extracted, failed, requeued = run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert (extracted, failed, requeued) == (0, 0, 0)

    def test_extracted_paper_status(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert _paper(worker_db).status == "extracted"

    def test_extracted_timestamp_set(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert _paper(worker_db).extracted_at is not None

    def test_extraction_results_stored(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[make_result(), make_result()]]
        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        assert len(_extractions(worker_db)) == 2

    def test_extraction_result_fields(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        result = make_result(
            schema_name="coastal_v1",
            model_version="llm-3",
            data={"depth": 42.0, "units": "m"},
            latitude=51.5,
            longitude=-0.1,
        )
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[result]]
        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        with Session(worker_db) as s:
            ext = s.scalars(select(Extraction)).one()
        assert ext.schema_name == "coastal_v1"
        assert ext.model_version == "llm-3"
        assert ext.data["depth"] == pytest.approx(42.0)
        assert ext.latitude == pytest.approx(51.5)
        assert ext.longitude == pytest.approx(-0.1)

    def test_ocr_context_stored_once_per_paper(self, worker_db: Engine, tmp_path: Path) -> None:
        """paper_ocr_context gets exactly one row per paper, holding the same
        text run_worker read from ocr_dir — regardless of how many
        measurement records the adapter returns for that paper."""
        _insert(worker_db, make_paper())
        paper_id = _paper(worker_db).id
        _write_ocr(tmp_path, paper_id, text="the full document text")
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[make_result(), make_result()]]
        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        with Session(worker_db) as s:
            assert store.get_paper_ocr_context(s, paper_id) == "the full document text"

    def test_stub_adapter_produces_no_extractions(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert _extractions(worker_db) == []
        assert _paper(worker_db).status == "extracted"

    def test_default_adapter_is_stub(self, worker_db: Engine, tmp_path: Path) -> None:
        """Calling run_worker without adapter kwarg uses StubMeasurementAdapter."""
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        extracted, failed, requeued = run_worker(batch_size=10, ocr_dir=tmp_path)
        assert extracted == 1
        assert failed == 0

    def test_batch_size_respected(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, *[make_paper() for _ in range(5)])
        for p in _papers(worker_db):
            _write_ocr(tmp_path, p.id)
        run_worker(batch_size=3, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        with Session(worker_db) as s:
            extracted = s.scalar(select(func.count(Paper.id)).where(Paper.status == "extracted"))
            unclaimed = s.scalar(select(func.count(Paper.id)).where(Paper.status == "ocr_done"))
        assert extracted == 3
        assert unclaimed == 2

    def test_adapter_called_with_ocr_texts(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id, text="hello world")
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[]]
        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        adapter.extract_batch.assert_called_once_with(["hello world"])

    def test_returns_extracted_failed_counts(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper(), make_paper())
        for p in _papers(worker_db):
            _write_ocr(tmp_path, p.id)
        extracted, failed, requeued = run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert extracted == 2
        assert failed == 0


# ---------------------------------------------------------------------------
# run_worker — page_number/page_matched persistence (find_snippet() at
# insert time — see 2026-08-11-page-number-persistence-01)
# ---------------------------------------------------------------------------

class TestRunWorkerPageNumberPersistence:
    def test_matched_value_stores_page_number_and_matched_true(
        self, worker_db: Engine, tmp_path: Path
    ) -> None:
        """A value that literally appears on one OCR page gets that page's
        number and matched=True, exactly like site/app.py's live
        find_snippet() call would compute for the same row."""
        _insert(worker_db, make_paper())
        paper_id = _paper(worker_db).id
        ocr_text = (
            '<page number="1">intro, no numbers here</page>'
            '<page number="2">soil carbon measured at 42.5 g/kg</page>'
        )
        _write_ocr(tmp_path, paper_id, text=ocr_text)
        result = make_result(data={"value": "42.5", "attribute": "soil carbon", "units": "g/kg"})
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[result]]

        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)

        ext = _extractions(worker_db)[0]
        assert ext.page_number == 2
        assert ext.page_matched is True

    def test_unmatched_value_falls_back_to_first_page_matched_false(
        self, worker_db: Engine, tmp_path: Path
    ) -> None:
        """A value that appears nowhere in the OCR text falls back to page 1
        with matched=False — find_snippet()'s documented "best guess" path."""
        _insert(worker_db, make_paper())
        paper_id = _paper(worker_db).id
        ocr_text = (
            '<page number="1">unrelated text</page>'
            '<page number="2">also unrelated</page>'
        )
        _write_ocr(tmp_path, paper_id, text=ocr_text)
        result = make_result(data={"value": "999.9", "attribute": "nitrogen", "units": "mg/L"})
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[result]]

        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)

        ext = _extractions(worker_db)[0]
        assert ext.page_number == 1
        assert ext.page_matched is False

    def test_multiple_results_each_get_their_own_page(self, worker_db: Engine, tmp_path: Path) -> None:
        """Each measurement in a paper's outcome is snippet-matched against
        the same OCR text independently, not all pinned to one page."""
        _insert(worker_db, make_paper())
        paper_id = _paper(worker_db).id
        ocr_text = (
            '<page number="1">depth: 3.2 m</page>'
            '<page number="2">salinity: 18.7 ppt</page>'
        )
        _write_ocr(tmp_path, paper_id, text=ocr_text)
        depth_result = make_result(data={"value": "3.2", "attribute": "depth", "units": "m"})
        salinity_result = make_result(data={"value": "18.7", "attribute": "salinity", "units": "ppt"})
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[depth_result, salinity_result]]

        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)

        pages = sorted(ext.page_number for ext in _extractions(worker_db))
        assert pages == [1, 2]
        assert all(ext.page_matched is True for ext in _extractions(worker_db))


# ---------------------------------------------------------------------------
# run_worker — missing OCR text file
# ---------------------------------------------------------------------------

class TestRunWorkerMissingOcrFile:
    def test_missing_file_requeues_to_relevant_not_failed(self, worker_db: Engine, tmp_path: Path) -> None:
        """A claimed paper whose OCR text file isn't on disk (shouldn't
        happen given ocr_worker.py's write-then-commit ordering, but disk
        issues are possible) is self-healing: back to 'relevant' so it's
        re-OCR'd, not 'failed' (which requeue-failed could never fix)."""
        _insert(worker_db, make_paper())
        extracted, failed, requeued = run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert (extracted, failed, requeued) == (0, 0, 1)
        paper = _paper(worker_db)
        assert paper.status == "relevant"
        assert _extractions(worker_db) == []

    def test_missing_file_does_not_affect_other_papers(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper(), make_paper())
        papers = _papers(worker_db)
        _write_ocr(tmp_path, papers[0].id)
        # papers[1] has no OCR file.
        extracted, failed, requeued = run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert extracted == 1
        assert requeued == 1


# ---------------------------------------------------------------------------
# run_worker — failure handling
# ---------------------------------------------------------------------------

class TestRunWorkerFailures:
    def test_adapter_error_marks_failed(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        adapter = MagicMock()
        adapter.extract_batch.side_effect = RuntimeError("model crashed")
        extracted, failed, requeued = run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        assert (extracted, failed, requeued) == (0, 1, 0)
        paper = _paper(worker_db)
        assert paper.status == "failed"
        assert "model crashed" in paper.error

    def test_adapter_error_no_partial_extractions(self, worker_db: Engine, tmp_path: Path) -> None:
        """An adapter error must not leave orphaned extraction rows."""
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        adapter = MagicMock()
        adapter.extract_batch.side_effect = RuntimeError("oom")
        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        assert _extractions(worker_db) == []

    def test_error_text_truncated_to_2000_chars(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper())
        _write_ocr(tmp_path, _paper(worker_db).id)
        adapter = MagicMock()
        adapter.extract_batch.side_effect = RuntimeError("x" * 5000)
        run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        paper = _paper(worker_db)
        assert paper.error is not None
        assert len(paper.error) <= 2000

    def test_continues_after_single_failure(self, worker_db: Engine, tmp_path: Path) -> None:
        """A missing-file requeue on one paper must not prevent processing
        subsequent papers."""
        _insert(worker_db, make_paper(), make_paper())
        papers = _papers(worker_db)
        _write_ocr(tmp_path, papers[0].id)
        extracted, failed, requeued = run_worker(batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path)
        assert extracted == 1
        assert requeued == 1

    def test_document_level_extraction_failure_marks_only_that_paper(
        self, worker_db: Engine, tmp_path: Path
    ) -> None:
        """A per-document extraction failure (a str DocumentOutcome) must
        mark only that paper 'failed', not its batch-mates, and must not be
        silently recorded as a clean extraction of zero measurements — see
        EFFICIENCY.md item 3."""
        _insert(worker_db, make_paper(), make_paper())
        for p in _papers(worker_db):
            _write_ocr(tmp_path, p.id)
        adapter = MagicMock()
        adapter.extract_batch.return_value = [[], "model returned unparseable output"]
        extracted, failed, requeued = run_worker(batch_size=10, adapter=adapter, ocr_dir=tmp_path)
        assert (extracted, failed, requeued) == (1, 1, 0)
        statuses = sorted(p.status for p in _papers(worker_db))
        assert statuses == ["extracted", "failed"]


# ---------------------------------------------------------------------------
# run_worker — chunking (cross-paper batching of the GPU calls)
# ---------------------------------------------------------------------------

class TestRunWorkerChunking:
    def test_multiple_chunks_process_every_paper(self, worker_db: Engine, tmp_path: Path) -> None:
        """chunk_size smaller than the claimed batch must still process every paper."""
        _insert(worker_db, *[make_paper() for _ in range(5)])
        for p in _papers(worker_db):
            _write_ocr(tmp_path, p.id)
        adapter = MagicMock()
        adapter.extract_batch.side_effect = lambda texts: [[] for _ in texts]

        extracted, failed, requeued = run_worker(batch_size=10, adapter=adapter, chunk_size=2, ocr_dir=tmp_path)

        assert (extracted, failed, requeued) == (5, 0, 0)
        assert adapter.extract_batch.call_count == 3
        call_sizes = sorted(len(call.args[0]) for call in adapter.extract_batch.call_args_list)
        assert call_sizes == [1, 2, 2]

    def test_chunk_failure_does_not_affect_other_chunks(self, worker_db: Engine, tmp_path: Path) -> None:
        """An extract_batch exception for one chunk must not fail papers in other chunks."""
        _insert(worker_db, *[make_paper() for _ in range(4)])
        for p in _papers(worker_db):
            _write_ocr(tmp_path, p.id)
        adapter = MagicMock()
        call_count = 0

        def _side_effect(texts: list[str]) -> list[Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("gpu oom")
            return [[] for _ in texts]

        adapter.extract_batch.side_effect = _side_effect

        extracted, failed, requeued = run_worker(batch_size=10, adapter=adapter, chunk_size=2, ocr_dir=tmp_path)

        assert (extracted, failed, requeued) == (2, 2, 0)
        assert adapter.extract_batch.call_count == 2


# ---------------------------------------------------------------------------
# OCR stage -> extraction stage integration
#
# Every other test in this file writes OCR text via the local _write_ocr
# helper, and every test in test_ocr_worker.py never reads one back — so
# nothing pins the {paper_id}.txt filename contract between the two
# independently-implemented workers. This runs the real ocr_worker output
# straight into the real worker input, against the same DB.
# ---------------------------------------------------------------------------

class _NonEmptyOCRAdapter:
    """StubOCRAdapter returns "" per PDF, which run_ocr_worker treats as a
    failure — this test needs the OCR stage to actually succeed."""

    def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
        return ["some ocr text" for _ in pdf_paths]


class TestOcrToExtractionIntegration:
    def test_ocr_output_is_readable_by_extraction(
        self, both_stages_db: Engine, mocker: Any, tmp_path: Path
    ) -> None:
        _insert(both_stages_db, make_paper(status="relevant"))
        mock_resp = MagicMock()
        mock_resp.content = b"%PDF-1.4 fake"
        mock_resp.raise_for_status = MagicMock()
        mocker.patch("coastal_crawler.pdf.httpx.get", return_value=mock_resp)

        ocr_done, ocr_failed, ocr_requeued = run_ocr_worker(
            batch_size=10, adapter=_NonEmptyOCRAdapter(), ocr_dir=tmp_path
        )
        assert (ocr_done, ocr_failed, ocr_requeued) == (1, 0, 0)
        assert _paper(both_stages_db).status == "ocr_done"

        extracted, failed, requeued = run_worker(
            batch_size=10, adapter=StubMeasurementAdapter(), ocr_dir=tmp_path
        )
        assert (extracted, failed, requeued) == (1, 0, 0)
        assert _paper(both_stages_db).status == "extracted"


# ---------------------------------------------------------------------------
# run_worker_until_idle
# ---------------------------------------------------------------------------

class _FakeClock:
    """Deterministic monotonic clock + sleep for testing run_worker_until_idle
    without any real waiting."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class TestRunWorkerUntilIdle:
    def test_returns_immediately_when_no_upstream_work(self, worker_db: Engine, tmp_path: Path) -> None:
        _insert(worker_db, make_paper(status="extracted"))
        clock = _FakeClock()
        extracted, failed, requeued = run_worker_until_idle(
            batch_size=10,
            adapter=StubMeasurementAdapter(),
            ocr_dir=tmp_path,
            poll_interval=5,
            idle_timeout=1000,
            sleep_fn=clock.sleep,
            now_fn=clock.now,
        )
        assert (extracted, failed, requeued) == (0, 0, 0)
        assert clock.sleeps == []

    def test_picks_up_paper_that_becomes_ocr_done_mid_loop(self, worker_db: Engine, tmp_path: Path) -> None:
        """Directly exercises the partially-populated-OCR-directory
        requirement: a paper starts out mid-OCR (status='ocr_processing',
        no text file yet) and only becomes claimable after a simulated
        concurrent OCR worker finishes it between polls."""
        _insert(worker_db, make_paper(status="ocr_processing"))
        paper_id = _paper(worker_db).id
        clock = _FakeClock()

        def _sleep(seconds: float) -> None:
            clock.sleep(seconds)
            if len(clock.sleeps) == 1:
                _write_ocr(tmp_path, paper_id)
                with Session(worker_db) as s:
                    s.execute(update(Paper).where(Paper.id == paper_id).values(status="ocr_done"))
                    s.commit()

        extracted, failed, requeued = run_worker_until_idle(
            batch_size=10,
            adapter=StubMeasurementAdapter(),
            ocr_dir=tmp_path,
            poll_interval=5,
            idle_timeout=1000,
            sleep_fn=_sleep,
            now_fn=clock.now,
        )
        assert extracted == 1
        assert _paper(worker_db).status == "extracted"

    def test_exits_once_idle_timeout_elapses_with_work_pending(self, worker_db: Engine, tmp_path: Path) -> None:
        """A paper stuck at 'relevant' (simulating a stalled OCR job) never
        becomes claimable — the loop must still bound its wait by
        idle_timeout rather than polling forever."""
        _insert(worker_db, make_paper(status="relevant"))
        clock = _FakeClock()
        extracted, failed, requeued = run_worker_until_idle(
            batch_size=10,
            adapter=StubMeasurementAdapter(),
            ocr_dir=tmp_path,
            poll_interval=10,
            idle_timeout=25,
            sleep_fn=clock.sleep,
            now_fn=clock.now,
        )
        assert (extracted, failed, requeued) == (0, 0, 0)
        assert clock.t >= 25


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
        statuses = sorted(p.status for p in _papers(worker_db))
        assert statuses == ["extracted", "ocr_done", "ocr_done"]

    def test_returns_zero_when_nothing_failed(self, worker_db: Engine) -> None:
        _insert(worker_db, make_paper(status="discovered"))
        assert requeue_failed() == 0


# ---------------------------------------------------------------------------
# requeue_processing
# ---------------------------------------------------------------------------

class TestRequeueProcessing:
    def test_delegates_to_store(self, worker_db: Engine) -> None:
        _insert(
            worker_db,
            make_paper(status="processing"),
            make_paper(status="processing"),
            make_paper(status="extracted"),
        )
        count = requeue_processing()
        assert count == 2
        statuses = sorted(p.status for p in _papers(worker_db))
        assert statuses == ["extracted", "ocr_done", "ocr_done"]

    def test_returns_zero_when_nothing_processing(self, worker_db: Engine) -> None:
        _insert(worker_db, make_paper(status="discovered"))
        assert requeue_processing() == 0
