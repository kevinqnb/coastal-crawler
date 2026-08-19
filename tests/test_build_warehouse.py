"""End-to-end tests for scripts/build_warehouse.py.

`scripts/` isn't a package (no __init__.py, not installed) — the module is
loaded directly from its file path via importlib, same pattern
tests/test_resolve_locations.py used for the script this one supersedes.

Uses ``clean_db`` (real committed transactions, patched get_session) and
patches ``get_settings`` so tests can point ``warehouse_path`` at a temp
file without touching the environment.
"""

from __future__ import annotations

import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from coastal_crawler.config import Settings
from coastal_crawler.db import store
from coastal_crawler.db.models import Attribution, Extraction, Paper

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_warehouse.py"
_spec = importlib.util.spec_from_file_location("build_warehouse", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
build_warehouse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_warehouse)


def _settings(warehouse_path: str, **overrides: Any) -> Settings:
    return Settings(
        database_url="postgresql://unused/test",
        warehouse_path=warehouse_path,
        location_distance_threshold_km=overrides.pop("location_distance_threshold_km", 1.0),
        location_name_similarity_threshold=overrides.pop(
            "location_name_similarity_threshold", 0.85
        ),
        **overrides,
    )


@pytest.fixture
def warehouse_db(clean_db: Engine, mocker: Any, tmp_path: Path) -> tuple[Engine, Path]:
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

    warehouse_path = tmp_path / "warehouse.duckdb"
    mocker.patch.object(build_warehouse, "get_session", _test_get_session)
    mocker.patch.object(
        build_warehouse, "get_settings", lambda: _settings(str(warehouse_path))
    )
    return clean_db, warehouse_path


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
        from sqlalchemy import select

        return s.scalars(select(Paper.id)).one()


def _insert_extraction(
    engine: Engine,
    paper_id: int,
    data: dict[str, Any],
    model_version: str = "doc_lm=X+meas_lm=Y",
    page_number: int | None = None,
    confidence: float | None = None,
    probe_score: float | None = None,
) -> int:
    with Session(engine) as s:
        ext = Extraction(
            paper_id=paper_id,
            schema_name="test_schema",
            model_version=model_version,
            data=data,
            page_number=page_number,
            confidence=confidence,
            probe_score=probe_score,
        )
        s.add(ext)
        s.commit()
        return ext.id


def _insert_attribution(
    engine: Engine,
    extraction_id: int,
    method: str,
    scores: list[float],
    tokens: list[str],
    snippet: str = "the quick brown fox",
) -> None:
    with Session(engine) as s:
        s.add(
            Attribution(
                extraction_id=extraction_id,
                method=method,
                scores=scores,
                token_indices=list(range(len(scores))),
                tokens=tokens,
                snippet=snippet,
            )
        )
        s.commit()


def _tables(warehouse_path: Path) -> dict[str, list[tuple]]:
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        return {
            t: con.execute(f"SELECT * FROM {t}").fetchall()
            for t in (
                "paper_dim", "entity_dim", "event_dim", "qualifier_dim",
                "model_dim", "extractions_fact", "attribution_fact",
            )
        }
    finally:
        con.close()


class TestBuildWarehouseEndToEnd:
    def test_clean_row_reaches_fact_table(self, warehouse_db: tuple[Engine, Path]) -> None:
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "28.4", "units": "psu"}
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert len(tables["extractions_fact"]) == 1
        fact = tables["extractions_fact"][0]
        assert fact[6] == "salinity"  # attribute
        assert fact[9] == pytest.approx(28.4)  # quantity_canonical

    def test_malformed_row_does_not_crash_and_appears_in_report(
        self, warehouse_db: tuple[Engine, Path]
    ) -> None:
        """A deliberately malformed fixture row (garbage value) doesn't
        crash the run and appears in the skip report — done_when item 6's
        skip-and-log behavior test."""
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        good_id = _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "28.4", "units": "psu"}
        )
        bad_id = _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "not-a-number-at-all", "units": "psu"}
        )

        build_warehouse.main()  # must not raise

        tables = _tables(warehouse_path)
        assert len(tables["extractions_fact"]) == 1
        assert tables["extractions_fact"][0][1] == good_id  # source_extraction_id

        report_path = warehouse_path.with_name(warehouse_path.name + ".skip_report.jsonl")
        records = [json.loads(line) for line in report_path.read_text().splitlines()]
        assert len(records) == 1
        assert records[0]["extraction_id"] == bad_id
        assert records[0]["reason"] == "unparseable_value"

    def test_unknown_attribute_skips_and_logs(self, warehouse_db: tuple[Engine, Path]) -> None:
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(engine, paper_id, {"attribute": "not_a_real_attribute", "value": "1"})

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert tables["extractions_fact"] == []
        report_path = warehouse_path.with_name(warehouse_path.name + ".skip_report.jsonl")
        records = [json.loads(line) for line in report_path.read_text().splitlines()]
        assert records[0]["reason"] == "unknown_attribute"

    def test_unknown_unit_skips_and_logs(self, warehouse_db: tuple[Engine, Path]) -> None:
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "5", "units": "mS/cm"}
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert tables["extractions_fact"] == []
        report_path = warehouse_path.with_name(warehouse_path.name + ".skip_report.jsonl")
        records = [json.loads(line) for line in report_path.read_text().splitlines()]
        assert records[0]["reason"] == "unknown_unit"

    def test_known_unit_conversion_succeeds(self, warehouse_db: tuple[Engine, Path]) -> None:
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id, {"attribute": "dissolved_inorganic_carbon", "value": "1", "units": "mmol/kg"}
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert len(tables["extractions_fact"]) == 1
        fact = tables["extractions_fact"][0]
        assert fact[9] == pytest.approx(1000.0)  # quantity_canonical: mmol/kg -> µmol/kg
        assert fact[10] == "µmol/kg"  # units_canonical

    def test_two_nearby_rows_resolve_to_one_entity(self, warehouse_db: tuple[Engine, Path]) -> None:
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id,
            {"attribute": "salinity", "value": "1", "units": "psu",
             "name": "Site A", "latitude": 10.0, "longitude": 20.0},
        )
        _insert_extraction(
            engine, paper_id,
            {"attribute": "salinity", "value": "2", "units": "psu",
             "name": "Site A", "latitude": 10.001, "longitude": 20.0},
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert len(tables["entity_dim"]) == 1
        entity_ids = {fact[4] for fact in tables["extractions_fact"]}
        assert len(entity_ids) == 1

    def test_current_model_gets_seed_and_prompt_historical_does_not(
        self, warehouse_db: tuple[Engine, Path], mocker: Any
    ) -> None:
        engine, warehouse_path = warehouse_db
        current_settings = _settings(
            str(warehouse_path),
            doc_lm_model="olmocr", meas_lm_model="gpt-oss", meas_lm_seed=42,
        )
        mocker.patch.object(build_warehouse, "get_settings", lambda: current_settings)
        current_version = "doc_lm=olmocr+meas_lm=gpt-oss"
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "1", "units": "psu"},
            model_version=current_version,
        )
        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "2", "units": "psu"},
            model_version="doc_lm=old+meas_lm=old",
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        by_name = {row[1]: row for row in tables["model_dim"]}
        assert by_name[current_version][3] == 42  # seed
        assert by_name["doc_lm=old+meas_lm=old"][3] is None  # seed not recoverable historically

    def test_page_number_and_confidence_carry_through(
        self, warehouse_db: tuple[Engine, Path]
    ) -> None:
        """extractions_fact.page_number/.confidence are a plain carry-through
        from the source Postgres row — no parsing, unlike every other fact
        column (see the build note's 2026-08-12 resolution: these are
        static per-row attributes set once at extraction time, not live
        state like `judgement`, so they belong in the warehouse snapshot)."""
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "28.4", "units": "psu"},
            page_number=3, confidence=0.87,
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert len(tables["extractions_fact"]) == 1
        fact = tables["extractions_fact"][0]
        assert fact[12] == 3  # page_number
        assert fact[13] == pytest.approx(0.87)  # confidence

    def test_duplicate_fact_rows_collapse_and_appear_in_skip_report(
        self, warehouse_db: tuple[Engine, Path]
    ) -> None:
        """Two source rows that would produce an identical fact row (same
        paper/entity/event/attribute/quantity/units/qualifier/page/
        confidence) collapse to one — the highest source extraction id
        survives, the other is skip-and-logged as duplicate_fact_row. Two
        rows with different `page_number` are NOT duplicates — they're kept
        separately (see the build note's fact-dedup resolution)."""
        # All three rows share a `name` and no coordinates, so
        # resolve_entities' fuzzy-name matching puts them all in the *same*
        # entity — otherwise each no-coordinate/no-shared-name row becomes
        # its own unresolved singleton entity, and the dedup key (which
        # includes entity_id) would never see the pair as duplicates.
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        first_id = _insert_extraction(
            engine, paper_id,
            {"attribute": "salinity", "value": "28.4", "units": "psu", "name": "Site A"},
            page_number=1,
        )
        second_id = _insert_extraction(
            engine, paper_id,
            {"attribute": "salinity", "value": "28.4", "units": "psu", "name": "Site A"},
            page_number=1,
        )
        _insert_extraction(
            engine, paper_id,
            {"attribute": "salinity", "value": "28.4", "units": "psu", "name": "Site A"},
            page_number=2,
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert len(tables["extractions_fact"]) == 2  # the page=2 row + one survivor of the pair
        surviving_source_ids = {fact[1] for fact in tables["extractions_fact"]}
        assert second_id in surviving_source_ids  # highest id among the duplicate pair
        assert first_id not in surviving_source_ids

        report_path = warehouse_path.with_name(warehouse_path.name + ".skip_report.jsonl")
        records = [json.loads(line) for line in report_path.read_text().splitlines()]
        duplicate_records = [r for r in records if r["reason"] == "duplicate_fact_row"]
        assert len(duplicate_records) == 1
        assert duplicate_records[0]["extraction_id"] == first_id

    def test_probe_score_carries_through(self, warehouse_db: tuple[Engine, Path]) -> None:
        """extractions_fact.probe_score is a plain carry-through from the
        source Postgres row, same treatment as .confidence — see
        notes/coastal-crawler/builds/2026-08-19-judge-attribution-display-01.md."""
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "28.4", "units": "psu"},
            confidence=1.07e-6, probe_score=3.25e-12,
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert len(tables["extractions_fact"]) == 1
        fact = tables["extractions_fact"][0]
        assert fact[13] == pytest.approx(1.07e-6)  # confidence
        assert fact[14] == pytest.approx(3.25e-12)  # probe_score

    def test_attributions_load_into_attribution_fact(
        self, warehouse_db: tuple[Engine, Path]
    ) -> None:
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        extraction_id = _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "28.4", "units": "psu"},
        )
        _insert_attribution(
            engine, extraction_id, "probe", scores=[0.1, 0.9], tokens=["foo", "bar"]
        )
        _insert_attribution(
            engine, extraction_id, "contrastive_gradient", scores=[-0.2, 0.3], tokens=["foo", "bar"]
        )

        build_warehouse.main()

        tables = _tables(warehouse_path)
        assert len(tables["attribution_fact"]) == 2
        by_method = {row[1]: row for row in tables["attribution_fact"]}
        assert by_method["probe"][0] == extraction_id  # source_extraction_id
        assert by_method["probe"][3] == ["foo", "bar"]  # tokens
        assert by_method["probe"][4] == pytest.approx([0.1, 0.9])  # scores
        assert by_method["contrastive_gradient"][4] == pytest.approx([-0.2, 0.3])

    def test_attribution_for_a_fact_table_skipped_row_still_loads(
        self, warehouse_db: tuple[Engine, Path]
    ) -> None:
        """An extraction row that extractions_fact skips (e.g. unknown
        attribute) can still have attribution data — build_warehouse must
        not crash, and the orphaned attribution row is simply unreachable
        from the site (no fact row to join against), not an error."""
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        extraction_id = _insert_extraction(
            engine, paper_id, {"attribute": "not_a_real_attribute", "value": "1"}
        )
        _insert_attribution(
            engine, extraction_id, "probe", scores=[0.5], tokens=["foo"]
        )

        build_warehouse.main()  # must not raise

        tables = _tables(warehouse_path)
        assert tables["extractions_fact"] == []
        assert len(tables["attribution_fact"]) == 1

    def test_rebuild_fully_replaces_prior_contents(self, warehouse_db: tuple[Engine, Path]) -> None:
        """Fact-grain decision: each run is a full replacement, not an
        accumulation — see the build note."""
        engine, warehouse_path = warehouse_db
        paper_id = _make_paper(engine)
        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "1", "units": "psu"}
        )
        build_warehouse.main()
        assert len(_tables(warehouse_path)["extractions_fact"]) == 1

        _insert_extraction(
            engine, paper_id, {"attribute": "salinity", "value": "2", "units": "psu"}
        )
        build_warehouse.main()
        assert len(_tables(warehouse_path)["extractions_fact"]) == 2
