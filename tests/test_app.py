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
