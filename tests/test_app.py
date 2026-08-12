"""Route-level tests for the results website (site/app.py).

Every other test file in this repo exercises the store layer directly; this
is the first to go through an actual HTTP response, for the one thing the
store layer can't verify on its own: response headers/content-type/body
shape (see /export.csv's Content-Disposition filename and CSV body).

Unlike ``db_session`` (rolled back, invisible to other connections), these
tests need the app's own ``get_session()`` — a separate connection — to see
committed data, so they use ``clean_db`` and monkeypatch
``coastal_crawler.db.engine``'s global engine/session-factory to point at it.
"""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.db import engine as db_engine_module
from coastal_crawler.db import store
from coastal_crawler.db.models import Paper
from coastal_crawler.site.app import app
from tests.test_store import make_extraction_result, make_paper


@pytest.fixture
def client(clean_db: Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db_engine_module, "_engine", clean_db)
    monkeypatch.setattr(db_engine_module, "_factory", None)
    return TestClient(app)


def _seed_paper_with_extraction(engine: Engine, *, title: str, data: dict) -> int:
    with Session(engine) as session:
        store.upsert_papers([make_paper(status="extracted", title=title)], session)
        paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
        store.insert_extraction(paper.id, make_extraction_result(data=data), session)
        session.commit()
        return paper.id


def _seed_paper_with_paged_extraction(
    engine: Engine,
    *,
    title: str = "Paged Paper",
    data: dict,
    page_number: int | None,
    ocr_context: str | None = None,
) -> tuple[int, int]:
    """Like `_seed_paper_with_extraction`, plus a stored `page_number` and
    (optionally) a `paper_ocr_context` row — for exercising page_view/paper_view's
    page-grouped behavior. Returns (paper_id, extraction_id)."""
    with Session(engine) as session:
        store.upsert_papers([make_paper(status="extracted", title=title)], session)
        paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
        if ocr_context is not None:
            store.upsert_paper_ocr_context(paper.id, ocr_context, session)
        extraction = store.insert_extraction(
            paper.id,
            make_extraction_result(data=data),
            session,
            page_number=page_number,
            page_matched=True,
        )
        session.commit()
        return paper.id, extraction.id


class TestExportCsvRoute:
    def test_returns_csv_with_expected_columns_and_row(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Nutrient Cycling in Salt Marshes",
            data={
                "attribute": "salinity",
                "value": "28.4",
                "units": "PSU",
                "name": "Site A",
                "identifiers": "SITE-1",
                "ecosystem_type": "salt_marsh",
                "location": "Bay X",
                "latitude": 41.5,
                "longitude": -70.6,
                "date": "2020",
                "sub_location": "T1",
                "additional_details": "high tide",
            },
        )
        resp = client.get("/export.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert resp.headers["content-disposition"] == 'attachment; filename="measurements.csv"'

        rows = list(csv.reader(io.StringIO(resp.text)))
        header, row = rows[0], rows[1]
        assert header == [
            "attribute", "value", "units", "name", "identifiers",
            "ecosystem_type", "location", "latitude", "longitude", "date",
            "sub_location", "additional_details", "judgement", "confidence",
            "title", "authors", "doi", "publication_date",
        ]
        assert row[header.index("attribute")] == "salinity"
        assert row[header.index("value")] == "28.4"
        assert row[header.index("ecosystem_type")] == "salt_marsh"
        assert row[header.index("latitude")] == "41.5"
        assert row[header.index("title")] == "Nutrient Cycling in Salt Marshes"

    def test_filename_reflects_active_filters(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Reef Study",
            data={
                "attribute": "salinity",
                "value": "1",
                "units": None,
                "ecosystem_type": "coral reef",
            },
        )
        resp = client.get(
            "/export.csv", params={"attribute": "salinity", "ecosystem_type": "coral reef"}
        )
        assert (
            resp.headers["content-disposition"]
            == 'attachment; filename="measurements_salinity_coral_reef.csv"'
        )

    def test_excludes_rows_outside_title_filter(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Match Paper",
            data={"attribute": "salinity", "value": "1", "units": None},
        )
        _seed_paper_with_extraction(
            clean_db,
            title="Other Paper",
            data={"attribute": "nitrate", "value": "2", "units": None},
        )
        resp = client.get("/export.csv", params={"title": "Match"})
        rows = list(csv.reader(io.StringIO(resp.text)))
        assert len(rows) == 2  # header + one matching data row
        assert rows[1][rows[0].index("title")] == "Match Paper"


class TestListViewTitleFilter:
    def test_title_query_param_narrows_results(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Match Paper",
            data={"attribute": "salinity", "value": "1", "units": None},
        )
        _seed_paper_with_extraction(
            clean_db,
            title="Other Paper",
            data={"attribute": "nitrate", "value": "2", "units": None},
        )
        resp = client.get("/", params={"title": "Match"})
        assert resp.status_code == 200
        assert "Match Paper" in resp.text
        assert "Other Paper" not in resp.text


class TestListViewShowsPapers:
    """GET / now lists papers, not individual measurement rows — see
    notes/coastal-crawler/builds/2026-08-11-paper-page-view-01.md."""

    def test_paper_title_links_to_paper_page(self, client: TestClient, clean_db: Engine) -> None:
        paper_id = _seed_paper_with_extraction(
            clean_db,
            title="Nutrient Cycling in Salt Marshes",
            data={"attribute": "salinity", "value": "28.4", "units": "PSU"},
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert f'href="/papers/{paper_id}' in resp.text
        assert "Nutrient Cycling in Salt Marshes" in resp.text

    def test_one_row_per_paper_not_per_measurement(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        with Session(clean_db) as session:
            store.upsert_papers([make_paper(status="extracted", title="Multi-Measurement Paper")], session)
            paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "salinity", "value": "1", "units": None}),
                session,
            )
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "nitrate", "value": "2", "units": None}),
                session,
            )
            session.commit()
        resp = client.get("/")
        assert resp.text.count("Multi-Measurement Paper") == 1
        assert "Measurements:</span> 2" in resp.text  # extraction_count, not one row per measurement


class TestPaperViewShowsPages:
    def test_lists_page_links_with_counts(self, client: TestClient, clean_db: Engine) -> None:
        paper_id, _ = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=2,
        )
        resp = client.get(f"/papers/{paper_id}")
        assert resp.status_code == 200
        assert f'href="/papers/{paper_id}/pages/2' in resp.text
        assert "page 3" in resp.text.lower()  # 0-indexed stored, displayed as +1

    def test_unknown_page_bucket_is_not_a_link(self, client: TestClient, clean_db: Engine) -> None:
        paper_id, _ = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=None,
        )
        resp = client.get(f"/papers/{paper_id}")
        assert resp.status_code == 200
        assert "unknown page" in resp.text.lower()
        assert "/pages/None" not in resp.text

    def test_404_for_missing_paper(self, client: TestClient, clean_db: Engine) -> None:
        resp = client.get("/papers/999999")
        assert resp.status_code == 404

    def test_attribute_filter_narrows_page_counts(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        with Session(clean_db) as session:
            store.upsert_papers([make_paper(status="extracted")], session)
            paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "salinity", "value": "1", "units": None}),
                session, page_number=0, page_matched=True,
            )
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "nitrate", "value": "2", "units": None}),
                session, page_number=1, page_matched=True,
            )
            session.commit()
            paper_id = paper.id
        resp = client.get(f"/papers/{paper_id}", params={"attribute": "nitrate"})
        assert resp.status_code == 200
        assert f'/papers/{paper_id}/pages/1' in resp.text
        assert f'/papers/{paper_id}/pages/0' not in resp.text


class TestPageView:
    def test_shows_ocr_text_and_measurement(self, client: TestClient, clean_db: Engine) -> None:
        paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "28.4", "units": "PSU"},
            page_number=0,
            ocr_context='<page number="0">Salinity was measured at 28.4 PSU.</page>',
        )
        resp = client.get(f"/papers/{paper_id}/pages/0")
        assert resp.status_code == 200
        assert "28.4" in resp.text
        assert "Salinity was measured" in resp.text
        assert f'action="/extraction/{extraction_id}/vote"' in resp.text

    def test_missing_ocr_text_shows_unavailable_message(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        paper_id, _ = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=0,
        )
        resp = client.get(f"/papers/{paper_id}/pages/0")
        assert resp.status_code == 200
        assert "unavailable" in resp.text.lower()

    def test_attribute_filter_excludes_non_matching_measurement(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        with Session(clean_db) as session:
            store.upsert_papers([make_paper(status="extracted")], session)
            paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "salinity", "value": "1", "units": None}),
                session, page_number=0, page_matched=True,
            )
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "nitrate", "value": "99", "units": None}),
                session, page_number=0, page_matched=True,
            )
            session.commit()
            paper_id = paper.id
        resp = client.get(f"/papers/{paper_id}/pages/0", params={"attribute": "nitrate"})
        assert "99" in resp.text
        assert "salinity" not in resp.text.lower()

    def test_404_for_missing_paper(self, client: TestClient, clean_db: Engine) -> None:
        resp = client.get("/papers/999999/pages/0")
        assert resp.status_code == 404


class TestExtractionDetailRouteRemoved:
    def test_get_extraction_detail_no_longer_exists(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=0,
        )
        resp = client.get(f"/extraction/{extraction_id}", follow_redirects=False)
        assert resp.status_code == 404


class TestCastVoteRedirect:
    def test_redirects_to_page_view_when_page_number_known(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=3,
        )
        resp = client.post(
            f"/extraction/{extraction_id}/vote", data={"vote": "valid"}, follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/papers/{paper_id}/pages/3"

    def test_redirects_to_paper_view_when_page_number_unknown(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=None,
        )
        resp = client.post(
            f"/extraction/{extraction_id}/vote", data={"vote": "valid"}, follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/papers/{paper_id}"

    def test_active_filters_carried_through_redirect(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=2,
        )
        resp = client.post(
            f"/extraction/{extraction_id}/vote",
            data={"vote": "valid", "attribute": "salinity", "ecosystem_type": "salt_marsh"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert location.startswith(f"/papers/{paper_id}/pages/2?")
        assert "attribute=salinity" in location
        assert "ecosystem_type=salt_marsh" in location

    def test_vote_is_recorded(self, client: TestClient, clean_db: Engine) -> None:
        _paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=0,
        )
        client.post(f"/extraction/{extraction_id}/vote", data={"vote": "valid"})
        with Session(clean_db) as session:
            extraction = store.get_extraction(session, extraction_id)
            assert extraction.judgement == "valid"
