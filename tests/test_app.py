"""Route-level tests for the results website (site/app.py).

Every other test file in this repo exercises the store layer directly; this
is the first to go through an actual HTTP response, for the one thing the
store layer can't verify on its own: response headers/content-type/body
shape (see /export.csv's Content-Disposition filename and CSV body).

Unlike ``db_session`` (rolled back, invisible to other connections), these
tests need the app's own ``get_session()`` — a separate connection — to see
committed data, so they use ``clean_db`` and monkeypatch
``coastal_crawler.db.engine``'s global engine/session-factory to point at it.

Paper/extraction/entity reads now come from a DuckDB warehouse snapshot
(``db/warehouse_reader.py``), not live Postgres — see
notes/coastal-crawler/builds/2026-08-12-warehouse-site-01.md. Every test
that hits a route rendering warehouse-backed data must seed Postgres *and
then* call the ``rebuild_warehouse`` fixture to produce a matching
snapshot before hitting the client — the two are separate stores now, not
one live connection. Tests that only exercise the vote flow (POST
``/extraction/{id}/vote`` and its redirect) don't need a rebuild:
``judgement`` is always read live from Postgres, never from the warehouse.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Callable

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.config import Settings
from coastal_crawler.db import engine as db_engine_module
from coastal_crawler.db import store, warehouse_reader
from coastal_crawler.db.models import Paper
from coastal_crawler.site.app import app
from tests.test_build_warehouse import build_warehouse
from tests.test_store import make_extraction_result, make_paper


def _test_settings(warehouse_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://unused/test",
        warehouse_path=str(warehouse_path),
        location_distance_threshold_km=1.0,
        location_name_similarity_threshold=0.85,
    )


@pytest.fixture
def warehouse_path(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.duckdb"


@pytest.fixture
def client(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch, warehouse_path: Path
) -> TestClient:
    monkeypatch.setattr(db_engine_module, "_engine", clean_db)
    monkeypatch.setattr(db_engine_module, "_factory", None)
    monkeypatch.setattr(warehouse_reader, "get_settings", lambda: _test_settings(warehouse_path))
    return TestClient(app)


@pytest.fixture
def rebuild_warehouse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, warehouse_path: Path
) -> Callable[[], None]:
    """Rebuild the DuckDB warehouse from whatever's currently committed in
    `clean_db`, into the same path `client`'s site reads from. Depends on
    `client` so the Postgres engine patch is already in place —
    `build_warehouse.main()`'s own `get_session()` call picks it up
    transparently (see db/engine.py: the patched globals live on the
    module, not on any particular imported reference to `get_session`)."""
    monkeypatch.setattr(build_warehouse, "get_settings", lambda: _test_settings(warehouse_path))

    def _rebuild() -> None:
        build_warehouse.main()

    return _rebuild


def _entity_id_for_paper(warehouse_path: Path, paper_id: int) -> int:
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        row = con.execute(
            "SELECT entity_id FROM extractions_fact WHERE paper_id = ? LIMIT 1", [paper_id]
        ).fetchone()
        assert row is not None
        return row[0]
    finally:
        con.close()


def _seed_paper_with_extraction(engine: Engine, *, title: str, data: dict) -> int:
    with Session(engine) as session:
        store.upsert_papers([make_paper(status="extracted", title=title)], session)
        paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
        store.insert_extraction(paper.id, make_extraction_result(data=data), session)
        session.commit()
        return paper.id


def _seed_paper_with_located_extraction(
    engine: Engine,
    *,
    title: str = "Located Paper",
    data: dict,
    latitude: float,
    longitude: float,
) -> int:
    """Like `_seed_paper_with_extraction`, plus coordinates on the
    extraction row itself — `scripts/build_warehouse.py`'s entity
    resolution reads `Extraction.latitude`/`.longitude` (typed columns),
    not `data`, to cluster rows into `entity_dim` (see
    `resolve_entities`/`warehouse.py`). Returns the paper id; the
    resulting `entity_id` is assigned at rebuild time, not known ahead of
    it — look it up via `_entity_id_for_paper` after `rebuild_warehouse()`."""
    with Session(engine) as session:
        store.upsert_papers([make_paper(status="extracted", title=title)], session)
        paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
        store.insert_extraction(
            paper.id,
            make_extraction_result(data=data, latitude=latitude, longitude=longitude),
            session,
        )
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
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        _seed_paper_with_located_extraction(
            clean_db,
            title="Nutrient Cycling in Salt Marshes",
            data={
                "attribute": "salinity",
                "value": "28.4",
                "units": "psu",
                "name": "Site A",
                "identifiers": "SITE-1",
                "ecosystem_type": "salt_marsh",
                "location": "Bay X",
                "date": "2020",
                "sub_location": "T1",
                "additional_details": "high tide",
            },
            latitude=41.5,
            longitude=-70.6,
        )
        rebuild_warehouse()
        resp = client.get("/export.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert resp.headers["content-disposition"] == 'attachment; filename="measurements.csv"'

        rows = list(csv.reader(io.StringIO(resp.text)))
        header, row = rows[0], rows[1]
        assert header == [
            "entity_id", "entity_name", "entity_latitude", "entity_longitude",
            "identifiers", "location_description",
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
        assert row[header.index("entity_latitude")] == "41.5"
        assert row[header.index("entity_longitude")] == "-70.6"
        assert row[header.index("title")] == "Nutrient Cycling in Salt Marshes"

    def test_filename_reflects_active_filters(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
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
        rebuild_warehouse()
        resp = client.get(
            "/export.csv", params={"attribute": "salinity", "ecosystem_type": "coral reef"}
        )
        assert (
            resp.headers["content-disposition"]
            == 'attachment; filename="measurements_salinity_coral_reef.csv"'
        )

    def test_excludes_rows_outside_title_filter(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Match Paper",
            data={"attribute": "salinity", "value": "1", "units": "psu"},
        )
        _seed_paper_with_extraction(
            clean_db,
            title="Other Paper",
            data={"attribute": "nitrate", "value": "2", "units": "µmol/L"},
        )
        rebuild_warehouse()
        resp = client.get("/export.csv", params={"title": "Match"})
        rows = list(csv.reader(io.StringIO(resp.text)))
        assert len(rows) == 2  # header + one matching data row
        assert rows[1][rows[0].index("title")] == "Match Paper"


class TestListViewTitleFilter:
    def test_title_query_param_narrows_results(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Match Paper",
            data={"attribute": "salinity", "value": "1", "units": "psu"},
        )
        _seed_paper_with_extraction(
            clean_db,
            title="Other Paper",
            data={"attribute": "nitrate", "value": "2", "units": "µmol/L"},
        )
        rebuild_warehouse()
        resp = client.get("/", params={"title": "Match"})
        assert resp.status_code == 200
        assert "Match Paper" in resp.text
        assert "Other Paper" not in resp.text


class TestListViewShowsPapers:
    """GET / now lists papers, not individual measurement rows — see
    notes/coastal-crawler/builds/2026-08-11-paper-page-view-01.md."""

    def test_paper_title_links_to_paper_page(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        paper_id = _seed_paper_with_extraction(
            clean_db,
            title="Nutrient Cycling in Salt Marshes",
            data={"attribute": "salinity", "value": "28.4", "units": "PSU"},
        )
        rebuild_warehouse()
        resp = client.get("/")
        assert resp.status_code == 200
        assert f'href="/papers/{paper_id}' in resp.text
        assert "Nutrient Cycling in Salt Marshes" in resp.text

    def test_one_row_per_paper_not_per_measurement(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        with Session(clean_db) as session:
            store.upsert_papers([make_paper(status="extracted", title="Multi-Measurement Paper")], session)
            paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "salinity", "value": "1", "units": "psu"}),
                session,
            )
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "nitrate", "value": "2", "units": "µmol/L"}),
                session,
            )
            session.commit()
        rebuild_warehouse()
        resp = client.get("/")
        assert resp.text.count("Multi-Measurement Paper") == 1
        assert "Measurements:</span> 2" in resp.text  # extraction_count, not one row per measurement


class TestListViewMapData:
    """GET / embeds map_entities() as MAP_ENTITIES for map.js — see
    notes/coastal-crawler/builds/2026-08-12-location-map-01.md (now
    entity-terminology, per 2026-08-12-warehouse-site-01.md)."""

    def test_embeds_located_paper_as_map_entity(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None],
        warehouse_path: Path,
    ) -> None:
        paper_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Coastal Marsh Study",
            data={"attribute": "salinity", "value": "28.4", "units": "psu", "name": "Great Bay"},
            latitude=41.5,
            longitude=-70.6,
        )
        rebuild_warehouse()
        entity_id = _entity_id_for_paper(warehouse_path, paper_id)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "const MAP_ENTITIES" in resp.text
        assert '"entity_id": %d' % entity_id in resp.text
        assert '"entity_name": "Great Bay"' in resp.text
        assert '"latitude": 41.5' in resp.text
        assert '"paper_count": 1' in resp.text

    def test_unlocated_paper_not_in_map_data(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        _seed_paper_with_extraction(
            clean_db,
            title="Unlocated Paper",
            data={"attribute": "salinity", "value": "1", "units": "psu"},
        )
        rebuild_warehouse()
        resp = client.get("/")
        assert resp.status_code == 200
        assert "const MAP_ENTITIES = [];" in resp.text

    def test_filter_narrows_map_data(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None],
        warehouse_path: Path,
    ) -> None:
        matching_paper_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Reef Paper",
            data={
                "attribute": "salinity", "value": "1", "units": "psu",
                "ecosystem_type": "reef", "name": "Reef Site",
            },
            latitude=10.0,
            longitude=20.0,
        )
        _seed_paper_with_located_extraction(
            clean_db,
            title="Marsh Paper",
            data={
                "attribute": "salinity", "value": "2", "units": "psu",
                "ecosystem_type": "marsh", "name": "Marsh Site",
            },
            latitude=30.0,
            longitude=40.0,
        )
        rebuild_warehouse()
        matching_entity_id = _entity_id_for_paper(warehouse_path, matching_paper_id)
        resp = client.get("/", params={"ecosystem_type": "reef"})
        assert resp.status_code == 200
        assert '"entity_id": %d' % matching_entity_id in resp.text
        assert "Marsh Site" not in resp.text


class TestPaperViewShowsPages:
    def test_lists_page_links_with_counts(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        paper_id, _ = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": "psu"},
            page_number=2,
        )
        rebuild_warehouse()
        resp = client.get(f"/papers/{paper_id}")
        assert resp.status_code == 200
        assert f'href="/papers/{paper_id}/pages/2' in resp.text
        assert "page 3" in resp.text.lower()  # 0-indexed stored, displayed as +1

    def test_unknown_page_bucket_is_not_a_link(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        paper_id, _ = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": "psu"},
            page_number=None,
        )
        rebuild_warehouse()
        resp = client.get(f"/papers/{paper_id}")
        assert resp.status_code == 200
        assert "unknown page" in resp.text.lower()
        assert "/pages/None" not in resp.text

    def test_404_for_missing_paper(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        rebuild_warehouse()  # even an empty warehouse must exist for get_paper() to run
        resp = client.get("/papers/999999")
        assert resp.status_code == 404

    def test_attribute_filter_narrows_page_counts(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        with Session(clean_db) as session:
            store.upsert_papers([make_paper(status="extracted")], session)
            paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "salinity", "value": "1", "units": "psu"}),
                session, page_number=0, page_matched=True,
            )
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "nitrate", "value": "2", "units": "µmol/L"}),
                session, page_number=1, page_matched=True,
            )
            session.commit()
            paper_id = paper.id
        rebuild_warehouse()
        resp = client.get(f"/papers/{paper_id}", params={"attribute": "nitrate"})
        assert resp.status_code == 200
        assert f'/papers/{paper_id}/pages/1' in resp.text
        assert f'/papers/{paper_id}/pages/0' not in resp.text


class TestPageView:
    def test_shows_ocr_text_and_measurement(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "28.4", "units": "PSU"},
            page_number=0,
            ocr_context='<page number="0">Salinity was measured at 28.4 PSU.</page>',
        )
        rebuild_warehouse()
        resp = client.get(f"/papers/{paper_id}/pages/0")
        assert resp.status_code == 200
        assert "28.4" in resp.text
        assert "Salinity was measured" in resp.text
        assert f'action="/extraction/{extraction_id}/vote"' in resp.text

    def test_missing_ocr_text_shows_unavailable_message(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        paper_id, _ = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": "psu"},
            page_number=0,
        )
        rebuild_warehouse()
        resp = client.get(f"/papers/{paper_id}/pages/0")
        assert resp.status_code == 200
        assert "unavailable" in resp.text.lower()

    def test_attribute_filter_excludes_non_matching_measurement(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        with Session(clean_db) as session:
            store.upsert_papers([make_paper(status="extracted")], session)
            paper = session.scalars(select(Paper).order_by(Paper.id.desc())).first()
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "salinity", "value": "1", "units": "psu"}),
                session, page_number=0, page_matched=True,
            )
            store.insert_extraction(
                paper.id,
                make_extraction_result(data={"attribute": "nitrate", "value": "99", "units": "µmol/L"}),
                session, page_number=0, page_matched=True,
            )
            session.commit()
            paper_id = paper.id
        rebuild_warehouse()
        resp = client.get(f"/papers/{paper_id}/pages/0", params={"attribute": "nitrate"})
        assert "99" in resp.text
        assert "salinity" not in resp.text.lower()

    def test_404_for_missing_paper(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        rebuild_warehouse()  # even an empty warehouse must exist for get_paper() to run
        resp = client.get("/papers/999999/pages/0")
        assert resp.status_code == 404


class TestEntityPapersRoute:
    def test_lists_papers_at_entity(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None],
        warehouse_path: Path,
    ) -> None:
        paper_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Great Bay Nutrient Study",
            data={"attribute": "salinity", "value": "1", "units": "psu", "name": "Great Bay"},
            latitude=41.5,
            longitude=-70.6,
        )
        rebuild_warehouse()
        entity_id = _entity_id_for_paper(warehouse_path, paper_id)
        resp = client.get(f"/entities/{entity_id}/papers")
        assert resp.status_code == 200
        assert "Great Bay" in resp.text
        assert f'href="/papers/{paper_id}' in resp.text

    def test_excludes_papers_at_other_entities(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None],
        warehouse_path: Path,
    ) -> None:
        included_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Included Paper",
            data={"attribute": "salinity", "value": "1", "units": "psu"},
            latitude=41.5,
            longitude=-70.6,
        )
        _seed_paper_with_located_extraction(
            clean_db,
            title="Excluded Paper",
            data={"attribute": "salinity", "value": "2", "units": "psu"},
            latitude=42.0,
            longitude=-71.0,
        )
        rebuild_warehouse()
        entity_id = _entity_id_for_paper(warehouse_path, included_id)
        resp = client.get(f"/entities/{entity_id}/papers")
        assert "Included Paper" in resp.text
        assert "Excluded Paper" not in resp.text

    def test_filter_narrows_papers_at_entity(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None],
        warehouse_path: Path,
    ) -> None:
        # Same coordinates for both papers' extractions — resolve_entities
        # clusters them into one entity, so the attribute filter alone
        # decides which paper shows.
        match_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Matching Paper",
            data={"attribute": "salinity", "value": "1", "units": "psu"},
            latitude=41.5,
            longitude=-70.6,
        )
        _seed_paper_with_located_extraction(
            clean_db,
            title="Non-Matching Paper",
            data={"attribute": "nitrate", "value": "2", "units": "µmol/L"},
            latitude=41.5,
            longitude=-70.6,
        )
        rebuild_warehouse()
        entity_id = _entity_id_for_paper(warehouse_path, match_id)
        resp = client.get(f"/entities/{entity_id}/papers", params={"attribute": "salinity"})
        assert resp.status_code == 200
        assert "Matching Paper" in resp.text
        assert "Non-Matching Paper" not in resp.text

    def test_404_for_missing_entity(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        rebuild_warehouse()  # even an empty warehouse must exist for get_entity() to run
        resp = client.get("/entities/999999/papers")
        assert resp.status_code == 404

    def test_back_link_carries_filter(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None],
        warehouse_path: Path,
    ) -> None:
        paper_id = _seed_paper_with_located_extraction(
            clean_db,
            title="Filtered Back Link Paper",
            data={"attribute": "salinity", "value": "1", "units": "psu"},
            latitude=41.5,
            longitude=-70.6,
        )
        rebuild_warehouse()
        entity_id = _entity_id_for_paper(warehouse_path, paper_id)
        resp = client.get(f"/entities/{entity_id}/papers", params={"attribute": "salinity"})
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

    def test_vote_is_recorded(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        _paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": None},
            page_number=0,
        )
        # The POST redirects (303) to page_view, which client.post() follows
        # by default — that route needs a warehouse to exist, even though
        # this test only cares about the Postgres-side judgement write.
        rebuild_warehouse()
        client.post(f"/extraction/{extraction_id}/vote", data={"vote": "valid"})
        with Session(clean_db) as session:
            extraction = store.get_extraction(session, extraction_id)
            assert extraction.judgement == "valid"


class TestVoteJudgementShownOnPage:
    """judgement is read live from Postgres, not the warehouse (see
    site/app.py's _attach_judgements) — a vote cast after the warehouse was
    built must still show up on the next page load."""

    def test_judgement_appears_after_vote_without_rebuild(
        self, client: TestClient, clean_db: Engine, rebuild_warehouse: Callable[[], None]
    ) -> None:
        paper_id, extraction_id = _seed_paper_with_paged_extraction(
            clean_db,
            data={"attribute": "salinity", "value": "1", "units": "psu"},
            page_number=0,
        )
        rebuild_warehouse()
        client.post(f"/extraction/{extraction_id}/vote", data={"vote": "valid"})
        resp = client.get(f"/papers/{paper_id}/pages/0")
        assert resp.status_code == 200
        assert 'class="judgement valid"' in resp.text
