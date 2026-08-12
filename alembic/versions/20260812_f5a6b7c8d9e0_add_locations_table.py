"""add locations table

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-08-12

Adds the canonical `locations` table and `extractions.location_id`, backfilled
by scripts/resolve_locations.py — see that script and
notes/coastal-crawler/builds/2026-08-11-location-resolution-01.md for the
resolution approach (coordinate proximity clustering, falling back to fuzzy
name matching when coordinates are absent on both sides of a candidate match).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_extractions_location_id", table_name="extractions")
    op.drop_column("extractions", "location_id")
    op.drop_table("locations")
