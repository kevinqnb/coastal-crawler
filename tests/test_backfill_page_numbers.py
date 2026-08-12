"""Tests for scripts/backfill_page_numbers.py.

`scripts/` isn't a package (no __init__.py, not installed) — the module is
loaded directly from its file path via importlib, same as any other
standalone script would need to be to unit test it.

Uses ``clean_db`` (not ``db_session``): the script commits one real
transaction per row via its own ``get_session()`` calls, patched here to the
test engine the same way ``worker_db`` patches worker.py's in test_worker.py.
"""

from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.db import store
from coastal_crawler.db.models import Extraction, Paper

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill_page_numbers.py"
_spec = importlib.util.spec_from_file_location("backfill_page_numbers", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
backfill_page_numbers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_page_numbers)


def make_paper(**kwargs: Any) -> dict[str, Any]:
    import uuid

    uid = str(uuid.uuid4())[:8]
    return {
        "doi": f"10.1/{uid}",
        "openalex_id": f"W{uid}",
        "semantic_scholar_id": None,
        "title": f"Test Paper {uid}",
        "oa_pdf_url": None,
        "metadata": {},
        "status": "extracted",
        **kwargs,
    }


@pytest.fixture
def backfill_db(clean_db: Engine, mocker: Any) -> Engine:
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

    mocker.patch.object(backfill_page_numbers, "get_session", _test_get_session)
    return clean_db


def _make_paper(engine: Engine) -> int:
    with Session(engine) as s:
        store.upsert_papers([make_paper()], s)
        s.commit()
        return s.scalars(select(Paper.id)).one()


def _insert_extraction_no_page(engine: Engine, paper_id: int, data: dict[str, Any]) -> int:
    """Insert an extraction row with page_number/page_matched left NULL —
    simulating a pre-migration row the backfill needs to fill in."""
    with Session(engine) as s:
        ext = Extraction(
            paper_id=paper_id,
            schema_name="test_schema",
            model_version="v1",
            data=data,
            confidence=0.9,
            provenance={},
        )
        s.add(ext)
        s.commit()
        return ext.id


def _extraction(engine: Engine, extraction_id: int) -> Extraction:
    with Session(engine) as s:
        return s.get(Extraction, extraction_id)


class TestBackfillPageNumbers:
    def test_matched_value_backfills_page_number(self, backfill_db: Engine) -> None:
        paper_id = _make_paper(backfill_db)
        with Session(backfill_db) as s:
            store.upsert_paper_ocr_context(
                paper_id,
                '<page number="1">intro</page><page number="2">depth 12.5 m recorded</page>',
                s,
            )
            s.commit()
        ext_id = _insert_extraction_no_page(
            backfill_db, paper_id, {"value": "12.5", "attribute": "depth", "units": "m"}
        )

        backfill_page_numbers.main(dry_run=False)

        ext = _extraction(backfill_db, ext_id)
        assert ext.page_number == 2
        assert ext.page_matched is True

    def test_fallback_path_sets_matched_false(self, backfill_db: Engine) -> None:
        """A value that appears nowhere in the OCR text still gets a page
        (find_snippet()'s first-page fallback) but with matched=False —
        exercising the same fallback path detail_view's live call hits."""
        paper_id = _make_paper(backfill_db)
        with Session(backfill_db) as s:
            store.upsert_paper_ocr_context(
                paper_id, '<page number="1">unrelated text only</page>', s
            )
            s.commit()
        ext_id = _insert_extraction_no_page(
            backfill_db, paper_id, {"value": "999.9", "attribute": "nitrogen", "units": "mg/L"}
        )

        backfill_page_numbers.main(dry_run=False)

        ext = _extraction(backfill_db, ext_id)
        assert ext.page_number == 1
        assert ext.page_matched is False

    def test_no_ocr_context_anywhere_sets_none_not_skipped(self, backfill_db: Engine) -> None:
        """No paper_ocr_context row and no embedded data['context'] — still
        gets updated (page_number=None, page_matched=False), not left NULL
        forever as if silently skipped."""
        paper_id = _make_paper(backfill_db)
        ext_id = _insert_extraction_no_page(
            backfill_db, paper_id, {"value": "1.0", "attribute": "x", "units": "m"}
        )

        backfill_page_numbers.main(dry_run=False)

        ext = _extraction(backfill_db, ext_id)
        assert ext.page_number is None
        assert ext.page_matched is False

    def test_falls_back_to_embedded_context_when_no_paper_ocr_context_row(
        self, backfill_db: Engine
    ) -> None:
        """Pre-PaperOcrContext rows embedded their OCR text in data['context']
        — the backfill must still use it, matching detail_view's fallback."""
        paper_id = _make_paper(backfill_db)
        ext_id = _insert_extraction_no_page(
            backfill_db,
            paper_id,
            {
                "value": "7.0",
                "attribute": "depth",
                "units": "m",
                "context": '<page number="3">depth 7.0 m</page>',
            },
        )

        backfill_page_numbers.main(dry_run=False)

        ext = _extraction(backfill_db, ext_id)
        assert ext.page_number == 3
        assert ext.page_matched is True

    def test_already_backfilled_rows_are_skipped(self, backfill_db: Engine) -> None:
        """page_number IS NOT NULL rows aren't touched — reruns are idempotent."""
        paper_id = _make_paper(backfill_db)
        with Session(backfill_db) as s:
            store.upsert_paper_ocr_context(
                paper_id, '<page number="5">depth 1.0 m</page>', s
            )
            ext = Extraction(
                paper_id=paper_id,
                schema_name="test_schema",
                model_version="v1",
                data={"value": "1.0", "attribute": "depth", "units": "m"},
                confidence=0.9,
                provenance={},
                page_number=99,
                page_matched=False,
            )
            s.add(ext)
            s.commit()
            ext_id = ext.id

        backfill_page_numbers.main(dry_run=False)

        ext = _extraction(backfill_db, ext_id)
        assert ext.page_number == 99  # untouched, not overwritten to 5
        assert ext.page_matched is False

    def test_dry_run_does_not_write(self, backfill_db: Engine) -> None:
        paper_id = _make_paper(backfill_db)
        with Session(backfill_db) as s:
            store.upsert_paper_ocr_context(
                paper_id, '<page number="1">depth 4.0 m</page>', s
            )
            s.commit()
        ext_id = _insert_extraction_no_page(
            backfill_db, paper_id, {"value": "4.0", "attribute": "depth", "units": "m"}
        )

        backfill_page_numbers.main(dry_run=True)

        ext = _extraction(backfill_db, ext_id)
        assert ext.page_number is None
        assert ext.page_matched is None
