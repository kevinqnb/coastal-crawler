"""add pdf_inaccessible column to papers

Revision ID: d4e5f6a7b8c9
Revises: c3a1b2d4e5f6
Create Date: 2026-06-24

Adds pdf_inaccessible (BOOLEAN, NOT NULL DEFAULT false) to record papers
whose PDF URL returned 401 or 403.  These papers are skipped by claim_batch
so extraction workers do not repeatedly attempt unreachable PDFs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3a1b2d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("pdf_inaccessible", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("papers", "pdf_inaccessible")
