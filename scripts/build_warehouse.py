#!/usr/bin/env python3
"""One-shot rebuild of the DuckDB star-schema warehouse from Postgres.

Reads `papers`/`extractions` from Postgres (the landing zone, left
unchanged), resolves entities, parses value qualifiers, converts units, and
writes a fresh `Settings.warehouse_path` DuckDB file — full replacement,
not an incremental update (see the fact-grain decision in
notes/coastal-crawler/builds/2026-08-12-warehouse-init-01.md). Standalone
script, not wired into the experiment contract (params.stage) — see that
build note's "Out of scope".

**Skip and log, not fail loud** — a deliberate, named exception to
CLAUDE.md's fail-loud default (see the build note's done_when item 4): any
row that can't be translated (unknown attribute, unparseable value,
unconvertible units) is skipped and recorded in `<warehouse_path>.skip_report.jsonl`
(one JSON object per skipped row: extraction_id, paper_id, reason, detail)
rather than crashing the run. A summary count by reason is logged at the
end.

Usage:
    uv run scripts/build_warehouse.py
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import structlog
from sqlalchemy import select

from coastal_crawler.config import get_settings
from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Extraction, Paper
from coastal_crawler.measurement_schema import ATTRIBUTE_INFO_DICT, convert_to_canonical
from coastal_crawler.warehouse import (
    SCHEMA_DDL,
    dedupe_exact_fact_rows,
    hash_prompt,
    majority_value,
    parse_qualifier,
    resolve_entities,
)

log = structlog.get_logger(__name__)

# extraction_lm.py hardcodes this as ExtractionLM's sampling-params default
# (see __init__'s `self.sampling_params`) — not a Settings field today, so
# it can't be read from config. Recorded here as "the constant currently in
# effect," not silently fixed — see the build note's model_dim resolution.
_EXTRACTION_LM_DEFAULT_TEMPERATURE = 0.6


def _load_papers(session: Any) -> list[Paper]:
    return list(session.scalars(select(Paper)).all())


def _load_extractions(session: Any) -> list[Extraction]:
    return list(session.scalars(select(Extraction)).all())


def main() -> None:
    settings = get_settings()
    skip_records: list[dict[str, Any]] = []
    skip_reasons: Counter[str] = Counter()

    def _skip(extraction_id: int, paper_id: int, reason: str, detail: str) -> None:
        skip_records.append(
            {"extraction_id": extraction_id, "paper_id": paper_id, "reason": reason, "detail": detail}
        )
        skip_reasons[reason] += 1

    with get_session() as session:
        papers = _load_papers(session)
        extractions = _load_extractions(session)
        # Detach plain values while the session is open — Extraction.data is
        # a JSONB dict, safe to read after close, but keeping the read
        # inside the session block is simplest.
        extraction_rows: list[dict[str, Any]] = [
            {
                "id": e.id,
                "paper_id": e.paper_id,
                "schema_name": e.schema_name,
                "model_version": e.model_version,
                "data": dict(e.data or {}),
                "latitude": e.latitude,
                "longitude": e.longitude,
                "page_number": e.page_number,
                "confidence": e.confidence,
            }
            for e in extractions
        ]
        paper_rows = [
            {
                "id": p.id,
                "doi": p.doi,
                "title": p.title,
                "authors": list(p.authors) if p.authors else None,
                "publication_date": p.publication_date,
                "discovered_from": p.discovered_from,
                "openalex_id": p.openalex_id,
                "semantic_scholar_id": p.semantic_scholar_id,
            }
            for p in papers
        ]

    log.info("loaded_from_postgres", papers=len(paper_rows), extractions=len(extraction_rows))

    # ---------------------------------------------------------------- model_dim
    current_model_version = settings.extraction_model_version or (
        f"doc_lm={settings.doc_lm_model}+meas_lm={settings.meas_lm_model}"
    )
    distinct_models = sorted(
        {(r["schema_name"], r["model_version"]) for r in extraction_rows}
    )
    model_id_by_version: dict[str, int] = {}
    model_dim_rows: list[tuple[Any, ...]] = []
    for i, (_schema_name, model_version) in enumerate(distinct_models, start=1):
        model_id_by_version[model_version] = i
        is_current = model_version == current_model_version
        model_dim_rows.append((
            i,
            model_version,
            hash_prompt(settings.meas_lm_entity_identification_prompt) if is_current else None,
            settings.meas_lm_seed if is_current else None,
            _EXTRACTION_LM_DEFAULT_TEMPERATURE if is_current else None,
            "extraction",
        ))
    log.info(
        "model_dim_built",
        distinct_model_versions=len(distinct_models),
        current_model_version=current_model_version,
        current_version_present=current_model_version in model_id_by_version,
    )

    # ---------------------------------------------------------------- paper_dim
    paper_dim_rows = [
        (
            p["id"],
            p["doi"],
            p["title"],
            p["authors"],
            p["publication_date"],
            None,  # publisher: no source data today across any discovery source — see build note
            p["discovered_from"],
            p["openalex_id"],
            p["semantic_scholar_id"],
        )
        for p in paper_rows
    ]
    known_paper_ids = {p["id"] for p in paper_rows}

    # ---------------------------------------------------------------- entity_dim
    entity_input_rows: list[tuple[int, str | None, float | None, float | None]] = [
        (r["id"], r["data"].get("name"), r["latitude"], r["longitude"]) for r in extraction_rows
    ]
    row_to_entity = resolve_entities(
        entity_input_rows,
        settings.location_distance_threshold_km,
        settings.location_name_similarity_threshold,
    )
    entities_by_id = {e.entity_id: e for e in row_to_entity.values()}
    contributing_by_entity: dict[int, list[dict[str, Any]]] = {}
    for r in extraction_rows:
        entity = row_to_entity[r["id"]]
        contributing_by_entity.setdefault(entity.entity_id, []).append(r["data"])

    entity_dim_rows = []
    for entity_id, entity in sorted(entities_by_id.items()):
        contributing = contributing_by_entity.get(entity_id, [])
        entity_dim_rows.append((
            entity_id,
            entity.latitude,
            entity.longitude,
            entity.name,
            majority_value([d.get("location") for d in contributing]),
            majority_value([d.get("identifiers") for d in contributing]),
            majority_value([d.get("ecosystem_type") for d in contributing]),
            entity.resolution_method,
        ))
    log.info(
        "entity_dim_built",
        entities=len(entity_dim_rows),
        by_method=dict(Counter(e.resolution_method for e in entities_by_id.values())),
    )

    # ---------------------------------------------------------------- event_dim
    # Dedupe by the (date, sub_location, additional_details) tuple — many
    # fact rows share "no event info at all" (None, None, None).
    event_key_to_id: dict[tuple[Any, Any, Any], int] = {}
    event_dim_rows = []

    def _event_id_for(data: dict[str, Any]) -> int:
        key = (data.get("date"), data.get("sub_location"), data.get("additional_details"))
        if key not in event_key_to_id:
            event_id = len(event_key_to_id) + 1
            event_key_to_id[key] = event_id
            event_dim_rows.append((event_id, key[0], key[1], key[2]))
        return event_key_to_id[key]

    # ---------------------------------------------------------- qualifier_dim
    # Dedupe by the *converted* (canonical-unit) tuple — see build note:
    # bounds must be stored in canonical units, same as quantity_canonical.
    qualifier_key_to_id: dict[tuple[Any, ...], int] = {}
    qualifier_dim_rows = []

    def _qualifier_id_for(
        region: str,
        confidence_min: float | None,
        confidence_max: float | None,
        range_min: float | None,
        range_max: float | None,
        lte: float | None,
        gt: float | None,
    ) -> int:
        key = (region, confidence_min, confidence_max, range_min, range_max, lte, gt)
        if key not in qualifier_key_to_id:
            qualifier_id = len(qualifier_key_to_id) + 1
            qualifier_key_to_id[key] = qualifier_id
            qualifier_dim_rows.append((qualifier_id, *key))
        return qualifier_key_to_id[key]

    # ---------------------------------------------------------- extractions_fact
    candidate_rows: list[dict[str, Any]] = []
    for r in extraction_rows:
        extraction_id, paper_id, data = r["id"], r["paper_id"], r["data"]
        if paper_id not in known_paper_ids:
            _skip(extraction_id, paper_id, "orphaned_paper", f"paper_id {paper_id} not found")
            continue

        attribute = data.get("attribute")
        if attribute not in ATTRIBUTE_INFO_DICT:
            _skip(extraction_id, paper_id, "unknown_attribute", f"attribute={attribute!r}")
            continue

        raw_value = data.get("value")
        qualifier = parse_qualifier(raw_value)
        if qualifier is None:
            _skip(extraction_id, paper_id, "unparseable_value", f"value={raw_value!r}")
            continue

        raw_units = data.get("units")
        conversion = convert_to_canonical(attribute, raw_units)
        if conversion is None:
            _skip(extraction_id, paper_id, "unknown_unit", f"attribute={attribute!r} units={raw_units!r}")
            continue
        canonical_units, factor = conversion

        def _scale(v: float | None) -> float | None:
            return v * factor if v is not None else None

        quantity_canonical = _scale(qualifier.quantity)
        confidence_min = _scale(qualifier.confidence_min)
        confidence_max = _scale(qualifier.confidence_max)
        range_min = _scale(qualifier.range_min)
        range_max = _scale(qualifier.range_max)
        lte = _scale(qualifier.less_than_or_equal)
        gt = _scale(qualifier.greater_than)

        qualifier_id = _qualifier_id_for(
            qualifier.confidence_region, confidence_min, confidence_max, range_min, range_max, lte, gt
        )
        event_id = _event_id_for(data)
        entity_id = row_to_entity[extraction_id].entity_id
        model_id = model_id_by_version[r["model_version"]]

        candidate_rows.append({
            "extraction_id": extraction_id,
            "paper_id": paper_id,
            "model_id": model_id,
            "entity_id": entity_id,
            "event_id": event_id,
            "attribute": attribute,
            "quantity_raw": raw_value,
            "units_raw": raw_units,
            "quantity_canonical": quantity_canonical,
            "units_canonical": canonical_units,
            "qualifier_id": qualifier_id,
            "page_number": r["page_number"],
            "confidence": r["confidence"],
        })

    # Drop fact rows that are exact duplicates of another (same measurement
    # recorded twice) — see the build note's fact-row-dedup resolution.
    # Genuinely distinct measurements that merely share a reported value
    # differ on entity_id/event_id/etc. and are never touched by this pass.
    kept_rows, duplicate_rows = dedupe_exact_fact_rows(candidate_rows)
    for row in duplicate_rows:
        _skip(
            row["extraction_id"],
            row["paper_id"],
            "duplicate_fact_row",
            f"identical to a kept fact row for paper_id={row['paper_id']} attribute={row['attribute']!r}",
        )

    fact_rows = [
        (
            i,
            row["extraction_id"],
            row["paper_id"],
            row["model_id"],
            row["entity_id"],
            row["event_id"],
            row["attribute"],
            row["quantity_raw"],
            row["units_raw"],
            row["quantity_canonical"],
            row["units_canonical"],
            row["qualifier_id"],
            row["page_number"],
            row["confidence"],
        )
        for i, row in enumerate(kept_rows, start=1)
    ]

    log.info(
        "extractions_fact_built",
        fact_rows=len(fact_rows),
        duplicate_fact_rows=len(duplicate_rows),
        skipped=len(skip_records),
        skip_reasons=dict(skip_reasons),
        source_rows=len(extraction_rows),
    )

    # ---------------------------------------------------------------- write
    warehouse_path = Path(settings.warehouse_path)
    warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=warehouse_path.parent, prefix=f".{warehouse_path.name}.", suffix=".tmp"
    )
    os.close(fd)
    tmp_path = Path(tmp_path_str)
    tmp_path.unlink()  # duckdb.connect() must create the file itself

    def _insert_many(con: duckdb.DuckDBPyConnection, sql: str, rows: list[tuple[Any, ...]]) -> None:
        # duckdb's executemany() rejects an empty parameter list outright —
        # every one of these can legitimately be empty (e.g. zero surviving
        # fact rows means zero events/qualifiers too).
        if rows:
            con.executemany(sql, rows)

    con = duckdb.connect(str(tmp_path))
    try:
        for ddl in SCHEMA_DDL:
            con.execute(ddl)
        _insert_many(con, "INSERT INTO model_dim VALUES (?, ?, ?, ?, ?, ?)", model_dim_rows)
        _insert_many(
            con, "INSERT INTO paper_dim VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", paper_dim_rows
        )
        _insert_many(
            con, "INSERT INTO entity_dim VALUES (?, ?, ?, ?, ?, ?, ?, ?)", entity_dim_rows
        )
        _insert_many(con, "INSERT INTO event_dim VALUES (?, ?, ?, ?)", event_dim_rows)
        _insert_many(
            con, "INSERT INTO qualifier_dim VALUES (?, ?, ?, ?, ?, ?, ?, ?)", qualifier_dim_rows
        )
        _insert_many(
            con,
            "INSERT INTO extractions_fact VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            fact_rows,
        )
    finally:
        con.close()

    os.replace(tmp_path, warehouse_path)
    log.info("warehouse_written", path=str(warehouse_path))

    report_path = warehouse_path.with_name(warehouse_path.name + ".skip_report.jsonl")
    with open(report_path, "w") as f:
        for record in skip_records:
            f.write(json.dumps(record) + "\n")
    log.info("skip_report_written", path=str(report_path), rows=len(skip_records))


if __name__ == "__main__":
    main()
