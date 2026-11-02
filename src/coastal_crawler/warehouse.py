"""DuckDB star-schema warehouse build logic.

Pure transform functions used by scripts/build_warehouse.py's one-shot
rebuild — no Postgres/DuckDB I/O in this module, so every function here is
testable against hand-built fixtures. See
notes/coastal-crawler/builds/2026-08-12-warehouse-init-01.md for the full
design writeup (schema, tier definitions, resolved decisions) this
implements.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

# ---------------------------------------------------------------------------
# Parsed quantity — scholarlm's MeasurementLM._parse_quantities() output,
# carried through from extractions.data (no re-parsing here)
# ---------------------------------------------------------------------------

PARSED_QUANTITY_FIELDS: tuple[str, ...] = (
    "qualifiers", "point_value", "lower", "upper",
    "list_values", "tolerance", "standard_deviation",
)
"""The seven keys `_parse_quantities()` adds to every record it parses. A
record whose parse call failed has none of them (scholarlm logs and keeps
the record unparsed) — see `extract_parsed_quantity`."""


@dataclass(frozen=True)
class ParsedQuantity:
    """One extraction row's model-parsed value, as returned by the model.

    point_value/lower/upper/tolerance/standard_deviation are stored as text
    exactly as returned (``str()`` of a bare JSON number): the model may emit
    a number or a string for the first three, and tolerance/standard_deviation
    are freeform ("95% CI: 5-9"). No unit conversion is applied anywhere.
    """

    qualifiers: list[str]
    point_value: str | None
    lower: str | None
    upper: str | None
    list_values: list[str] | None
    tolerance: str | None
    standard_deviation: str | None


def _as_text(field: str, v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise TypeError(f"parsed field {field!r} must be str/number/None, got {type(v).__name__}: {v!r}")
    return str(v)


def extract_parsed_quantity(data: dict[str, Any]) -> ParsedQuantity | None:
    """Read the model-parsed value fields from an `extractions.data` dict.

    Returns None if none of the seven fields are present (the parse call
    failed for this record — the caller skips and logs). Raises if only some
    are present or a field has the wrong type: that is a corrupt row, not a
    failed parse.
    """
    present = [f for f in PARSED_QUANTITY_FIELDS if f in data]
    if not present:
        return None
    if len(present) != len(PARSED_QUANTITY_FIELDS):
        missing = sorted(set(PARSED_QUANTITY_FIELDS) - set(present))
        raise ValueError(f"partial parsed-quantity fields in extractions.data: missing {missing}")

    qualifiers = data["qualifiers"]
    if not isinstance(qualifiers, list) or not all(isinstance(q, str) for q in qualifiers):
        raise TypeError(f"parsed field 'qualifiers' must be list[str], got {qualifiers!r}")
    list_values = data["list_values"]
    if list_values is not None:
        if not isinstance(list_values, list):
            raise TypeError(f"parsed field 'list_values' must be list or None, got {list_values!r}")
        list_values = [_as_text("list_values", v) for v in list_values]
        if any(v is None for v in list_values):
            raise TypeError(f"parsed field 'list_values' contains null: {data['list_values']!r}")

    return ParsedQuantity(
        qualifiers=list(qualifiers),
        point_value=_as_text("point_value", data["point_value"]),
        lower=_as_text("lower", data["lower"]),
        upper=_as_text("upper", data["upper"]),
        list_values=list_values,
        tolerance=_as_text("tolerance", data["tolerance"]),
        standard_deviation=_as_text("standard_deviation", data["standard_deviation"]),
    )


# ---------------------------------------------------------------------------
# Entity resolution — ported from scripts/resolve_locations.py (deleted by
# this build; see the build note's Postgres-cleanup item). Same algorithm,
# adapted to operate on an in-memory list of rows rather than reading from
# and writing back to Postgres: coordinate-proximity clustering first
# (haversine union-find over distinct points), then fuzzy-name matching for
# whatever has no coordinates, same as before.
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0088
_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ResolvedEntity:
    entity_id: int
    name: str | None
    latitude: float | None
    longitude: float | None
    resolution_method: str  # 'coordinate' | 'name' | 'unresolved'


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def normalize_entity_name(name: str | None) -> str | None:
    if not name:
        return None
    normalized = _WHITESPACE_RE.sub(" ", _PUNCT_RE.sub("", name.lower())).strip()
    return normalized or None


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def resolve_entities(
    rows: list[tuple[int, str | None, float | None, float | None]],
    distance_threshold_km: float,
    name_similarity_threshold: float,
) -> dict[int, ResolvedEntity]:
    """Resolve extraction rows into canonical entities.

    `rows` is `(row_key, name, latitude, longitude)` — `row_key` is
    caller-defined (e.g. `extractions.id`) and just needs to be unique and
    hashable; a partial coordinate pair (one of lat/lon present, not both)
    is treated as "no coordinates," same as scripts/resolve_locations.py
    did (see that script's history — confirmed against live data that a
    lone coordinate should be discarded, not guessed at).

    Returns a dict mapping every input `row_key` to the `ResolvedEntity` it
    belongs to. Every row gets an entity — a row with neither usable
    coordinates nor a usable name becomes its own singleton
    'unresolved' entity, same as before.
    """
    coord_rows: list[tuple[int, str | None, float, float]] = []
    nocoord_rows: list[tuple[int, str | None]] = []
    for row_key, name, lat, lon in rows:
        if lat is not None and lon is not None:
            coord_rows.append((row_key, name, lat, lon))
        else:
            nocoord_rows.append((row_key, name))

    row_to_entity: dict[int, ResolvedEntity] = {}
    next_id = 1

    distinct_points = sorted({(lat, lon) for _, _, lat, lon in coord_rows})
    point_index = {point: i for i, point in enumerate(distinct_points)}
    uf = _UnionFind(len(distinct_points))
    for i in range(len(distinct_points)):
        for j in range(i + 1, len(distinct_points)):
            if _haversine_km(distinct_points[i], distinct_points[j]) <= distance_threshold_km:
                uf.union(i, j)

    cluster_points: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for point, idx in point_index.items():
        cluster_points[uf.find(idx)].append(point)

    cluster_members: dict[int, list[tuple[int, str | None]]] = defaultdict(list)
    for row_key, name, lat, lon in coord_rows:
        root = uf.find(point_index[(lat, lon)])
        cluster_members[root].append((row_key, name))

    for root, members in cluster_members.items():
        points = cluster_points[root]
        centroid_lat = sum(p[0] for p in points) / len(points)
        centroid_lon = sum(p[1] for p in points) / len(points)
        name_counts = Counter(name for _, name in members if name)
        loc_name = None
        if name_counts:
            top_count = max(name_counts.values())
            loc_name = next(name for _, name in members if name and name_counts[name] == top_count)
        entity = ResolvedEntity(next_id, loc_name, centroid_lat, centroid_lon, "coordinate")
        next_id += 1
        for row_key, _ in members:
            row_to_entity[row_key] = entity

    rows_by_normalized: dict[str | None, list[tuple[int, str | None]]] = defaultdict(list)
    for row_key, name in nocoord_rows:
        rows_by_normalized[normalize_entity_name(name)].append((row_key, name))

    matched_entities: list[tuple[str, ResolvedEntity]] = []
    for normalized, members in rows_by_normalized.items():
        if normalized is None:
            for row_key, _ in members:
                entity = ResolvedEntity(next_id, None, None, None, "unresolved")
                next_id += 1
                row_to_entity[row_key] = entity
            continue

        target = next(
            (
                e
                for existing, e in matched_entities
                if SequenceMatcher(None, normalized, existing).ratio() >= name_similarity_threshold
            ),
            None,
        )
        if target is None:
            target = ResolvedEntity(next_id, members[0][1], None, None, "name")
            next_id += 1
            matched_entities.append((normalized, target))
        for row_key, _ in members:
            row_to_entity[row_key] = target

    return row_to_entity


def majority_value(values: list[str | None]) -> str | None:
    """Highest raw count among a set of non-null strings, ties broken
    ascending for determinism — same rule store.py's
    `location_majority_ecosystem_type` used, generalized to any
    entity-level field aggregated from multiple contributing extraction
    rows (`ecosystem_type`, `location_description`, `identifiers`). None
    if every contributing row is null for that field.
    """
    counts = Counter(v for v in values if v)
    if not counts:
        return None
    top_count = max(counts.values())
    return min(v for v, c in counts.items() if c == top_count)


# Kept as a named alias so call sites can say what they mean.
majority_ecosystem_type = majority_value


_FACT_DEDUP_KEY_FIELDS = (
    "paper_id", "model_id", "entity_id", "event_id", "attribute",
    "quantity_raw", "units_raw", "qualifiers", "point_value", "lower", "upper",
    "list_values", "tolerance", "standard_deviation", "page_number", "confidence",
)
"""Every fact column except `fact_id`/`source_extraction_id`/`probe_score`.
`qualifiers`/`list_values` are lists (unhashable) — keyed as tuples."""


def _dedup_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(tuple(v) if isinstance(v, list) else v for v in (row[f] for f in _FACT_DEDUP_KEY_FIELDS))


def dedupe_exact_fact_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop fact rows identical to another on every column except
    `extraction_id` (`extractions_fact.source_extraction_id`) — the same
    measurement recorded twice, not two genuinely distinct measurements
    that happen to share a value (those differ on `entity_id`/`event_id`/
    etc. and are kept as separate rows — that's the star schema's whole
    point over the old Postgres `(paper_id, attribute, value, units)` key).
    Ties keep the row with the highest `extraction_id`.

    Each dict in `rows` must have every field in `_FACT_DEDUP_KEY_FIELDS`
    plus `extraction_id`. Returns `(kept, dropped)`, each in the same
    relative order as the input.
    """
    best_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = _dedup_key(row)
        current = best_by_key.get(key)
        if current is None or row["extraction_id"] > current["extraction_id"]:
            best_by_key[key] = row
    kept_ids = {row["extraction_id"] for row in best_by_key.values()}
    kept = [r for r in rows if r["extraction_id"] in kept_ids]
    dropped = [r for r in rows if r["extraction_id"] not in kept_ids]
    return kept, dropped


def hash_prompt(prompt: str | None) -> str | None:
    """Short, stable hash of prompt text for model_dim.prompt_version —
    good enough to detect a prompt change across rebuilds without
    inventing a real versioning scheme the pipeline doesn't have."""
    if prompt is None:
        return None
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# DuckDB star schema
# ---------------------------------------------------------------------------

SCHEMA_DDL: list[str] = [
    """
    CREATE OR REPLACE TABLE model_dim (
        model_id INTEGER PRIMARY KEY,
        model_name VARCHAR NOT NULL,
        prompt_version VARCHAR,
        seed INTEGER,
        temperature DOUBLE,
        role VARCHAR NOT NULL
    )
    """,
    """
    CREATE OR REPLACE TABLE paper_dim (
        paper_id INTEGER PRIMARY KEY,
        doi VARCHAR,
        title VARCHAR,
        authors VARCHAR[],
        publication_date DATE,
        publisher VARCHAR,
        discovered_from VARCHAR,
        openalex_id VARCHAR,
        semantic_scholar_id VARCHAR
    )
    """,
    """
    CREATE OR REPLACE TABLE entity_dim (
        entity_id INTEGER PRIMARY KEY,
        latitude DOUBLE,
        longitude DOUBLE,
        name VARCHAR,
        location_description VARCHAR,
        identifiers VARCHAR,
        ecosystem_type VARCHAR,
        resolution_method VARCHAR NOT NULL
    )
    """,
    """
    CREATE OR REPLACE TABLE event_dim (
        event_id INTEGER PRIMARY KEY,
        date_measured VARCHAR,
        sub_location VARCHAR,
        additional_details VARCHAR
    )
    """,
    """
    CREATE OR REPLACE TABLE extractions_fact (
        fact_id INTEGER PRIMARY KEY,
        source_extraction_id INTEGER NOT NULL,
        paper_id INTEGER NOT NULL REFERENCES paper_dim (paper_id),
        extraction_model_id INTEGER NOT NULL REFERENCES model_dim (model_id),
        entity_id INTEGER NOT NULL REFERENCES entity_dim (entity_id),
        event_id INTEGER NOT NULL REFERENCES event_dim (event_id),
        attribute VARCHAR NOT NULL,
        quantity_raw VARCHAR,
        units_raw VARCHAR,
        qualifiers VARCHAR[] NOT NULL,
        point_value VARCHAR,
        lower VARCHAR,
        upper VARCHAR,
        list_values VARCHAR[],
        tolerance VARCHAR,
        standard_deviation VARCHAR,
        page_number INTEGER,
        confidence DOUBLE,
        probe_score DOUBLE
    )
    """,
    """
    CREATE OR REPLACE TABLE attribution_fact (
        source_extraction_id INTEGER NOT NULL,
        method VARCHAR NOT NULL,
        snippet VARCHAR NOT NULL,
        tokens VARCHAR[] NOT NULL,
        scores DOUBLE[] NOT NULL,
        PRIMARY KEY (source_extraction_id, method)
    )
    """,
]
"""Executed in order (fact table last, once every dim it references
exists) by scripts/build_warehouse.py inside a single transaction —
`CREATE OR REPLACE TABLE` makes each rebuild a full replacement, matching
the fact-grain decision (one row per source `extractions.id`, no
cross-rebuild accumulation — see the build note).

`attribution_fact` is a straight passthrough copy of Postgres
`attributions` — no dedup/skip logic (unlike `extractions_fact`, nothing
here can fail to parse) — keyed by `source_extraction_id` (Postgres
`extractions.id`), not `extractions_fact.fact_id`, since an attribution row
can exist for an extraction that `extractions_fact` skipped (missing
model parse, unknown attribute); such rows are simply unreachable from the
site, which only ever looks up attributions for ids it already has a fact
row for. `token_indices` (JudgementLM prompt-token positions) is
deliberately not carried over — display only needs `tokens`/`scores` in
their stored order, not their position in the judge prompt. See
notes/coastal-crawler/builds/2026-08-19-judge-attribution-display-01.md."""
