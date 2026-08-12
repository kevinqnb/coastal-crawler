"""add page_number/page_matched to extractions

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-08-12

Persists site/snippets.py's find_snippet() heuristic result on each
extraction row at write time instead of recomputing it live per request.
NULL means not yet computed (existing rows, until scripts/backfill_page_numbers.py
runs) or find_snippet() found no page tags at all to search.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("extractions", sa.Column("page_number", sa.Integer(), nullable=True))
    op.add_column("extractions", sa.Column("page_matched", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("extractions", "page_matched")
    op.drop_column("extractions", "page_number")
