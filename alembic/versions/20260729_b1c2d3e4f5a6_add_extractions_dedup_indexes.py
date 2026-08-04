"""add indexes to speed up the results website's list/filter queries

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e1f2
Create Date: 2026-07-29

The results website's list view (site/app.py's list_view -> db/store.py's
list_extractions) dedupes/sorts/filters on JSONB fields (data->>'attribute',
'value', 'units', 'ecosystem_type') with no supporting index. Every row also
carries a ~55KB embedded copy of its paper's OCR text in a `context` key, so
computing any of those expressions without an index means detoasting that
whole blob per row — confirmed via EXPLAIN ANALYZE taking 6+ seconds for a
single query against ~15,700 rows, well before this table grows further.

These indexes let Postgres answer the dedup/sort/filter step from the index
alone, without touching the JSONB heap value at all except for the small
number of winning rows actually displayed on a page.

Uses raw op.execute for the composite index since expression indexes with an
explicit per-column sort direction (`id DESC`, to match list_extractions'
`ORDER BY ... id DESC` dedup tie-break) aren't expressible through
op.create_index's column-list interface.

Run as the DB-owning `quinnk` OS user directly on the Postgres host (see
CLAUDE.md's migrations section) — `coastal_app` cannot run DDL.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Matches list_extractions' dedup key + id DESC tie-break exactly, with
    # created_at included so the final ORDER BY can also be answered from
    # the index alone. INCLUDE columns can't be expressions, so
    # ecosystem_type isn't covered here — see the standalone index below.
    op.execute(
        """
        CREATE INDEX ix_extractions_dedup_key ON extractions (
            paper_id,
            (data->>'attribute'),
            (data->>'value'),
            (data->>'units'),
            id DESC
        ) INCLUDE (created_at)
        """
    )
    # Supports filtering by attribute alone (WHERE data->>'attribute' = ...)
    # without paper_id being constrained first.
    op.execute("CREATE INDEX ix_extractions_attribute ON extractions ((data->>'attribute'))")
    # Supports filtering by ecosystem_type and list_ecosystem_types()'s
    # DISTINCT query.
    op.execute(
        "CREATE INDEX ix_extractions_ecosystem_type ON extractions ((data->>'ecosystem_type'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_extractions_ecosystem_type")
    op.execute("DROP INDEX IF EXISTS ix_extractions_attribute")
    op.execute("DROP INDEX IF EXISTS ix_extractions_dedup_key")
