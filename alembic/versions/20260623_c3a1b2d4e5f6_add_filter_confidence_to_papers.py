"""add filter_confidence column to papers

Revision ID: c3a1b2d4e5f6
Revises: 95900fcda75f
Create Date: 2026-06-23

Adds filter_confidence (REAL, nullable) to store the logprob-derived
p_true / (p_true + p_false) score from the abstract relevance filter step.
NULL means the paper has not been filtered yet, or the model did not emit
a boolean token (conservative reject, confidence unknown).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a1b2d4e5f6"
down_revision: Union[str, None] = "95900fcda75f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("filter_confidence", sa.REAL(), nullable=True))


def downgrade() -> None:
    op.drop_column("papers", "filter_confidence")
