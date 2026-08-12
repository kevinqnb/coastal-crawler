"""Tests for scripts/resolve_locations.py.

`scripts/` isn't a package (no __init__.py, not installed) — the module is
loaded directly from its file path via importlib, same pattern as
tests/test_backfill_page_numbers.py.

Uses ``clean_db`` (not ``db_session``): the script commits one real
transaction via its own ``get_session()`` call, patched here to the test
engine the same way ``worker_db``/``backfill_db`` patch worker.py's/
backfill_page_numbers.py's get_session in the sibling test files.
``get_settings`` is patched too, so tests can set
location_distance_threshold_km/location_name_similarity_threshold without
touching the environment.
"""

from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.config import Settings
from coastal_crawler.db import store
from coastal_crawler.db.models import Extraction, Location, Paper

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "resolve_locations.py"
_spec = importlib.util.spec_from_file_location("resolve_locations", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
resolve_locations = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolve_locations)


def _settings(**overrides: Any) -> Settings:
    return Settings(
        database_url="postgresql://unused/test",
        location_distance_threshold_km=overrides.pop("location_distance_threshold_km", 1.0),
        location_name_similarity_threshold=overrides.pop(
            "location_name_similarity_threshold", 0.85
        ),
        **overrides,
    )


@pytest.fixture
def resolve_db(clean_db: Engine, mocker: Any) -> Engine:
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

    mocker.patch.object(resolve_locations, "get_session", _test_get_session)
    mocker.patch.object(resolve_locations, "get_settings", lambda: _settings())
    return clean_db


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


def _make_paper(engine: Engine) -> int:
    with Session(engine) as s:
        store.upsert_papers([make_paper()], s)
        s.commit()
        return s.scalars(select(Paper.id)).one()


def _insert_extraction(engine: Engine, paper_id: int, data: dict[str, Any]) -> int:
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


def _location(engine: Engine, location_id: int) -> Location:
    with Session(engine) as s:
        return s.get(Location, location_id)


class TestCoordinateClustering:
    def test_identical_coordinates_merge(self, resolve_db: Engine) -> None:
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(
            resolve_db, paper_id, {"name": "Site A", "latitude": "10.0", "longitude": "20.0"}
        )
        e2 = _insert_extraction(
            resolve_db, paper_id, {"name": "Site A", "latitude": "10.0", "longitude": "20.0"}
        )

        resolve_locations.main()

        ext1, ext2 = _extraction(resolve_db, e1), _extraction(resolve_db, e2)
        assert ext1.location_id is not None
        assert ext1.location_id == ext2.location_id
        loc = _location(resolve_db, ext1.location_id)
        assert loc.resolution_method == "coordinate"
        assert loc.latitude == 10.0
        assert loc.longitude == 20.0

    def test_within_threshold_merges_outside_does_not(self, resolve_db: Engine) -> None:
        paper_id = _make_paper(resolve_db)
        # ~0.56 km apart (0.005 deg lat) — inside the 1.0 km default threshold.
        near_a = _insert_extraction(
            resolve_db, paper_id, {"name": "A", "latitude": "10.000", "longitude": "20.0"}
        )
        near_b = _insert_extraction(
            resolve_db, paper_id, {"name": "A", "latitude": "10.005", "longitude": "20.0"}
        )
        # ~55 km away — well outside the threshold.
        far = _insert_extraction(
            resolve_db, paper_id, {"name": "A", "latitude": "10.5", "longitude": "20.0"}
        )

        resolve_locations.main()

        e_near_a, e_near_b, e_far = (
            _extraction(resolve_db, near_a),
            _extraction(resolve_db, near_b),
            _extraction(resolve_db, far),
        )
        assert e_near_a.location_id == e_near_b.location_id
        assert e_far.location_id != e_near_a.location_id

    def test_centroid_is_mean_of_distinct_points_not_rows(self, resolve_db: Engine) -> None:
        """Two rows share point (10.000, 20.0), one row sits at (10.002, 20.0)
        — all three within the 1.0 km default threshold, so they cluster
        together. A row-weighted mean would pull the centroid toward
        (10.000, 20.0) (10.000667); the point-weighted mean documented in
        the Approach note is exactly 10.001 — this is the case that tells
        the two apart."""
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(
            resolve_db, paper_id, {"name": "A", "latitude": "10.000", "longitude": "20.000"}
        )
        _insert_extraction(
            resolve_db, paper_id, {"name": "A", "latitude": "10.000", "longitude": "20.000"}
        )
        _insert_extraction(
            resolve_db, paper_id, {"name": "A", "latitude": "10.002", "longitude": "20.000"}
        )

        resolve_locations.main()

        loc = _location(resolve_db, _extraction(resolve_db, e1).location_id)
        assert loc.latitude == pytest.approx(10.001)
        assert loc.latitude != pytest.approx(10.000667, abs=1e-6)
        assert loc.longitude == pytest.approx(20.000)


class TestNameMatching:
    def test_matching_normalized_names_merge(self, resolve_db: Engine) -> None:
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(resolve_db, paper_id, {"name": "Cedar Marsh"})
        e2 = _insert_extraction(resolve_db, paper_id, {"name": "  CEDAR   marsh!! "})

        resolve_locations.main()

        ext1, ext2 = _extraction(resolve_db, e1), _extraction(resolve_db, e2)
        assert ext1.location_id == ext2.location_id
        loc = _location(resolve_db, ext1.location_id)
        assert loc.resolution_method == "name"
        assert loc.resolution_key == "cedar marsh"

    def test_fuzzy_match_merges_differently_normalized_names(self, resolve_db: Engine) -> None:
        """Unlike test_matching_normalized_names_merge (identical normalized
        strings, merges via dict grouping before SequenceMatcher ever runs),
        this pair normalizes to two *different* strings —
        "cedar marsh site" vs "cedar marsh sites" — that must go through
        the actual fuzzy-match branch. ratio() == 0.9697, comfortably above
        the 0.85 default threshold."""
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(resolve_db, paper_id, {"name": "Cedar Marsh Site"})
        e2 = _insert_extraction(resolve_db, paper_id, {"name": "Cedar Marsh Sites"})

        resolve_locations.main()

        ext1, ext2 = _extraction(resolve_db, e1), _extraction(resolve_db, e2)
        assert ext1.location_id == ext2.location_id
        loc = _location(resolve_db, ext1.location_id)
        assert loc.resolution_method == "name"
        # Target location keeps the first-encountered row's normalized name
        # as its resolution_key — the second row matched *into* it, rather
        # than the two merging into a new blended key.
        assert loc.resolution_key == "cedar marsh site"

    def test_dissimilar_names_do_not_merge(self, resolve_db: Engine) -> None:
        """ratio("cedar marsh", "pacific ocean deep trench") == 0.2778 —
        well below the 0.85 default threshold, so this exercises the
        no-match path for the right reason, not by accident."""
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(resolve_db, paper_id, {"name": "Cedar Marsh"})
        e2 = _insert_extraction(resolve_db, paper_id, {"name": "Pacific Ocean Deep Trench"})

        resolve_locations.main()

        ext1, ext2 = _extraction(resolve_db, e1), _extraction(resolve_db, e2)
        assert ext1.location_id != ext2.location_id

    def test_coordinateless_row_never_merges_into_coordinate_location(
        self, resolve_db: Engine
    ) -> None:
        paper_id = _make_paper(resolve_db)
        with_coords = _insert_extraction(
            resolve_db,
            paper_id,
            {"name": "Cedar Marsh", "latitude": "10.0", "longitude": "20.0"},
        )
        without_coords = _insert_extraction(resolve_db, paper_id, {"name": "Cedar Marsh"})

        resolve_locations.main()

        loc_with = _location(resolve_db, _extraction(resolve_db, with_coords).location_id)
        loc_without = _location(resolve_db, _extraction(resolve_db, without_coords).location_id)
        assert loc_with.id != loc_without.id
        assert loc_with.resolution_method == "coordinate"
        assert loc_without.resolution_method == "name"


class TestUnresolvedSingleton:
    def test_no_name_no_coords_becomes_own_unresolved_location(self, resolve_db: Engine) -> None:
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(resolve_db, paper_id, {})
        e2 = _insert_extraction(resolve_db, paper_id, {})

        resolve_locations.main()

        ext1, ext2 = _extraction(resolve_db, e1), _extraction(resolve_db, e2)
        # No blind null-to-null merging: each gets its own location.
        assert ext1.location_id != ext2.location_id
        assert _location(resolve_db, ext1.location_id).resolution_method == "unresolved"
        assert _location(resolve_db, ext2.location_id).resolution_method == "unresolved"


class TestFailLoud:
    def test_refuses_to_run_if_any_row_already_resolved(self, resolve_db: Engine) -> None:
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(resolve_db, paper_id, {"name": "Cedar Marsh"})
        with Session(resolve_db) as s:
            loc = Location(resolution_method="unresolved")
            s.add(loc)
            s.flush()
            s.execute(update(Extraction).where(Extraction.id == e1).values(location_id=loc.id))
            s.commit()

        with pytest.raises(RuntimeError, match="already set"):
            resolve_locations.main()

    def test_unparseable_coordinate_raises(self, resolve_db: Engine) -> None:
        paper_id = _make_paper(resolve_db)
        _insert_extraction(
            resolve_db, paper_id, {"latitude": "not-a-number", "longitude": "20.0"}
        )

        with pytest.raises(ValueError, match="unparseable"):
            resolve_locations.main()

    def test_partial_coordinates_treated_as_no_coordinates(self, resolve_db: Engine) -> None:
        """A row with only one of latitude/longitude present is routed
        through name-matching, not raised — confirmed against live data
        (336/16375 extraction rows had exactly one of the two set; see
        notes/coastal-crawler/builds/2026-08-11-location-resolution-01.md).
        The lone coordinate is discarded, not guessed at."""
        paper_id = _make_paper(resolve_db)
        e1 = _insert_extraction(
            resolve_db, paper_id, {"latitude": "10.0", "name": "Cedar Marsh"}
        )  # no longitude
        e2 = _insert_extraction(
            resolve_db, paper_id, {"longitude": "-70.0", "name": "Cedar Marsh"}
        )  # no latitude

        resolve_locations.main()

        ext1, ext2 = _extraction(resolve_db, e1), _extraction(resolve_db, e2)
        loc1, loc2 = _location(resolve_db, ext1.location_id), _location(resolve_db, ext2.location_id)
        # Same normalized name, both coordinate-less -> merge via the name path.
        assert ext1.location_id == ext2.location_id
        assert loc1.resolution_method == "name"
        assert loc1.latitude is None
        assert loc1.longitude is None
