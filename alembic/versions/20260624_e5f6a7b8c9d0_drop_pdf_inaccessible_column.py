"""drop pdf_inaccessible column from papers

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-24

Removes the pdf_inaccessible boolean column added in d4e5f6a7b8c9.
Inaccessibility is now represented by status='inaccessible', set during
the filter stage before the LLM relevance check.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("papers", "pdf_inaccessible")


def downgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("pdf_inaccessible", sa.Boolean(), nullable=False, server_default="false"),
    )
