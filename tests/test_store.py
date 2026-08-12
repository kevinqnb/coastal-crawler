"""Tests for the storage layer (db/store.py).

Most tests use ``db_session`` (rolled back after each test, no committed state).
The SKIP LOCKED test uses ``clean_db`` (commits real transactions, truncates after).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import ExtractionResult
from coastal_crawler.db import store
from coastal_crawler.db.models import Extraction, Location, Paper, Vote


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:8]


def make_paper(
    *,
    doi: str | None = None,
    openalex_id: str | None = None,
    semantic_scholar_id: str | None = None,
    status: str = "discovered",
    **kwargs,
) -> dict:
    """Return a minimal paper dict suitable for upsert_papers."""
    uid = _uid()
    return {
        "doi": doi if doi is not None else f"10.1/{uid}",
        "openalex_id": openalex_id if openalex_id is not None else f"W{uid}",
        "semantic_scholar_id": semantic_scholar_id,
        "title": f"Test Paper {uid}",
        "oa_pdf_url": None,
        "metadata": {},
        "status": status,
        **kwargs,
    }


def make_extraction_result(**kwargs) -> ExtractionResult:
    return ExtractionResult(
        schema_name=kwargs.get("schema_name", "test_schema"),
        model_version=kwargs.get("model_version", "v1"),
        data=kwargs.get("data", {"value": 1.0, "units": "m"}),
        confidence=kwargs.get("confidence", 0.9),
        provenance=kwargs.get("provenance", {"page_number": 1}),
        latitude=kwargs.get("latitude"),
        longitude=kwargs.get("longitude"),
    )


# ---------------------------------------------------------------------------
# upsert_papers
# ---------------------------------------------------------------------------

class TestUpsertPapers:
    def test_inserts_new_paper(self, db_session: Session) -> None:
        n = store.upsert_papers([make_paper()], db_session)
        assert n == 1
        count = db_session.scalar(select(func.count(Paper.id)))
        assert count == 1

    def test_inserts_multiple_papers(self, db_session: Session) -> None:
        n = store.upsert_papers([make_paper(), make_paper()], db_session)
        assert n == 2

    def test_empty_list_returns_zero(self, db_session: Session) -> None:
        assert store.upsert_papers([], db_session) == 0

    def test_doi_dedup_same_source(self, db_session: Session) -> None:
        """Re-inserting the same DOI is silently ignored."""
        doi = f"10.1/{_uid()}"
        store.upsert_papers([make_paper(doi=doi)], db_session)
        n = store.upsert_papers([make_paper(doi=doi)], db_session)
        assert n == 0
        count = db_session.scalar(select(func.count(Paper.id)))
        assert count == 1

    def test_doi_dedup_cross_source(self, db_session: Session) -> None:
        """Same DOI arriving from two different sources → only one row."""
        doi = f"10.1/{_uid()}"
        n1 = store.upsert_papers([make_paper(doi=doi, openalex_id=f"W{_uid()}")], db_session)
        n2 = store.upsert_papers([make_paper(doi=doi, semantic_scholar_id=f"S{_uid()}")], db_session)
        assert n1 == 1
        assert n2 == 0
        count = db_session.scalar(select(func.count(Paper.id)))
        assert count == 1

    def test_no_doi_dedup_by_openalex_id(self, db_session: Session) -> None:
        """Papers without a DOI dedup on openalex_id."""
        oa_id = f"W{_uid()}"
        store.upsert_papers([make_paper(doi=None, openalex_id=oa_id)], db_session)
        n = store.upsert_papers([make_paper(doi=None, openalex_id=oa_id)], db_session)
        assert n == 0
        count = db_session.scalar(select(func.count(Paper.id)))
        assert count == 1

    def test_no_doi_dedup_by_semantic_scholar_id(self, db_session: Session) -> None:
        """Papers without DOI or openalex_id dedup on semantic_scholar_id."""
        s2_id = f"S{_uid()}"
        store.upsert_papers(
            [make_paper(doi=None, openalex_id=None, semantic_scholar_id=s2_id)],
            db_session,
        )
        n = store.upsert_papers(
            [make_paper(doi=None, openalex_id=None, semantic_scholar_id=s2_id)],
            db_session,
        )
        assert n == 0

    def test_different_dois_both_inserted(self, db_session: Session) -> None:
        """Two papers with different DOIs are both inserted."""
        papers = [make_paper(doi=f"10.1/{_uid()}") for _ in range(3)]
        n = store.upsert_papers(papers, db_session)
        assert n == 3


# ---------------------------------------------------------------------------
# claim_batch_for_ocr
# ---------------------------------------------------------------------------

class TestClaimBatchForOcr:
    def test_claims_relevant_papers(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="relevant"), make_paper(status="relevant")], db_session)
        claimed = store.claim_batch_for_ocr(10, db_session)
        assert len(claimed) == 2

    def test_sets_status_to_ocr_processing(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="relevant")], db_session)
        claimed = store.claim_batch_for_ocr(10, db_session)
        assert all(p.status == "ocr_processing" for p in claimed)

    def test_respects_batch_size(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="relevant") for _ in range(5)], db_session)
        claimed = store.claim_batch_for_ocr(3, db_session)
        assert len(claimed) == 3

    def test_returns_empty_when_nothing_relevant(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_done")], db_session)
        claimed = store.claim_batch_for_ocr(10, db_session)
        assert claimed == []

    def test_skips_ocr_processing_papers(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_processing")], db_session)
        claimed = store.claim_batch_for_ocr(10, db_session)
        assert claimed == []

    def test_skip_locked_no_double_claim(self, clean_db: Engine) -> None:
        """Two concurrent open sessions cannot claim the same paper.

        s1 holds an uncommitted FOR UPDATE lock on the row.  s2 uses SKIP
        LOCKED so it silently skips the locked row instead of blocking.
        """
        with Session(clean_db) as setup:
            store.upsert_papers([make_paper(status="relevant")], setup)
            setup.commit()

        s1 = Session(clean_db)
        s2 = Session(clean_db)
        try:
            batch1 = store.claim_batch_for_ocr(10, s1)   # acquires row lock
            batch2 = store.claim_batch_for_ocr(10, s2)   # SKIP LOCKED → 0 rows

            assert len(batch1) == 1
            assert len(batch2) == 0
        finally:
            s1.rollback()
            s1.close()
            s2.rollback()
            s2.close()


class TestMarkOcrDoneAndFailed:
    def test_mark_ocr_done_sets_status(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        store.mark_ocr_done(paper.id, db_session)
        db_session.expire(paper)
        assert paper.status == "ocr_done"

    def test_mark_ocr_failed_sets_status_and_error(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        store.mark_ocr_failed(paper.id, "OCR produced empty output", db_session)
        db_session.expire(paper)
        assert paper.status == "ocr_failed"
        assert paper.error == "OCR produced empty output"


class TestResetOcrProcessingToRelevant:
    def test_resets_ocr_processing_paper(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        updated = store.reset_ocr_processing_to_relevant(paper.id, db_session)
        assert updated is True
        db_session.expire(paper)
        assert paper.status == "relevant"

    def test_no_op_when_not_ocr_processing(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_failed")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        updated = store.reset_ocr_processing_to_relevant(paper.id, db_session)
        assert updated is False
        db_session.expire(paper)
        assert paper.status == "ocr_failed"


class TestRequeueOcrProcessing:
    def test_resets_all_ocr_processing_to_relevant(self, db_session: Session) -> None:
        store.upsert_papers(
            [
                make_paper(status="ocr_processing"),
                make_paper(status="ocr_processing"),
                make_paper(status="ocr_done"),
            ],
            db_session,
        )
        n = store.requeue_ocr_processing(db_session)
        assert n == 2
        statuses = sorted(p.status for p in db_session.scalars(select(Paper)).all())
        assert statuses == ["ocr_done", "relevant", "relevant"]

    def test_returns_zero_when_nothing_ocr_processing(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="discovered")], db_session)
        assert store.requeue_ocr_processing(db_session) == 0


class TestRequeueOcrFailed:
    def test_resets_ocr_failed_to_relevant(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_failed"), make_paper(status="ocr_failed")], db_session)
        n = store.requeue_ocr_failed(db_session)
        assert n == 2
        statuses = {p.status for p in db_session.scalars(select(Paper)).all()}
        assert statuses == {"relevant"}

    def test_clears_error(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        store.mark_ocr_failed(paper.id, "some error", db_session)
        store.requeue_ocr_failed(db_session)
        db_session.expire(paper)
        assert paper.error is None

    def test_returns_zero_when_nothing_ocr_failed(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="discovered")], db_session)
        assert store.requeue_ocr_failed(db_session) == 0


class TestRequeueOcr:
    def test_resets_every_downstream_status_to_relevant(self, db_session: Session) -> None:
        store.upsert_papers(
            [
                make_paper(status="ocr_done"),
                make_paper(status="ocr_failed"),
                make_paper(status="processing"),
                make_paper(status="extracted"),
                make_paper(status="failed"),
                make_paper(status="discovered"),
            ],
            db_session,
        )
        n = store.requeue_ocr(db_session)
        assert n == 5
        statuses = sorted(p.status for p in db_session.scalars(select(Paper)).all())
        assert statuses == ["discovered", "relevant", "relevant", "relevant", "relevant", "relevant"]

    def test_clears_error_and_extracted_at(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        store.mark_extracted(paper.id, db_session)
        store.mark_failed(paper.id, "boom", db_session)
        store.requeue_ocr(db_session)
        db_session.expire(paper)
        assert paper.status == "relevant"
        assert paper.error is None
        assert paper.extracted_at is None


# ---------------------------------------------------------------------------
# claim_batch
# ---------------------------------------------------------------------------

class TestClaimBatch:
    def test_claims_ocr_done_papers(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_done"), make_paper(status="ocr_done")], db_session)
        claimed = store.claim_batch(10, db_session)
        assert len(claimed) == 2

    def test_sets_status_to_processing(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_done")], db_session)
        claimed = store.claim_batch(10, db_session)
        assert all(p.status == "processing" for p in claimed)

    def test_respects_batch_size(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="ocr_done") for _ in range(5)], db_session)
        claimed = store.claim_batch(3, db_session)
        assert len(claimed) == 3

    def test_returns_empty_when_nothing_ocr_done(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="extracted")], db_session)
        claimed = store.claim_batch(10, db_session)
        assert claimed == []

    def test_skips_relevant_papers(self, db_session: Session) -> None:
        """'relevant' papers haven't been OCR'd yet — not claimable for extraction."""
        store.upsert_papers([make_paper(status="relevant")], db_session)
        claimed = store.claim_batch(10, db_session)
        assert claimed == []

    def test_skips_processing_papers(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="processing")], db_session)
        claimed = store.claim_batch(10, db_session)
        assert claimed == []

    def test_skips_failed_papers(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="failed")], db_session)
        claimed = store.claim_batch(10, db_session)
        assert claimed == []

    def test_skip_locked_no_double_claim(self, clean_db: Engine) -> None:
        """Two concurrent open sessions cannot claim the same paper.

        s1 holds an uncommitted FOR UPDATE lock on the row.  s2 uses SKIP
        LOCKED so it silently skips the locked row instead of blocking.
        """
        # Arrange: insert a committed ocr_done paper.
        with Session(clean_db) as setup:
            store.upsert_papers([make_paper(status="ocr_done")], setup)
            setup.commit()

        s1 = Session(clean_db)
        s2 = Session(clean_db)
        try:
            batch1 = store.claim_batch(10, s1)   # acquires row lock
            batch2 = store.claim_batch(10, s2)   # SKIP LOCKED → 0 rows

            assert len(batch1) == 1
            assert len(batch2) == 0
        finally:
            s1.rollback()
            s1.close()
            s2.rollback()
            s2.close()


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    def _insert_one(self, session: Session) -> Paper:
        store.upsert_papers([make_paper()], session)
        return session.scalars(select(Paper)).one()

    def test_mark_extracted_sets_status(self, db_session: Session) -> None:
        paper = self._insert_one(db_session)
        store.mark_extracted(paper.id, db_session)
        db_session.expire(paper)
        assert paper.status == "extracted"

    def test_mark_extracted_stamps_timestamp(self, db_session: Session) -> None:
        paper = self._insert_one(db_session)
        before = datetime.now(timezone.utc)
        store.mark_extracted(paper.id, db_session)
        db_session.expire(paper)
        assert paper.extracted_at is not None
        assert paper.extracted_at >= before

    def test_mark_failed_sets_status(self, db_session: Session) -> None:
        paper = self._insert_one(db_session)
        store.mark_failed(paper.id, "some error", db_session)
        db_session.expire(paper)
        assert paper.status == "failed"

    def test_mark_failed_records_error(self, db_session: Session) -> None:
        paper = self._insert_one(db_session)
        store.mark_failed(paper.id, "pdf download timeout", db_session)
        db_session.expire(paper)
        assert paper.error == "pdf download timeout"

    def test_requeue_failed_resets_to_ocr_done(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="failed"), make_paper(status="failed")], db_session)
        n = store.requeue_failed(db_session)
        assert n == 2
        statuses = {p.status for p in db_session.scalars(select(Paper)).all()}
        assert statuses == {"ocr_done"}

    def test_requeue_failed_clears_error(self, db_session: Session) -> None:
        paper_dict = make_paper(status="failed")
        store.upsert_papers([paper_dict], db_session)
        paper = db_session.scalars(select(Paper)).one()
        store.mark_failed(paper.id, "old error", db_session)
        store.requeue_failed(db_session)
        db_session.expire(paper)
        assert paper.error is None

    def test_requeue_failed_does_not_touch_other_statuses(self, db_session: Session) -> None:
        store.upsert_papers(
            [
                make_paper(status="discovered"),
                make_paper(status="extracted"),
                make_paper(status="failed"),
            ],
            db_session,
        )
        store.requeue_failed(db_session)
        statuses = sorted(
            p.status for p in db_session.scalars(select(Paper)).all()
        )
        assert statuses == ["discovered", "extracted", "ocr_done"]

    def test_requeue_returns_zero_when_nothing_failed(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="discovered")], db_session)
        assert store.requeue_failed(db_session) == 0


# ---------------------------------------------------------------------------
# relevant_papers / mark_ocr_failed_if_relevant / reset_processing_to_relevant /
# requeue_processing — scripts/wiley_download.py + worker.py support
# ---------------------------------------------------------------------------

class TestRelevantPapers:
    def test_returns_only_relevant(self, db_session: Session) -> None:
        # Separate upsert_papers() calls: a single call's rows must share
        # the same dict keys (SQLAlchemy's multi-row VALUES compiles one
        # column list for the whole batch), and only this one paper needs
        # discovered_from set.
        store.upsert_papers(
            [
                make_paper(
                    status="relevant",
                    oa_pdf_url="https://example.com/a.pdf",
                    discovered_from="wiley",
                ),
            ],
            db_session,
        )
        store.upsert_papers(
            [make_paper(status="discovered"), make_paper(status="extracted")],
            db_session,
        )
        rows = store.relevant_papers(db_session)
        assert len(rows) == 1
        _, url, discovered_from = rows[0]
        assert url == "https://example.com/a.pdf"
        assert discovered_from == "wiley"

    def test_returns_empty_when_none_relevant(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="discovered")], db_session)
        assert store.relevant_papers(db_session) == []


class TestMarkOcrFailedIfRelevant:
    def test_updates_relevant_paper(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="relevant")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        updated = store.mark_ocr_failed_if_relevant(paper.id, "bad pdf", db_session)
        assert updated is True
        db_session.expire(paper)
        assert paper.status == "ocr_failed"
        assert paper.error == "bad pdf"

    def test_no_op_when_already_ocr_processing(self, db_session: Session) -> None:
        """Simulates the race scripts/wiley_download.py guards against: an
        OCR worker claimed the paper into 'ocr_processing' while the
        pre-downloader's download was in flight."""
        store.upsert_papers([make_paper(status="ocr_processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        updated = store.mark_ocr_failed_if_relevant(paper.id, "bad pdf", db_session)
        assert updated is False
        db_session.expire(paper)
        assert paper.status == "ocr_processing"
        assert paper.error is None


class TestResetProcessingToRelevant:
    def test_resets_processing_paper(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        updated = store.reset_processing_to_relevant(paper.id, db_session)
        assert updated is True
        db_session.expire(paper)
        assert paper.status == "relevant"

    def test_no_op_when_not_processing(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="failed")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        updated = store.reset_processing_to_relevant(paper.id, db_session)
        assert updated is False
        db_session.expire(paper)
        assert paper.status == "failed"


class TestRequeueProcessing:
    def test_resets_all_processing_to_ocr_done(self, db_session: Session) -> None:
        store.upsert_papers(
            [
                make_paper(status="processing"),
                make_paper(status="processing"),
                make_paper(status="extracted"),
            ],
            db_session,
        )
        n = store.requeue_processing(db_session)
        assert n == 2
        statuses = sorted(p.status for p in db_session.scalars(select(Paper)).all())
        assert statuses == ["extracted", "ocr_done", "ocr_done"]

    def test_returns_zero_when_nothing_processing(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="discovered")], db_session)
        assert store.requeue_processing(db_session) == 0


# ---------------------------------------------------------------------------
# insert_extraction
# ---------------------------------------------------------------------------

class TestInsertExtraction:
    def _paper_id(self, session: Session) -> int:
        store.upsert_papers([make_paper()], session)
        return session.scalars(select(Paper.id)).one()

    def test_inserts_extraction_row(self, db_session: Session) -> None:
        paper_id = self._paper_id(db_session)
        result = make_extraction_result()
        extraction = store.insert_extraction(paper_id, result, db_session)
        assert extraction.id is not None
        assert extraction.paper_id == paper_id
        assert extraction.schema_name == result.schema_name
        assert extraction.model_version == result.model_version

    def test_stores_data_and_provenance(self, db_session: Session) -> None:
        paper_id = self._paper_id(db_session)
        result = make_extraction_result(
            data={"value": 3.14, "units": "km"},
            provenance={"page_number": 2, "source": "table"},
        )
        extraction = store.insert_extraction(paper_id, result, db_session)
        assert extraction.data == {"value": 3.14, "units": "km"}
        assert extraction.provenance == {"page_number": 2, "source": "table"}

    def test_stores_coordinates(self, db_session: Session) -> None:
        paper_id = self._paper_id(db_session)
        result = make_extraction_result(latitude=51.5, longitude=-0.1)
        extraction = store.insert_extraction(paper_id, result, db_session)
        assert extraction.latitude == pytest.approx(51.5)
        assert extraction.longitude == pytest.approx(-0.1)

    def test_multiple_versions_accumulate(self, db_session: Session) -> None:
        """Re-running with a new model version adds rows, never overwrites."""
        paper_id = self._paper_id(db_session)
        store.insert_extraction(paper_id, make_extraction_result(model_version="v1"), db_session)
        store.insert_extraction(paper_id, make_extraction_result(model_version="v2"), db_session)

        rows = db_session.scalars(
            select(Extraction).where(Extraction.paper_id == paper_id)
        ).all()
        assert len(rows) == 2
        assert {r.model_version for r in rows} == {"v1", "v2"}

    def test_multiple_extractions_same_paper(self, db_session: Session) -> None:
        """Multiple measurements from the same paper run all get stored."""
        paper_id = self._paper_id(db_session)
        for _ in range(3):
            store.insert_extraction(paper_id, make_extraction_result(), db_session)
        count = db_session.scalar(
            select(func.count(Extraction.id)).where(Extraction.paper_id == paper_id)
        )
        assert count == 3

    def test_page_number_and_matched_default_to_none(self, db_session: Session) -> None:
        """Call sites that don't compute find_snippet() (e.g. this test file
        itself) must keep working — the columns are nullable."""
        paper_id = self._paper_id(db_session)
        extraction = store.insert_extraction(paper_id, make_extraction_result(), db_session)
        assert extraction.page_number is None
        assert extraction.page_matched is None

    def test_stores_page_number_and_matched(self, db_session: Session) -> None:
        paper_id = self._paper_id(db_session)
        extraction = store.insert_extraction(
            paper_id, make_extraction_result(), db_session, page_number=7, page_matched=True
        )
        assert extraction.page_number == 7
        assert extraction.page_matched is True


# ---------------------------------------------------------------------------
# Watermarks
# ---------------------------------------------------------------------------

class TestWatermark:
    def test_get_returns_none_before_set(self, db_session: Session) -> None:
        assert store.get_watermark("openalex", db_session) is None

    def test_set_and_get_roundtrip(self, db_session: Session) -> None:
        d = date(2024, 6, 1)
        store.set_watermark("openalex", d, db_session)
        assert store.get_watermark("openalex", db_session) == d

    def test_set_advances_watermark(self, db_session: Session) -> None:
        store.set_watermark("openalex", date(2024, 1, 1), db_session)
        store.set_watermark("openalex", date(2024, 6, 1), db_session)
        assert store.get_watermark("openalex", db_session) == date(2024, 6, 1)

    def test_set_is_idempotent(self, db_session: Session) -> None:
        d = date(2024, 3, 15)
        store.set_watermark("semantic_scholar", d, db_session)
        store.set_watermark("semantic_scholar", d, db_session)
        assert store.get_watermark("semantic_scholar", db_session) == d

    def test_sources_are_independent(self, db_session: Session) -> None:
        store.set_watermark("openalex", date(2024, 1, 1), db_session)
        store.set_watermark("semantic_scholar", date(2024, 6, 1), db_session)
        assert store.get_watermark("openalex", db_session) == date(2024, 1, 1)
        assert store.get_watermark("semantic_scholar", db_session) == date(2024, 6, 1)
        assert store.get_watermark("wiley", db_session) is None


# ---------------------------------------------------------------------------
# reset_extractions
# ---------------------------------------------------------------------------

class TestResetExtractions:
    def test_deletes_all_extraction_rows(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="extracted")], db_session)
        paper_id = db_session.scalars(select(Paper.id)).one()
        store.insert_extraction(paper_id, make_extraction_result(), db_session)
        store.insert_extraction(paper_id, make_extraction_result(), db_session)
        deleted, _ = store.reset_extractions(db_session)
        assert deleted == 2
        assert db_session.scalar(select(func.count(Extraction.id))) == 0

    def test_resets_extracted_processing_failed_to_ocr_done(self, db_session: Session) -> None:
        store.upsert_papers(
            [
                make_paper(status="extracted"),
                make_paper(status="processing"),
                make_paper(status="failed"),
                make_paper(status="relevant"),
                make_paper(status="ocr_done"),
            ],
            db_session,
        )
        _, reset = store.reset_extractions(db_session)
        assert reset == 3
        statuses = sorted(p.status for p in db_session.scalars(select(Paper)).all())
        assert statuses == ["ocr_done", "ocr_done", "ocr_done", "ocr_done", "relevant"]

    def test_clears_extracted_at_and_error(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="processing")], db_session)
        paper = db_session.scalars(select(Paper)).one()
        store.mark_extracted(paper.id, db_session)
        store.mark_failed(paper.id, "boom", db_session)
        store.reset_extractions(db_session)
        db_session.expire(paper)
        assert paper.status == "ocr_done"
        assert paper.extracted_at is None
        assert paper.error is None


# ---------------------------------------------------------------------------
# count_by_status
# ---------------------------------------------------------------------------

class TestCountByStatus:
    def test_counts_correctly(self, db_session: Session) -> None:
        store.upsert_papers(
            [
                make_paper(status="discovered"),
                make_paper(status="discovered"),
                make_paper(status="failed"),
            ],
            db_session,
        )
        counts = store.count_by_status(db_session)
        assert counts["discovered"] == 2
        assert counts["failed"] == 1
        assert counts.get("extracted", 0) == 0

    def test_empty_db_returns_empty_dict(self, db_session: Session) -> None:
        assert store.count_by_status(db_session) == {}


# ---------------------------------------------------------------------------
# list_extractions / get_extraction (results website read path)
# ---------------------------------------------------------------------------

def _make_paper_with_extractions(session: Session, extraction_datas: list[dict]) -> Paper:
    store.upsert_papers([make_paper(status="extracted")], session)
    paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
    for data in extraction_datas:
        store.insert_extraction(paper.id, make_extraction_result(data=data), session)
    return paper


def _make_paper_with_extractions_titled(
    session: Session, title: str, extraction_datas: list[dict]
) -> Paper:
    store.upsert_papers([make_paper(status="extracted", title=title)], session)
    paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
    for data in extraction_datas:
        store.insert_extraction(paper.id, make_extraction_result(data=data), session)
    return paper


class TestListExtractions:
    def test_returns_rows_with_paper_metadata(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "28.4", "units": "PSU"}]
        )
        rows, total = store.list_extractions(db_session)
        assert total == 1
        assert rows[0].attribute == "salinity"
        assert rows[0].value == "28.4"
        assert rows[0].title == paper.title
        assert rows[0].doi == paper.doi

    def test_excludes_context_key(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session,
            [{"attribute": "salinity", "value": "28.4", "units": "PSU", "context": "huge OCR blob"}],
        )
        rows, _ = store.list_extractions(db_session)
        assert not hasattr(rows[0], "context")

    def test_filters_by_attribute(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session,
            [
                {"attribute": "salinity", "value": "1", "units": None},
                {"attribute": "nitrate", "value": "2", "units": None},
            ],
        )
        rows, total = store.list_extractions(db_session, attribute="nitrate")
        assert total == 1
        assert rows[0].attribute == "nitrate"

    def test_dedupes_repeated_extraction_passes(self, db_session: Session) -> None:
        """Re-extraction accumulates rows (insert_extraction never overwrites);
        the list view must collapse identical (paper, attribute, value, units)
        measurements down to the latest row rather than showing duplicates."""
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "28.4", "units": "PSU"}]
        )
        second = store.insert_extraction(
            paper.id,
            make_extraction_result(data={"attribute": "salinity", "value": "28.4", "units": "PSU"}),
            db_session,
        )
        rows, total = store.list_extractions(db_session)
        assert total == 1
        assert rows[0].id == second.id  # keeps the newer row

    def test_pagination(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session,
            [{"attribute": "salinity", "value": str(i), "units": None} for i in range(5)],
        )
        rows, total = store.list_extractions(db_session, page=1, page_size=2)
        assert total == 5
        assert len(rows) == 2

    def test_filters_by_paper_id(self, db_session: Session) -> None:
        paper_a = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "1", "units": None}]
        )
        _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "2", "units": None}]
        )
        rows, total = store.list_extractions(db_session, paper_id=paper_a.id)
        assert total == 1
        assert rows[0].paper_id == paper_a.id

    def test_paper_id_filter_still_dedupes(self, db_session: Session) -> None:
        """A single paper's page is exactly where a re-extraction pass's
        duplicate rows would be most visible — confirm dedup still applies."""
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "28.4", "units": "PSU"}]
        )
        second = store.insert_extraction(
            paper.id,
            make_extraction_result(data={"attribute": "salinity", "value": "28.4", "units": "PSU"}),
            db_session,
        )
        rows, total = store.list_extractions(db_session, paper_id=paper.id)
        assert total == 1
        assert rows[0].id == second.id

    def test_filters_by_title(self, db_session: Session) -> None:
        uid = _uid()
        matching = _make_paper_with_extractions_titled(
            db_session, f"Nutrient Cycling in {uid} Marshes",
            [{"attribute": "salinity", "value": "1", "units": None}],
        )
        _make_paper_with_extractions_titled(
            db_session, f"Unrelated Paper {_uid()}",
            [{"attribute": "salinity", "value": "2", "units": None}],
        )
        rows, total = store.list_extractions(db_session, title=uid)
        assert total == 1
        assert rows[0].paper_id == matching.id

    def test_title_combines_with_attribute_and_ecosystem_type(self, db_session: Session) -> None:
        uid = _uid()
        _make_paper_with_extractions_titled(
            db_session, f"{uid} Salt Marsh Study",
            [
                {"attribute": "salinity", "value": "1", "units": None, "ecosystem_type": "salt_marsh"},
                {"attribute": "nitrate", "value": "2", "units": None, "ecosystem_type": "salt_marsh"},
            ],
        )
        rows, total = store.list_extractions(
            db_session, title=uid, attribute="salinity", ecosystem_type="salt_marsh"
        )
        assert total == 1
        assert rows[0].attribute == "salinity"

    def test_hydrates_entity_and_event_fields(self, db_session: Session) -> None:
        """CSV export needs these six fields alongside the ones already
        hydrated — see notes/coastal-crawler/builds/2026-08-04-csv-export-01.md."""
        _make_paper_with_extractions(
            db_session,
            [{
                "attribute": "salinity", "value": "28.4", "units": "PSU",
                "name": "Site A", "identifiers": "SITE-1",
                "ecosystem_type": "estuary", "location": "Bay X",
                "latitude": 41.5, "longitude": -70.6,
                "date": "2020", "sub_location": "T1",
                "additional_details": "high tide",
            }],
        )
        rows, _ = store.list_extractions(db_session)
        r = rows[0]
        assert r.identifiers == "SITE-1"
        assert r.location == "Bay X"
        assert r.latitude == "41.5"
        assert r.longitude == "-70.6"
        assert r.sub_location == "T1"
        assert r.additional_details == "high tide"


class TestListPapersWithExtractions:
    def test_returns_extraction_count_and_last_extracted(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session,
            [
                {"attribute": "salinity", "value": "1", "units": None},
                {"attribute": "nitrate", "value": "2", "units": None},
            ],
        )
        rows, total = store.list_papers_with_extractions(db_session)
        assert total == 1
        assert rows[0].id == paper.id
        assert rows[0].extraction_count == 2
        assert rows[0].last_extracted is not None

    def test_orders_by_last_extracted_desc(self, db_session: Session) -> None:
        older = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "1", "units": None}]
        )
        newer = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "2", "units": None}]
        )
        # created_at's server_default=func.now() returns the *transaction*
        # start time in Postgres, not per-statement wall-clock time — inside
        # one uncommitted db_session transaction every row gets the same
        # timestamp, so real insert order can't be relied on here. Set
        # explicit, distinct values instead.
        now = datetime.now(timezone.utc)
        db_session.execute(
            update(Extraction)
            .where(Extraction.paper_id == older.id)
            .values(created_at=now - timedelta(hours=1))
        )
        db_session.execute(
            update(Extraction).where(Extraction.paper_id == newer.id).values(created_at=now)
        )
        db_session.flush()
        rows, _total = store.list_papers_with_extractions(db_session)
        assert [r.id for r in rows] == [newer.id, older.id]

    def test_dedupes_repeated_extraction_passes(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "28.4", "units": "PSU"}]
        )
        store.insert_extraction(
            paper.id,
            make_extraction_result(data={"attribute": "salinity", "value": "28.4", "units": "PSU"}),
            db_session,
        )
        rows, total = store.list_papers_with_extractions(db_session)
        assert total == 1
        assert rows[0].extraction_count == 1  # not 2 — same measurement re-extracted

    def test_filters_by_attribute_excludes_non_matching_papers(self, db_session: Session) -> None:
        matching = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "1", "units": None}]
        )
        _make_paper_with_extractions(
            db_session, [{"attribute": "nitrate", "value": "2", "units": None}]
        )
        rows, total = store.list_papers_with_extractions(db_session, attribute="salinity")
        assert total == 1
        assert rows[0].id == matching.id

    def test_filters_by_ecosystem_type(self, db_session: Session) -> None:
        matching = _make_paper_with_extractions(
            db_session,
            [{"attribute": "salinity", "value": "1", "units": None, "ecosystem_type": "reef"}],
        )
        _make_paper_with_extractions(
            db_session,
            [{"attribute": "salinity", "value": "2", "units": None, "ecosystem_type": "marsh"}],
        )
        rows, total = store.list_papers_with_extractions(db_session, ecosystem_type="reef")
        assert total == 1
        assert rows[0].id == matching.id

    def test_filters_by_title(self, db_session: Session) -> None:
        uid = _uid()
        matching = _make_paper_with_extractions_titled(
            db_session, f"Nutrient Cycling in {uid} Marshes",
            [{"attribute": "salinity", "value": "1", "units": None}],
        )
        _make_paper_with_extractions_titled(
            db_session, f"Unrelated Paper {_uid()}",
            [{"attribute": "salinity", "value": "2", "units": None}],
        )
        rows, total = store.list_papers_with_extractions(db_session, title=uid)
        assert total == 1
        assert rows[0].id == matching.id

    def test_pagination(self, db_session: Session) -> None:
        for _ in range(5):
            _make_paper_with_extractions(
                db_session, [{"attribute": "salinity", "value": "1", "units": None}]
            )
        rows, total = store.list_papers_with_extractions(db_session, page=1, page_size=2)
        assert total == 5
        assert len(rows) == 2

    def test_no_matching_extractions_returns_empty(self, db_session: Session) -> None:
        rows, total = store.list_papers_with_extractions(db_session, attribute="nonexistent")
        assert rows == []
        assert total == 0


class TestPagesForPaper:
    def _insert(
        self, session: Session, paper_id: int, page_number: int | None, **data_kwargs
    ) -> Extraction:
        data = {"attribute": "salinity", "value": _uid(), "units": "PSU", **data_kwargs}
        return store.insert_extraction(
            paper_id,
            make_extraction_result(data=data),
            session,
            page_number=page_number,
            page_matched=True,
        )

    def test_groups_and_counts_by_page(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1)
        self._insert(db_session, paper.id, page_number=1)
        self._insert(db_session, paper.id, page_number=2)
        pages = store.pages_for_paper(db_session, paper.id)
        assert pages == [(1, 2), (2, 1)]

    def test_null_page_number_sorts_last(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=None)
        self._insert(db_session, paper.id, page_number=1)
        pages = store.pages_for_paper(db_session, paper.id)
        assert pages == [(1, 1), (None, 1)]

    def test_dedupes_repeated_extraction_pass(self, db_session: Session) -> None:
        """Same (attribute, value, units) re-extracted must not double-count
        its page — mirrors _extraction_rows' dedup, see
        test_dedupes_repeated_extraction_passes above."""
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1, attribute="salinity", value="28.4")
        self._insert(db_session, paper.id, page_number=1, attribute="salinity", value="28.4")
        pages = store.pages_for_paper(db_session, paper.id)
        assert pages == [(1, 1)]

    def test_scoped_to_one_paper(self, db_session: Session) -> None:
        paper_a = _make_paper_with_extractions(db_session, [])
        paper_b = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper_a.id, page_number=1)
        self._insert(db_session, paper_b.id, page_number=1)
        self._insert(db_session, paper_b.id, page_number=2)
        assert store.pages_for_paper(db_session, paper_a.id) == [(1, 1)]
        assert store.pages_for_paper(db_session, paper_b.id) == [(1, 1), (2, 1)]

    def test_filters_by_attribute(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1, attribute="salinity")
        self._insert(db_session, paper.id, page_number=2, attribute="nitrate")
        assert store.pages_for_paper(db_session, paper.id, attribute="nitrate") == [(2, 1)]

    def test_filters_by_ecosystem_type(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1, ecosystem_type="salt_marsh")
        self._insert(db_session, paper.id, page_number=2, ecosystem_type="estuary")
        assert store.pages_for_paper(
            db_session, paper.id, ecosystem_type="estuary"
        ) == [(2, 1)]

    def test_empty_paper_returns_empty_list(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        assert store.pages_for_paper(db_session, paper.id) == []


class TestPageExtractions:
    def _insert(
        self, session: Session, paper_id: int, page_number: int | None, **data_kwargs
    ) -> Extraction:
        data = {"attribute": "salinity", "value": _uid(), "units": "PSU", **data_kwargs}
        return store.insert_extraction(
            paper_id,
            make_extraction_result(data=data),
            session,
            page_number=page_number,
            page_matched=True,
        )

    def test_returns_only_rows_on_requested_page(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1)
        self._insert(db_session, paper.id, page_number=2)
        rows = store.page_extractions(db_session, paper.id, 1)
        assert len(rows) == 1

    def test_row_count_matches_pages_for_paper_count(self, db_session: Session) -> None:
        """The critical invariant: pages_for_paper's per-page count and
        page_extractions' row count for that same page must agree, even
        when a paper has been re-extracted (duplicate rows) — both dedupe
        across the whole paper first, then narrow to the page, not the
        other way around (see _extraction_rows' docstring)."""
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1, attribute="salinity", value="28.4")
        # Re-extraction pass: same (attribute, value, units) on the same page.
        self._insert(db_session, paper.id, page_number=1, attribute="salinity", value="28.4")
        self._insert(db_session, paper.id, page_number=1, attribute="nitrate", value="5.0")

        pages = dict(store.pages_for_paper(db_session, paper.id))
        rows = store.page_extractions(db_session, paper.id, 1)
        assert pages[1] == len(rows) == 2

    def test_filters_by_attribute(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1, attribute="salinity")
        self._insert(db_session, paper.id, page_number=1, attribute="nitrate")
        rows = store.page_extractions(db_session, paper.id, 1, attribute="nitrate")
        assert len(rows) == 1
        assert rows[0].attribute == "nitrate"

    def test_filters_by_ecosystem_type(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1, ecosystem_type="salt_marsh")
        self._insert(db_session, paper.id, page_number=1, ecosystem_type="estuary")
        rows = store.page_extractions(db_session, paper.id, 1, ecosystem_type="estuary")
        assert len(rows) == 1

    def test_scoped_to_one_paper(self, db_session: Session) -> None:
        paper_a = _make_paper_with_extractions(db_session, [])
        paper_b = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper_a.id, page_number=1)
        self._insert(db_session, paper_b.id, page_number=1)
        rows = store.page_extractions(db_session, paper_a.id, 1)
        assert len(rows) == 1
        assert rows[0].paper_id == paper_a.id

    def test_no_matches_returns_empty(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [])
        self._insert(db_session, paper.id, page_number=1)
        assert store.page_extractions(db_session, paper.id, 99) == []


class TestExportExtractions:
    def test_returns_all_matching_rows_unpaginated(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session,
            [{"attribute": "salinity", "value": str(i), "units": None} for i in range(30)],
        )
        rows = store.export_extractions(db_session)
        assert len(rows) == 30

    def test_dedupes_like_list_extractions(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "28.4", "units": "PSU"}]
        )
        second = store.insert_extraction(
            paper.id,
            make_extraction_result(data={"attribute": "salinity", "value": "28.4", "units": "PSU"}),
            db_session,
        )
        rows = store.export_extractions(db_session)
        assert len(rows) == 1
        assert rows[0].id == second.id

    def test_filters_by_title_attribute_ecosystem_type(self, db_session: Session) -> None:
        uid = _uid()
        paper = _make_paper_with_extractions_titled(
            db_session, f"{uid} Coastal Study",
            [{"attribute": "salinity", "value": "1", "units": None, "ecosystem_type": "reef"}],
        )
        _make_paper_with_extractions_titled(
            db_session, f"Other {uid} Paper",
            [{"attribute": "nitrate", "value": "2", "units": None, "ecosystem_type": "marsh"}],
        )
        rows = store.export_extractions(
            db_session, title=uid, attribute="salinity", ecosystem_type="reef"
        )
        assert len(rows) == 1
        assert rows[0].paper_id == paper.id

    def test_row_includes_all_csv_fields(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session,
            [{
                "attribute": "salinity", "value": "28.4", "units": "PSU",
                "name": "Site A", "identifiers": "SITE-1",
                "ecosystem_type": "estuary", "location": "Bay X",
                "latitude": 41.5, "longitude": -70.6,
                "date": "2020", "sub_location": "T1",
                "additional_details": "high tide",
            }],
        )
        rows = store.export_extractions(db_session)
        r = rows[0]
        for field in (
            "attribute", "value", "units", "entity_name", "identifiers",
            "ecosystem_type", "location", "latitude", "longitude",
            "event_date", "sub_location", "additional_details",
            "judgement", "confidence", "title", "doi", "authors",
            "publication_date",
        ):
            assert hasattr(r, field)


class TestGetExtraction:
    def test_returns_extraction_with_paper_and_context(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "28.4", "context": "the doc text"}]
        )
        extraction = db_session.scalars(select(Extraction)).one()
        result = store.get_extraction(db_session, extraction.id)
        assert result is not None
        assert result.paper.id == paper.id
        assert result.data["context"] == "the doc text"

    def test_missing_id_returns_none(self, db_session: Session) -> None:
        assert store.get_extraction(db_session, 999999) is None


class TestGetPaper:
    def test_returns_paper_by_id(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "1", "units": None}]
        )
        result = store.get_paper(db_session, paper.id)
        assert result is not None
        assert result.id == paper.id

    def test_missing_id_returns_none(self, db_session: Session) -> None:
        assert store.get_paper(db_session, 999999) is None


class TestSearchPapersByTitle:
    def test_matches_substring_case_insensitive(self, db_session: Session) -> None:
        uid = _uid()
        paper = _make_paper_with_extractions_titled(
            db_session, f"Nutrient Cycling in {uid} Salt Marshes",
            [{"attribute": "salinity", "value": "1", "units": None}],
        )
        rows = store.search_papers_by_title(db_session, uid.lower(), limit=10)
        assert [r.id for r in rows] == [paper.id]

    def test_percent_and_underscore_are_literal_not_wildcards(self, db_session: Session) -> None:
        uid = _uid()
        # LIKE treats "_" as "match any one character" unless escaped — without
        # autoescape, searching "Foo_Bar" would also match "FooXBar".
        match = _make_paper_with_extractions_titled(
            db_session, f"Foo_Bar {uid} Baseline",
            [{"attribute": "salinity", "value": "1", "units": None}],
        )
        _make_paper_with_extractions_titled(
            db_session, f"FooXBar {uid} Baseline",
            [{"attribute": "salinity", "value": "1", "units": None}],
        )
        rows = store.search_papers_by_title(db_session, f"Foo_Bar {uid}", limit=10)
        assert [r.id for r in rows] == [match.id]

    def test_excludes_papers_without_extractions(self, db_session: Session) -> None:
        uid = _uid()
        store.upsert_papers([make_paper(title=f"Unextracted {uid} Paper")], db_session)
        rows = store.search_papers_by_title(db_session, uid, limit=10)
        assert rows == []

    def test_prefix_match_ranked_first(self, db_session: Session) -> None:
        uid = _uid()
        prefix = _make_paper_with_extractions_titled(
            db_session, f"{uid} Coastal Erosion Study",
            [{"attribute": "salinity", "value": "1", "units": None}],
        )
        substring = _make_paper_with_extractions_titled(
            db_session, f"A Study on {uid} Coastal Erosion",
            [{"attribute": "salinity", "value": "1", "units": None}],
        )
        rows = store.search_papers_by_title(db_session, uid, limit=10)
        assert [r.id for r in rows] == [prefix.id, substring.id]

    def test_limit_respected(self, db_session: Session) -> None:
        uid = _uid()
        for i in range(3):
            _make_paper_with_extractions_titled(
                db_session, f"{uid} Paper {i}",
                [{"attribute": "salinity", "value": "1", "units": None}],
            )
        rows = store.search_papers_by_title(db_session, uid, limit=2)
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# paper_ocr_context
# ---------------------------------------------------------------------------

class TestPaperOcrContext:
    def test_roundtrip(self, db_session: Session) -> None:
        store.upsert_papers([make_paper()], db_session)
        paper = db_session.scalars(select(Paper).order_by(Paper.id.desc())).first()
        store.upsert_paper_ocr_context(paper.id, "the full ocr text", db_session)
        assert store.get_paper_ocr_context(db_session, paper.id) == "the full ocr text"

    def test_missing_paper_returns_none(self, db_session: Session) -> None:
        assert store.get_paper_ocr_context(db_session, 999999) is None

    def test_upsert_overwrites_existing_context(self, db_session: Session) -> None:
        """A re-extraction pass overwrites rather than duplicating rows —
        `paper_ocr_context` holds one row per paper, not one per pass."""
        store.upsert_papers([make_paper()], db_session)
        paper = db_session.scalars(select(Paper).order_by(Paper.id.desc())).first()
        store.upsert_paper_ocr_context(paper.id, "first pass text", db_session)
        store.upsert_paper_ocr_context(paper.id, "second pass text", db_session)
        assert store.get_paper_ocr_context(db_session, paper.id) == "second pass text"


# ---------------------------------------------------------------------------
# record_vote
# ---------------------------------------------------------------------------

class TestRecordVote:
    def test_single_valid_vote(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        judgement = store.record_vote(db_session, extraction.id, True, "hash1")
        assert judgement == "valid"
        db_session.expire(extraction)
        assert extraction.judgement == "valid"

    def test_majority_wins(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        store.record_vote(db_session, extraction.id, True, "hash1")
        store.record_vote(db_session, extraction.id, True, "hash2")
        judgement = store.record_vote(db_session, extraction.id, False, "hash3")
        assert judgement == "valid"

    def test_tie_leaves_judgement_unresolved(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        store.record_vote(db_session, extraction.id, True, "hash1")
        judgement = store.record_vote(db_session, extraction.id, False, "hash2")
        assert judgement is None

    def test_records_individual_vote_rows(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        store.record_vote(db_session, extraction.id, True, "hash1")
        votes = db_session.scalars(select(Vote).where(Vote.extraction_id == extraction.id)).all()
        assert len(votes) == 1
        assert votes[0].vote is True
        assert votes[0].voter_hash == "hash1"


class TestLocationMajorityEcosystemType:
    def _assign_location(self, session: Session, paper: Paper, **location_kwargs) -> Location:
        location = Location(resolution_method="coordinate", **location_kwargs)
        session.add(location)
        session.flush()
        for extraction in paper.extractions:
            extraction.location_id = location.id
        session.flush()
        return location

    def test_majority_is_raw_row_count_not_deduped(self, db_session: Session) -> None:
        """Two 'marsh' rows should beat one 'mangrove' row even though
        that's a raw row count, not a dedup by (paper_id, attribute, value,
        units) — a deliberate choice, see location_majority_ecosystem_type's
        docstring."""
        paper = _make_paper_with_extractions(
            db_session,
            [
                {"attribute": "salinity", "value": "1", "ecosystem_type": "marsh"},
                {"attribute": "turbidity", "value": "2", "ecosystem_type": "marsh"},
                {"attribute": "salinity", "value": "3", "ecosystem_type": "mangrove"},
            ],
        )
        location = self._assign_location(db_session, paper)

        rows = store.location_majority_ecosystem_type(db_session)

        assert rows == [(location.id, "marsh", 2)]

    def test_ties_broken_by_ecosystem_type_ascending(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session,
            [
                {"attribute": "salinity", "value": "1", "ecosystem_type": "zeta"},
                {"attribute": "salinity", "value": "2", "ecosystem_type": "alpha"},
            ],
        )
        location = self._assign_location(db_session, paper)

        rows = store.location_majority_ecosystem_type(db_session)

        assert rows == [(location.id, "alpha", 1)]

    def test_null_ecosystem_type_excluded(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        self._assign_location(db_session, paper)

        rows = store.location_majority_ecosystem_type(db_session)

        assert rows == []

    def test_extractions_without_location_excluded(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "1", "ecosystem_type": "marsh"}]
        )

        rows = store.location_majority_ecosystem_type(db_session)

        assert rows == []
