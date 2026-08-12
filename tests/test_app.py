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
from coastal_crawler.db.models import Location, Paper
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


def _seed_paper_with_located_extraction(
    engine: Engine, *, title: str, data: dict, location_kwargs: dict
) -> tuple[int, int]:
    """Like `_seed_paper_with_extraction`, plus a resolved `locations` row
    the extraction's `location_id` points at — for exercising
    /export.csv's canonical location columns and location-majority
    `ecosystem_type` filter end-to-end. Returns (paper_id, location_id)."""
    with Session(engine) as session:
        store.upsert_papers([make_paper(status="extracted", title=title)], session)
        paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
        extraction = store.insert_extraction(paper.id, make_extraction_result(data=data), session)
        location = Location(resolution_method="coordinate", **location_kwargs)
        session.add(location)
        session.flush()
        extraction.location_id = location.id
        session.commit()
        return paper.id, location.id


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
        _seed_paper_with_located_extraction(
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
            location_kwargs={"name": "Canonical Bay X", "latitude": 41.5, "longitude": -70.6},
        )
        resp = client.get("/export.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert resp.headers["content-disposition"] == 'attachment; filename="measurements.csv"'

        rows = list(csv.reader(io.StringIO(resp.text)))
        header, row = rows[0], rows[1]
        assert header == [
            "location_id", "location_name", "location_latitude", "location_longitude",
            "entity_name", "identifiers", "location_description",
            "attribute", "value", "units", "ecosystem_type", "date",
            "sub_location", "additional_details", "judgement", "confidence",
            "title", "authors", "doi", "publication_date",
        ]
        assert row[header.index("attribute")] == "salinity"
        assert row[header.index("value")] == "28.4"
        assert row[header.index("ecosystem_type")] == "salt_marsh"
        assert row[header.index("entity_name")] == "Site A"
        assert row[header.index("identifiers")] == "SITE-1"
        assert row[header.index("location_description")] == "Bay X"
        assert row[header.index("location_name")] == "Canonical Bay X"
        assert row[header.index("location_latitude")] == "41.5"
        assert row[header.index("location_longitude")] == "-70.6"
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

    def test_ecosystem_type_filter_uses_location_majority_not_own_row_value(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        """Row's own recorded ecosystem_type is 'mangrove', but its
        location's majority (established by other extractions at the same
        location) is 'marsh' — filtering by 'marsh' must still include it,
        filtering by 'mangrove' must not (done_when #4, end-to-end through
        an actual HTTP request)."""
        with Session(clean_db) as session:
            store.upsert_papers([make_paper(status="extracted", title="Marsh Site Paper")], session)
            paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
            location = Location(resolution_method="coordinate")
            session.add(location)
            session.flush()
            for i, ecosystem_type in enumerate(("marsh", "marsh", "mangrove")):
                extraction = store.insert_extraction(
                    paper.id,
                    make_extraction_result(
                        data={
                            "attribute": "salinity",
                            "value": str(i),
                            "units": None,
                            "ecosystem_type": ecosystem_type,
                        }
                    ),
                    session,
                )
                extraction.location_id = location.id
            session.commit()

        marsh_resp = client.get("/export.csv", params={"ecosystem_type": "marsh"})
        marsh_rows = list(csv.reader(io.StringIO(marsh_resp.text)))
        assert len(marsh_rows) == 4  # header + all 3 rows at this location

        mangrove_resp = client.get("/export.csv", params={"ecosystem_type": "mangrove"})
        mangrove_rows = list(csv.reader(io.StringIO(mangrove_resp.text)))
        assert len(mangrove_rows) == 1  # header only — location's majority is 'marsh'


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


class TestListViewMapData:
    """GET / embeds map_locations() as MAP_LOCATIONS for map.js — see
    notes/coastal-crawler/builds/2026-08-12-location-map-01.md."""

    def test_embeds_located_paper_as_map_location(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _paper_id, location_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Coastal Marsh Study",
            data={"attribute": "salinity", "value": "28.4", "units": None},
            location_kwargs={"name": "Great Bay", "latitude": 41.5, "longitude": -70.6},
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "const MAP_LOCATIONS" in resp.text
        assert '"location_id": %d' % location_id in resp.text
        assert '"location_name": "Great Bay"' in resp.text
        assert '"latitude": 41.5' in resp.text
        assert '"paper_count": 1' in resp.text

    def test_unlocated_paper_not_in_map_data(self, client: TestClient, clean_db: Engine) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Unlocated Paper",
            data={"attribute": "salinity", "value": "1", "units": None},
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "const MAP_LOCATIONS = [];" in resp.text

    def test_filter_narrows_map_data(self, client: TestClient, clean_db: Engine) -> None:
        _paper_id, matching_location_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Reef Paper",
            data={"attribute": "salinity", "value": "1", "units": None, "ecosystem_type": "reef"},
            location_kwargs={"name": "Reef Site", "latitude": 10.0, "longitude": 20.0},
        )
        _seed_paper_with_located_extraction(
            clean_db,
            title="Marsh Paper",
            data={"attribute": "salinity", "value": "2", "units": None, "ecosystem_type": "marsh"},
            location_kwargs={"name": "Marsh Site", "latitude": 30.0, "longitude": 40.0},
        )
        resp = client.get("/", params={"ecosystem_type": "reef"})
        assert resp.status_code == 200
        assert '"location_id": %d' % matching_location_id in resp.text
        assert "Marsh Site" not in resp.text


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


class TestLocationPapersRoute:
    def test_lists_papers_at_location(self, client: TestClient, clean_db: Engine) -> None:
        paper_id, location_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Great Bay Nutrient Study",
            data={"attribute": "salinity", "value": "1", "units": None},
            location_kwargs={"name": "Great Bay", "latitude": 41.5, "longitude": -70.6},
        )
        resp = client.get(f"/locations/{location_id}/papers")
        assert resp.status_code == 200
        assert "Great Bay" in resp.text
        assert f'href="/papers/{paper_id}' in resp.text

    def test_excludes_papers_at_other_locations(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _paper_id, location_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Included Paper",
            data={"attribute": "salinity", "value": "1", "units": None},
            location_kwargs={"latitude": 41.5, "longitude": -70.6},
        )
        _seed_paper_with_located_extraction(
            clean_db,
            title="Excluded Paper",
            data={"attribute": "salinity", "value": "2", "units": None},
            location_kwargs={"latitude": 42.0, "longitude": -71.0},
        )
        resp = client.get(f"/locations/{location_id}/papers")
        assert "Included Paper" in resp.text
        assert "Excluded Paper" not in resp.text

    def test_filter_narrows_papers_at_location(
        self, client: TestClient, clean_db: Engine
    ) -> None:
        _match_id, location_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Matching Paper",
            data={"attribute": "salinity", "value": "1", "units": None},
            location_kwargs={"latitude": 41.5, "longitude": -70.6},
        )
        other_paper_id, _other_location_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Non-Matching Paper",
            data={"attribute": "nitrate", "value": "2", "units": None},
            location_kwargs={"latitude": 41.5, "longitude": -70.6},
        )
        # Point the second paper's extraction at the same location so both
        # papers are candidates for /locations/{id}/papers — only the
        # attribute filter should decide which one shows.
        with Session(clean_db) as session:
            paper = session.get(Paper, other_paper_id)
            assert paper is not None
            for extraction in paper.extractions:
                extraction.location_id = location_id
            session.commit()

        resp = client.get(f"/locations/{location_id}/papers", params={"attribute": "salinity"})
        assert resp.status_code == 200
        assert "Matching Paper" in resp.text
        assert "Non-Matching Paper" not in resp.text

    def test_404_for_missing_location(self, client: TestClient, clean_db: Engine) -> None:
        resp = client.get("/locations/999999/papers")
        assert resp.status_code == 404

    def test_back_link_carries_filter(self, client: TestClient, clean_db: Engine) -> None:
        _paper_id, location_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Filtered Back Link Paper",
            data={"attribute": "salinity", "value": "1", "units": None},
            location_kwargs={"latitude": 41.5, "longitude": -70.6},
        )
        resp = client.get(f"/locations/{location_id}/papers", params={"attribute": "salinity"})
        assert resp.status_code == 200
        assert 'href="/?attribute=salinity"' in resp.text


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
