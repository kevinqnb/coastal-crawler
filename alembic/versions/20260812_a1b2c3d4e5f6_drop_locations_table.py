"""drop locations table

Revision ID: a1b2c3d4e5f6
Revises: f5a6b7c8d9e0
Create Date: 2026-08-12

Drops the `locations` table and `extractions.location_id` — entity
resolution now lives entirely in scripts/build_warehouse.py's DuckDB
rebuild (see coastal_crawler.warehouse.resolve_entities, the ported
version of the algorithm this table's data came from), not in Postgres.
See notes/coastal-crawler/builds/2026-08-12-warehouse-init-01.md
("Postgres cleanup", done_when item 5) for the full rationale — this is a
deliberate, user-approved consequence: the results website's
location-dependent routes/queries (store.py's map_locations/get_location,
site/app.py's /locations/{id}/papers, /export.csv's location columns) stop
working until notes/coastal-crawler/builds/2026-08-12-warehouse-site-01.md
re-points them at the warehouse.

Irreversible in practice even though downgrade() recreates the table shape:
the original `locations` rows and `extractions.location_id` backfill are
gone once this runs — downgrade() only restores empty structure, not data.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_extractions_location_id", table_name="extractions")
    op.drop_column("extractions", "location_id")
    op.drop_table("locations")


def downgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Double(), nullable=True),
        sa.Column("longitude", sa.Double(), nullable=True),
        sa.Column("resolution_method", sa.String(), nullable=False),
        sa.Column("resolution_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "extractions",
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id"), nullable=True),
    )
    op.create_index("ix_extractions_location_id", "extractions", ["location_id"])
