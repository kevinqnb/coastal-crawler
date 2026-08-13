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
# Qualifier / value parsing (extractions.data->>'value' -> qualifier_dim row)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedQualifier:
    """One row's parsed `value` shape. `quantity` is the single
    representative raw number for shapes that have one ('exact': the
    number itself; 'plus_minus': the center) — None for shapes with no
    single point value ('range', 'less_than_or_equal', 'greater_than').
    All numbers here are in the *raw*, pre-unit-conversion scale — the
    caller applies convert_to_canonical()'s factor uniformly to whichever
    of these fields are set.
    """

    confidence_region: str  # 'exact' | 'range' | 'plus_minus' | 'less_than_or_equal' | 'greater_than'
    quantity: float | None
    range_min: float | None = None
    range_max: float | None = None
    confidence_min: float | None = None
    confidence_max: float | None = None
    less_than_or_equal: float | None = None
    greater_than: float | None = None


_SUPERSCRIPT_TABLE = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-", "−": "-", "‑": "-",
})

_NUM = r"-?\d+(?:\.\d+)?"
_RANGE_DASH_RE = re.compile(rf"^\(?\s*({_NUM})\s*[-–—‑]\s*({_NUM})\s*\)?%?$")
_RANGE_TO_RE = re.compile(rf"^\s*({_NUM})\s*to\s*({_NUM})\s*$", re.IGNORECASE)
_PLUS_MINUS_RE = re.compile(rf"^\s*({_NUM})\s*(?:±|\+/-|\+-)\s*({_NUM})\s*%?$")
_LTE_RE = re.compile(rf"^\s*(?:<=|≤)\s*({_NUM})\s*%?$")
_LT_RE = re.compile(rf"^\s*<\s*({_NUM})\s*%?$")
_GTE_RE = re.compile(rf"^\s*(?:>=|≥)\s*({_NUM})\s*%?$")
_GT_RE = re.compile(rf"^\s*>\s*({_NUM})\s*%?$")
_SCI_E_RE = re.compile(rf"^\s*({_NUM})\s*[eE]\s*([+-]?\d+)\s*$")
_SCI_MULT_RE = re.compile(rf"^\s*({_NUM})\s*[×x]\s*10\^?(-?\d+)\s*$")
_PLAIN_NUMBER_RE = re.compile(rf"^\s*({_NUM})\s*\*?%?\s*$")


def parse_qualifier(raw_value: str | None) -> ParsedQualifier | None:
    """Parse a raw `extractions.data->>'value'` string into a qualifier
    shape, or None if unparseable (caller skips and logs the row). See the
    build note's qualifier_dim section for the full shape catalogue this
    was derived from (live-data value-string distribution).

    Comma thousands separators and Unicode super/subscript exponents are
    normalized before matching; a trailing footnote `*` and an incidental
    `%` are tolerated (stripped) inside an otherwise-numeric match — this
    function only judges numeric *shape*, not unit validity. Whether a raw
    `%` reading is actually meaningful for the attribute in question is a
    units question, decided separately by convert_to_canonical().
    """
    if raw_value is None:
        return None
    s = raw_value.strip()
    if not s:
        return None
    s = s.replace(",", "")
    s = s.translate(_SUPERSCRIPT_TABLE)

    m = _RANGE_DASH_RE.match(s) or _RANGE_TO_RE.match(s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return ParsedQualifier("range", None, range_min=min(lo, hi), range_max=max(lo, hi))

    m = _PLUS_MINUS_RE.match(s)
    if m:
        center, margin = float(m.group(1)), float(m.group(2))
        return ParsedQualifier(
            "plus_minus", center, confidence_min=center - margin, confidence_max=center + margin
        )

    m = _LTE_RE.match(s) or _LT_RE.match(s)
    if m:
        return ParsedQualifier("less_than_or_equal", None, less_than_or_equal=float(m.group(1)))

    m = _GTE_RE.match(s) or _GT_RE.match(s)
    if m:
        return ParsedQualifier("greater_than", None, greater_than=float(m.group(1)))

    m = _SCI_E_RE.match(s) or _SCI_MULT_RE.match(s)
    if m:
        return ParsedQualifier("exact", float(m.group(1)) * (10 ** int(m.group(2))))

    m = _PLAIN_NUMBER_RE.match(s)
    if m:
        return ParsedQualifier("exact", float(m.group(1)))

    return None


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
    "quantity_raw", "units_raw", "quantity_canonical", "units_canonical",
    "qualifier_id", "page_number", "confidence",
)


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
        key = tuple(row[f] for f in _FACT_DEDUP_KEY_FIELDS)
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
    CREATE OR REPLACE TABLE qualifier_dim (
        qualifier_id INTEGER PRIMARY KEY,
        confidence_region VARCHAR NOT NULL,
        confidence_min DOUBLE,
        confidence_max DOUBLE,
        range_min DOUBLE,
        range_max DOUBLE,
        less_than_or_equal DOUBLE,
        greater_than DOUBLE
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
        quantity_canonical DOUBLE,
        units_canonical VARCHAR,
        qualifier_id INTEGER REFERENCES qualifier_dim (qualifier_id),
        page_number INTEGER,
        confidence DOUBLE
    )
    """,
]
"""Executed in order (fact table last, once every dim it references
exists) by scripts/build_warehouse.py inside a single transaction —
`CREATE OR REPLACE TABLE` makes each rebuild a full replacement, matching
the fact-grain decision (one row per source `extractions.id`, no
cross-rebuild accumulation — see the build note)."""
