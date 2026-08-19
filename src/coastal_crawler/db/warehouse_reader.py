"""Read-only DuckDB queries against the star-schema warehouse
(`Settings.warehouse_path`) for the results website.

Every function here takes an open `duckdb.DuckDBPyConnection` — callers get
one from `get_warehouse_connection()`. Connections are opened per request
(read-only), not held open for the app's lifetime: the deferred
public-deployment build is expected to atomically swap in a freshly rebuilt
`.duckdb` file, and a long-lived connection would keep serving the old file
through that swap. See notes/coastal-crawler/builds/2026-08-12-warehouse-site-01.md.

`extractions_fact` already has exact-duplicate rows removed at build time
(see `scripts/build_warehouse.py`'s dedup pass) — no further dedup happens
here; every surviving fact row is a distinct measurement.

Rows are returned as `types.SimpleNamespace` (attribute access, like a
SQLAlchemy `Row`) rather than raw tuples, so call sites and Jinja templates
can use `row.attribute` the same way they did against the old `store.py`
query functions.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Generator

import duckdb

from coastal_crawler.config import get_settings


@contextmanager
def get_warehouse_connection() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Yield a read-only connection to the current warehouse snapshot."""
    con = duckdb.connect(get_settings().warehouse_path, read_only=True)
    try:
        yield con
    finally:
        con.close()


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> list[SimpleNamespace]:
    result = con.execute(sql, params)
    columns = [d[0] for d in result.description]
    return [SimpleNamespace(**dict(zip(columns, row))) for row in result.fetchall()]


def _one(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> SimpleNamespace | None:
    rows = _rows(con, sql, params)
    return rows[0] if rows else None


def _escape_ilike(value: str) -> str:
    """Escape a user-typed string for use inside a DuckDB `ILIKE ... ESCAPE
    '\\'` pattern, so a literal `%`/`_` the user typed doesn't act as a
    wildcard. SQLAlchemy's `autoescape=True` did this for Postgres queries;
    that responsibility moves here now that these reads are DuckDB SQL."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------

def get_paper(con: duckdb.DuckDBPyConnection, paper_id: int) -> SimpleNamespace | None:
    """One paper by id (`paper_dim` — every paper regardless of pipeline
    status, same coverage as the old Postgres `store.get_paper`)."""
    return _one(
        con,
        "SELECT paper_id AS id, doi, title, authors, publication_date, publisher, "
        "discovered_from FROM paper_dim WHERE paper_id = ?",
        [paper_id],
    )


def list_papers(
    con: duckdb.DuckDBPyConnection,
    page: int = 1,
    page_size: int = 25,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
    entity_id: int | None = None,
) -> tuple[list[SimpleNamespace], int]:
    """Paginated distinct papers with at least one filter-matching fact
    row. One row per paper: `.id`, `.title`, `.authors`,
    `.publication_date`, `.doi`, `.extraction_count`.

    Ordered by `MAX(source_extraction_id)` per paper, descending —
    `extractions_fact` has no timestamp column (unlike the old
    `Extraction.created_at`), so the fact row's source Postgres id (a
    monotonically increasing serial, assigned at insert time) is used as
    the "most recently extracted first" proxy instead.
    """
    conditions = ["1=1"]
    params: list[Any] = []
    if attribute:
        conditions.append("f.attribute = ?")
        params.append(attribute)
    if ecosystem_type:
        conditions.append("e.ecosystem_type = ?")
        params.append(ecosystem_type)
    if entity_id is not None:
        conditions.append("f.entity_id = ?")
        params.append(entity_id)
    if title:
        conditions.append("p.title ILIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_ilike(title)}%")
    where = " AND ".join(conditions)

    total_row = con.execute(
        f"""
        SELECT COUNT(DISTINCT f.paper_id)
        FROM extractions_fact f
        JOIN entity_dim e ON e.entity_id = f.entity_id
        JOIN paper_dim p ON p.paper_id = f.paper_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    assert total_row is not None
    total = total_row[0]

    rows = _rows(
        con,
        f"""
        SELECT p.paper_id AS id, p.title, p.authors, p.publication_date, p.doi,
               COUNT(*) AS extraction_count
        FROM extractions_fact f
        JOIN entity_dim e ON e.entity_id = f.entity_id
        JOIN paper_dim p ON p.paper_id = f.paper_id
        WHERE {where}
        GROUP BY p.paper_id, p.title, p.authors, p.publication_date, p.doi
        ORDER BY MAX(f.source_extraction_id) DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    )
    return rows, total


def list_ecosystem_types(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Distinct non-null ecosystem types among entities with at least one
    fact row (every `entity_dim` row already meets that bar — entities are
    only created from contributing extraction rows), for the site's facet
    filter."""
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT ecosystem_type FROM entity_dim "
            "WHERE ecosystem_type IS NOT NULL ORDER BY ecosystem_type"
        ).fetchall()
    ]


def search_papers(con: duckdb.DuckDBPyConnection, query: str, limit: int) -> list[SimpleNamespace]:
    """Title-search papers that have at least one fact row, for the site's
    search box. Checks `extractions_fact`, not just `paper_dim` existence —
    `paper_dim` holds every Postgres paper regardless of status (11,004
    rows including never-extracted ones), so a `paper_dim`-only check would
    surface papers that show as empty on their own detail page. Ranked
    prefix-match-first, then shortest title, same as the old
    `search_papers_by_title`.
    """
    escaped = _escape_ilike(query)
    substring_pattern = f"%{escaped}%"
    prefix_pattern = f"{escaped}%"
    return _rows(
        con,
        """
        SELECT DISTINCT p.paper_id AS id, p.title, p.authors, p.publication_date, p.doi
        FROM paper_dim p
        JOIN extractions_fact f ON f.paper_id = p.paper_id
        WHERE p.title ILIKE ? ESCAPE '\\'
        ORDER BY (CASE WHEN p.title ILIKE ? ESCAPE '\\' THEN 0 ELSE 1 END), LENGTH(p.title)
        LIMIT ?
        """,
        [substring_pattern, prefix_pattern, limit],
    )


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def get_entity(con: duckdb.DuckDBPyConnection, entity_id: int) -> SimpleNamespace | None:
    return _one(
        con,
        "SELECT entity_id AS id, name, latitude, longitude, location_description, "
        "identifiers, ecosystem_type FROM entity_dim WHERE entity_id = ?",
        [entity_id],
    )


def map_entities(
    con: duckdb.DuckDBPyConnection,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> list[SimpleNamespace]:
    """Coordinate-bearing entities matching the given filters, with a
    per-entity distinct-paper count, for the `/` map."""
    conditions = ["e.latitude IS NOT NULL", "e.longitude IS NOT NULL"]
    params: list[Any] = []
    if attribute:
        conditions.append("f.attribute = ?")
        params.append(attribute)
    if ecosystem_type:
        conditions.append("e.ecosystem_type = ?")
        params.append(ecosystem_type)
    if title:
        conditions.append("p.title ILIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_ilike(title)}%")
    where = " AND ".join(conditions)

    return _rows(
        con,
        f"""
        SELECT e.entity_id, e.name AS entity_name, e.latitude, e.longitude,
               COUNT(DISTINCT f.paper_id) AS paper_count
        FROM extractions_fact f
        JOIN entity_dim e ON e.entity_id = f.entity_id
        JOIN paper_dim p ON p.paper_id = f.paper_id
        WHERE {where}
        GROUP BY e.entity_id, e.name, e.latitude, e.longitude
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Fact rows (pages / export)
# ---------------------------------------------------------------------------

_FACT_ROW_SELECT = """
    SELECT f.source_extraction_id AS id, f.paper_id, f.attribute,
           f.quantity_raw AS value, f.units_raw AS units, f.confidence,
           f.probe_score,
           e.entity_id, e.name AS entity_name, e.identifiers,
           e.location_description AS location, e.ecosystem_type,
           ev.date_measured AS event_date, ev.sub_location, ev.additional_details
    FROM extractions_fact f
    JOIN entity_dim e ON e.entity_id = f.entity_id
    JOIN event_dim ev ON ev.event_id = f.event_id
"""


def pages_for_paper(
    con: duckdb.DuckDBPyConnection,
    paper_id: int,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> list[tuple[int | None, int]]:
    """(page_number, count) for one paper's fact rows, grouped and ordered
    by page — `page_number=None` (no page match at extraction time) sorts
    last."""
    conditions = ["f.paper_id = ?"]
    params: list[Any] = [paper_id]
    if attribute:
        conditions.append("f.attribute = ?")
        params.append(attribute)
    if ecosystem_type:
        conditions.append("e.ecosystem_type = ?")
        params.append(ecosystem_type)
    where = " AND ".join(conditions)
    rows = con.execute(
        f"""
        SELECT f.page_number, COUNT(*)
        FROM extractions_fact f
        JOIN entity_dim e ON e.entity_id = f.entity_id
        WHERE {where}
        GROUP BY f.page_number
        ORDER BY f.page_number ASC NULLS LAST
        """,
        params,
    ).fetchall()
    return [(page_number, count) for page_number, count in rows]


def page_extractions(
    con: duckdb.DuckDBPyConnection,
    paper_id: int,
    page_number: int,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> list[SimpleNamespace]:
    """All fact rows on one page of one paper, filter-scoped — unpaginated
    (one page's measurement count is small)."""
    conditions = ["f.paper_id = ?", "f.page_number = ?"]
    params: list[Any] = [paper_id, page_number]
    if attribute:
        conditions.append("f.attribute = ?")
        params.append(attribute)
    if ecosystem_type:
        conditions.append("e.ecosystem_type = ?")
        params.append(ecosystem_type)
    if title:
        conditions.append(
            "f.paper_id IN (SELECT paper_id FROM paper_dim WHERE title ILIKE ? ESCAPE '\\')"
        )
        params.append(f"%{_escape_ilike(title)}%")
    where = " AND ".join(conditions)
    return _rows(con, f"{_FACT_ROW_SELECT} WHERE {where} ORDER BY f.source_extraction_id", params)


def export_rows(
    con: duckdb.DuckDBPyConnection,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> list[SimpleNamespace]:
    """Every fact row matching the given filters, unpaginated, joined to
    its paper — for `/export.csv`."""
    conditions = ["1=1"]
    params: list[Any] = []
    if attribute:
        conditions.append("f.attribute = ?")
        params.append(attribute)
    if ecosystem_type:
        conditions.append("e.ecosystem_type = ?")
        params.append(ecosystem_type)
    if title:
        conditions.append("p.title ILIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_ilike(title)}%")
    where = " AND ".join(conditions)
    return _rows(
        con,
        f"""
        SELECT f.source_extraction_id AS id, f.paper_id, f.attribute,
               f.quantity_raw AS value, f.units_raw AS units, f.confidence,
               f.probe_score,
               e.entity_id, e.name AS entity_name, e.identifiers,
               e.location_description, e.ecosystem_type, e.latitude AS entity_latitude,
               e.longitude AS entity_longitude,
               ev.date_measured AS event_date, ev.sub_location, ev.additional_details,
               p.title, p.doi, p.authors, p.publication_date
        FROM extractions_fact f
        JOIN entity_dim e ON e.entity_id = f.entity_id
        JOIN event_dim ev ON ev.event_id = f.event_id
        JOIN paper_dim p ON p.paper_id = f.paper_id
        WHERE {where}
        ORDER BY f.source_extraction_id
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Attributions
# ---------------------------------------------------------------------------

def get_attributions(
    con: duckdb.DuckDBPyConnection, extraction_ids: list[int]
) -> list[SimpleNamespace]:
    """One row per `(extraction_id, method)` for the given extraction ids —
    `.extraction_id`, `.method`, `.snippet`, `.tokens`, `.scores`. Empty
    list in, empty list out (DuckDB's `IN ()` needs at least one param)."""
    if not extraction_ids:
        return []
    placeholders = ", ".join("?" for _ in extraction_ids)
    return _rows(
        con,
        f"""
        SELECT source_extraction_id AS extraction_id, method, snippet, tokens, scores
        FROM attribution_fact
        WHERE source_extraction_id IN ({placeholders})
        ORDER BY source_extraction_id, method
        """,
        list(extraction_ids),
    )
