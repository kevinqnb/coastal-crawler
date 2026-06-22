"""Tests for the storage layer (db/store.py).

Most tests use ``db_session`` (rolled back after each test, no committed state).
The SKIP LOCKED test uses ``clean_db`` (commits real transactions, truncates after).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.adapter import ExtractionResult
from coastal_crawler.db import store
from coastal_crawler.db.models import Extraction, Paper


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
# claim_batch
# ---------------------------------------------------------------------------

class TestClaimBatch:
    def test_claims_discovered_papers(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(), make_paper()], db_session)
        claimed = store.claim_batch(10, db_session)
        assert len(claimed) == 2

    def test_sets_status_to_processing(self, db_session: Session) -> None:
        store.upsert_papers([make_paper()], db_session)
        claimed = store.claim_batch(10, db_session)
        assert all(p.status == "processing" for p in claimed)

    def test_respects_batch_size(self, db_session: Session) -> None:
        store.upsert_papers([make_paper() for _ in range(5)], db_session)
        claimed = store.claim_batch(3, db_session)
        assert len(claimed) == 3

    def test_returns_empty_when_nothing_discovered(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="extracted")], db_session)
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
        # Arrange: insert a committed discovered paper.
        with Session(clean_db) as setup:
            store.upsert_papers([make_paper()], setup)
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

    def test_requeue_failed_resets_to_discovered(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="failed"), make_paper(status="failed")], db_session)
        n = store.requeue_failed(db_session)
        assert n == 2
        statuses = {p.status for p in db_session.scalars(select(Paper)).all()}
        assert statuses == {"discovered"}

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
        assert statuses == ["discovered", "discovered", "extracted"]

    def test_requeue_returns_zero_when_nothing_failed(self, db_session: Session) -> None:
        store.upsert_papers([make_paper(status="discovered")], db_session)
        assert store.requeue_failed(db_session) == 0


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
