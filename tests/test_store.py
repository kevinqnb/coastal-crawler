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
from coastal_crawler.db.models import Attribution, Extraction, Paper, Vote


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
# get_extraction / get_judgements (results website — vote flow only; other
# site reads moved to db/warehouse_reader.py, see
# notes/coastal-crawler/builds/2026-08-12-warehouse-site-01.md)
# ---------------------------------------------------------------------------

def _make_paper_with_extractions(session: Session, extraction_datas: list[dict]) -> Paper:
    store.upsert_papers([make_paper(status="extracted")], session)
    paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
    for data in extraction_datas:
        store.insert_extraction(paper.id, make_extraction_result(data=data), session)
    return paper


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


class TestGetJudgements:
    def test_returns_judgement_for_each_requested_id(self, db_session: Session) -> None:
        paper = _make_paper_with_extractions(
            db_session, [{"attribute": "salinity", "value": "1"}, {"attribute": "nitrate", "value": "2"}]
        )
        extractions = list(db_session.scalars(select(Extraction).where(Extraction.paper_id == paper.id)))
        store.record_vote(db_session, extractions[0].id, True, "voter-1")

        result = store.get_judgements(db_session, [e.id for e in extractions])
        assert result[extractions[0].id] == "valid"
        assert result[extractions[1].id] is None

    def test_ids_with_no_match_are_absent(self, db_session: Session) -> None:
        assert store.get_judgements(db_session, [999999]) == {}

    def test_empty_list_returns_empty_dict(self, db_session: Session) -> None:
        assert store.get_judgements(db_session, []) == {}


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


# TestLocationMajorityEcosystemType removed here — store.location_majority_ecosystem_type()
# and the `locations` table it aggregated over are removed by this build;
# majority-vote ecosystem_type is now computed once per entity at warehouse
# rebuild time (coastal_crawler.warehouse.majority_value), not queried live.
# See notes/coastal-crawler/builds/2026-08-12-warehouse-init-01.md.


# ---------------------------------------------------------------------------
# Judge / attribution (notes/coastal-crawler/builds/2026-08-18-judgement-attribution-01.md)
# ---------------------------------------------------------------------------

def _set_judge_status(session: Session, extraction_id: int, judge_status: str | None) -> None:
    session.execute(
        update(Extraction).where(Extraction.id == extraction_id).values(judge_status=judge_status)
    )


class TestClaimBatchForJudge:
    def test_claims_pending_extractions(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        claimed = store.claim_batch_for_judge(10, db_session)
        assert len(claimed) == 1

    def test_sets_judge_status_to_judging(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        claimed = store.claim_batch_for_judge(10, db_session)
        assert all(e.judge_status == "judging" for e in claimed)

    def test_respects_batch_size(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session,
            [{"attribute": "salinity", "value": str(i)} for i in range(5)],
        )
        claimed = store.claim_batch_for_judge(3, db_session)
        assert len(claimed) == 3

    def test_returns_empty_when_nothing_pending(self, db_session: Session) -> None:
        assert store.claim_batch_for_judge(10, db_session) == []

    def test_skips_null_judge_status(self, db_session: Session) -> None:
        """Pre-existing rows from before this stage existed are NULL, not
        'pending' — deliberately not backfilled (see insert_extraction)."""
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        _set_judge_status(db_session, extraction.id, None)
        assert store.claim_batch_for_judge(10, db_session) == []

    def test_skips_judging_extractions(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        _set_judge_status(db_session, extraction.id, "judging")
        assert store.claim_batch_for_judge(10, db_session) == []

    def test_skips_judged_extractions(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        _set_judge_status(db_session, extraction.id, "judged")
        assert store.claim_batch_for_judge(10, db_session) == []

    def test_skip_locked_no_double_claim(self, clean_db: Engine) -> None:
        with Session(clean_db) as setup:
            _make_paper_with_extractions(setup, [{"attribute": "salinity", "value": "1"}])
            setup.commit()

        s1 = Session(clean_db)
        s2 = Session(clean_db)
        try:
            batch1 = store.claim_batch_for_judge(10, s1)
            batch2 = store.claim_batch_for_judge(10, s2)

            assert len(batch1) == 1
            assert len(batch2) == 0
        finally:
            s1.rollback()
            s1.close()
            s2.rollback()
            s2.close()


class TestResetJudgingToPending:
    def test_resets_judging_extraction(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        _set_judge_status(db_session, extraction.id, "judging")
        assert store.reset_judging_to_pending(extraction.id, db_session) is True
        db_session.expire(extraction)
        assert extraction.judge_status == "pending"

    def test_no_op_when_not_judging(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        assert extraction.judge_status == "pending"
        assert store.reset_judging_to_pending(extraction.id, db_session) is False
        db_session.expire(extraction)
        assert extraction.judge_status == "pending"


class TestMarkJudged:
    def test_sets_judge_status_and_scores(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        store.mark_judged(extraction.id, 0.87, 0.62, db_session)
        db_session.expire(extraction)
        assert extraction.judge_status == "judged"
        assert extraction.confidence == pytest.approx(0.87)
        assert extraction.probe_score == pytest.approx(0.62)


class TestMarkJudgeFailed:
    def test_sets_judge_status_and_error(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        store.mark_judge_failed(extraction.id, "judge model timed out", db_session)
        db_session.expire(extraction)
        assert extraction.judge_status == "judge_failed"
        assert extraction.judge_error == "judge model timed out"


class TestRequeueJudgeProcessing:
    def test_resets_all_judging_to_pending(self, db_session: Session) -> None:
        _make_paper_with_extractions(
            db_session,
            [{"attribute": "salinity", "value": "1"}, {"attribute": "nitrate", "value": "2"}],
        )
        for extraction in db_session.scalars(select(Extraction)).all():
            _set_judge_status(db_session, extraction.id, "judging")
        n = store.requeue_judge_processing(db_session)
        assert n == 2
        statuses = {e.judge_status for e in db_session.scalars(select(Extraction)).all()}
        assert statuses == {"pending"}

    def test_does_not_touch_other_statuses(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        _set_judge_status(db_session, extraction.id, "judged")
        n = store.requeue_judge_processing(db_session)
        assert n == 0
        db_session.expire(extraction)
        assert extraction.judge_status == "judged"

    def test_returns_zero_when_nothing_judging(self, db_session: Session) -> None:
        assert store.requeue_judge_processing(db_session) == 0


class TestInsertAttribution:
    def test_inserts_row_with_expected_fields(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        attribution = store.insert_attribution(
            extraction.id,
            "contrastive_gradient",
            [0.1, -0.2, 0.3],
            [4, 5, 6],
            ["the", "salt", "marsh"],
            "the salt marsh snippet",
            db_session,
        )
        assert attribution.id is not None
        assert attribution.extraction_id == extraction.id
        assert attribution.method == "contrastive_gradient"
        assert attribution.scores == [0.1, -0.2, 0.3]
        assert attribution.token_indices == [4, 5, 6]
        assert attribution.tokens == ["the", "salt", "marsh"]
        assert attribution.snippet == "the salt marsh snippet"

    def test_one_row_per_method_per_extraction(self, db_session: Session) -> None:
        _make_paper_with_extractions(db_session, [{"attribute": "salinity", "value": "1"}])
        extraction = db_session.scalars(select(Extraction)).one()
        store.insert_attribution(
            extraction.id, "contrastive_gradient", [0.1], [0], ["x"], "snippet", db_session
        )
        store.insert_attribution(
            extraction.id, "probe", [0.2], [0], ["x"], "snippet", db_session
        )
        rows = db_session.scalars(
            select(Attribution).where(Attribution.extraction_id == extraction.id)
        ).all()
        assert {r.method for r in rows} == {"contrastive_gradient", "probe"}
