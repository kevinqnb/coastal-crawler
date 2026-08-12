#!/usr/bin/env python3
"""One-shot resolution of every extraction row into a canonical `locations` row.

There is no location entity anywhere in the schema — every extraction row
independently embeds its own copy of the entity fields (name/ecosystem_type/
location/latitude/longitude, see measurement_schema.py's EntitySchema). This
script derives a canonical `locations` table from that flat, denormalized
data and backfills extractions.location_id, so downstream views/exports can
be organized by physical site instead of by paper.

Resolution, in order:
  1. Coordinate-bearing rows are clustered by proximity: union-find over the
     *distinct* (latitude, longitude) points present (not over every row —
     identical points always merge anyway, so clustering distinct points and
     mapping rows back afterward gives the same result for far fewer
     comparisons), unioning any pair within LOCATION_DISTANCE_THRESHOLD_KM
     great-circle (haversine) distance of each other. One `locations` row
     per resulting cluster; latitude/longitude = the centroid of the
     cluster's member points; name = the most common non-null `name` among
     the cluster's member rows. resolution_method='coordinate'.
  2. Rows with no coordinates are matched by name: the `name` field is
     normalized (lowercased, punctuation stripped, whitespace collapsed),
     then fuzzy-matched (difflib.SequenceMatcher.ratio) against the
     normalized names of existing coordinate-less locations only — a
     coordinate-less row is never matched against a 'coordinate' location.
     A hit >= LOCATION_NAME_SIMILARITY_THRESHOLD merges into the first
     matching location (in creation order); otherwise a new location is
     created. resolution_method='name'.
  3. Rows with no coordinates and no usable name (empty/None after
     normalization) each become their own location rather than being
     blindly merged with each other. resolution_method='unresolved'.

Refuses to run (raises) if any extraction already has a location_id set —
this script is a one-shot job, not designed to be safely re-run after a
threshold change (see notes/coastal-crawler/builds/
2026-08-11-location-resolution-01.md, "Out of scope"). All writes happen in
a single transaction: because re-running isn't supported, a crash partway
through must leave zero rows written rather than a half-resolved DB.

Usage:
    uv run scripts/resolve_locations.py
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

import structlog
from sqlalchemy import select, update

from coastal_crawler.config import get_settings
from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Extraction, Location

log = structlog.get_logger(__name__)

_EARTH_RADIUS_KM = 6371.0088
_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    normalized = _WHITESPACE_RE.sub(" ", _PUNCT_RE.sub("", name.lower())).strip()
    return normalized or None


def _parse_coord(raw: str | None, extraction_id: int, field: str) -> float | None:
    """None means genuinely absent (no coordinates); anything else must parse."""
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"extraction {extraction_id}: unparseable {field} value {raw!r} — "
            "expected either absent or a numeric string"
        ) from None


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


def _cluster_by_coordinates(
    coord_rows: list[tuple[int, str | None, float, float]], distance_threshold_km: float
) -> tuple[list[Location], dict[int, Location]]:
    """Union-find over distinct (lat, lon) points. Returns (locations, row_id -> Location)."""
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
    for ext_id, name, lat, lon in coord_rows:
        root = uf.find(point_index[(lat, lon)])
        cluster_members[root].append((ext_id, name))

    locations: list[Location] = []
    row_to_location: dict[int, Location] = {}
    for root, members in cluster_members.items():
        points = cluster_points[root]
        centroid_lat = sum(p[0] for p in points) / len(points)
        centroid_lon = sum(p[1] for p in points) / len(points)

        name_counts = Counter(name for _, name in members if name)
        loc_name = None
        if name_counts:
            top_count = max(name_counts.values())
            loc_name = next(name for _, name in members if name and name_counts[name] == top_count)

        location = Location(
            name=loc_name,
            latitude=centroid_lat,
            longitude=centroid_lon,
            resolution_method="coordinate",
            resolution_key=None,
        )
        locations.append(location)
        for ext_id, _ in members:
            row_to_location[ext_id] = location

    return locations, row_to_location


def _match_by_name(
    nocoord_rows: list[tuple[int, str | None]], similarity_threshold: float
) -> tuple[list[Location], dict[int, Location]]:
    """Greedy fuzzy-match on normalized names, in row (id) order. Returns
    (locations, row_id -> Location)."""
    rows_by_normalized: dict[str | None, list[tuple[int, str | None]]] = defaultdict(list)
    for ext_id, name in nocoord_rows:
        rows_by_normalized[_normalize_name(name)].append((ext_id, name))

    locations: list[Location] = []
    row_to_location: dict[int, Location] = {}
    matched_locations: list[tuple[str, Location]] = []  # (normalized_name, Location)

    for normalized, members in rows_by_normalized.items():
        if normalized is None:
            for ext_id, _ in members:
                location = Location(
                    name=None,
                    latitude=None,
                    longitude=None,
                    resolution_method="unresolved",
                    resolution_key=None,
                )
                locations.append(location)
                row_to_location[ext_id] = location
            continue

        target = next(
            (loc for existing, loc in matched_locations
             if SequenceMatcher(None, normalized, existing).ratio() >= similarity_threshold),
            None,
        )
        if target is None:
            target = Location(
                name=members[0][1],
                latitude=None,
                longitude=None,
                resolution_method="name",
                resolution_key=normalized,
            )
            locations.append(target)
            matched_locations.append((normalized, target))

        for ext_id, _ in members:
            row_to_location[ext_id] = target

    return locations, row_to_location


def main() -> None:
    settings = get_settings()

    with get_session() as session:
        already_resolved = session.execute(
            select(Extraction.id).where(Extraction.location_id.is_not(None)).limit(1)
        ).first()
        if already_resolved is not None:
            raise RuntimeError(
                "extractions.location_id is already set on at least one row — "
                "resolve_locations.py is a one-shot script and refuses to run "
                "twice (safe re-resolution is out of scope; see "
                "notes/coastal-crawler/builds/2026-08-11-location-resolution-01.md)"
            )

        rows = session.execute(
            select(
                Extraction.id,
                Extraction.data["name"].astext,
                Extraction.data["latitude"].astext,
                Extraction.data["longitude"].astext,
            ).order_by(Extraction.id)
        ).all()

        log.info("extractions_loaded", count=len(rows))

        coord_rows: list[tuple[int, str | None, float, float]] = []
        nocoord_rows: list[tuple[int, str | None]] = []
        for ext_id, name, lat_raw, lon_raw in rows:
            lat = _parse_coord(lat_raw, ext_id, "latitude")
            lon = _parse_coord(lon_raw, ext_id, "longitude")
            if lat is None or lon is None:
                if lat is not None or lon is not None:
                    raise ValueError(
                        f"extraction {ext_id}: latitude/longitude must both be present or "
                        f"both absent (got latitude={lat_raw!r}, longitude={lon_raw!r})"
                    )
                nocoord_rows.append((ext_id, name))
            else:
                coord_rows.append((ext_id, name, lat, lon))

        coord_locations, row_to_location = _cluster_by_coordinates(
            coord_rows, settings.location_distance_threshold_km
        )
        name_locations, name_row_to_location = _match_by_name(
            nocoord_rows, settings.location_name_similarity_threshold
        )
        row_to_location.update(name_row_to_location)

        if len(row_to_location) != len(rows):
            raise RuntimeError(
                f"resolved {len(row_to_location)} of {len(rows)} extraction rows — "
                "every row must get a location"
            )

        all_locations = coord_locations + name_locations
        session.add_all(all_locations)
        session.flush()  # assign ids

        ext_ids_by_location_id: dict[int, list[int]] = defaultdict(list)
        for ext_id, location in row_to_location.items():
            ext_ids_by_location_id[location.id].append(ext_id)

        for location_id, ext_ids in ext_ids_by_location_id.items():
            session.execute(
                update(Extraction)
                .where(Extraction.id.in_(ext_ids))
                .values(location_id=location_id)
            )

        by_method = Counter(loc.resolution_method for loc in all_locations)
        log.info(
            "resolved",
            extraction_rows=len(rows),
            locations_created=len(all_locations),
            from_coordinates=by_method["coordinate"],
            from_names=by_method["name"],
            unresolved=by_method["unresolved"],
        )


if __name__ == "__main__":
    main()
