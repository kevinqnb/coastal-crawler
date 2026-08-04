"""add judgement column to extractions and a votes table

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-28

Supports the results website's validity-voting feature. `votes` stores each
individual valid/invalid vote against an extraction row; `extractions.judgement`
is the majority-vote outcome ('valid'/'invalid'), recomputed on every new vote
so the site's list/detail views don't need to aggregate votes on every read.
NULL judgement means no votes yet. Deliberately keyed on extraction_id, not
paper_id — extraction rows are never overwritten (a new extraction pass adds
new rows), so votes on a prior pass's row stay attached to that row rather
than silently applying to unrelated future rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("extractions", sa.Column("judgement", sa.String(), nullable=True))
    op.create_table(
        "votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "extraction_id",
            sa.Integer(),
            sa.ForeignKey("extractions.id"),
            nullable=False,
        ),
        sa.Column("vote", sa.Boolean(), nullable=False),
        sa.Column("voter_hash", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_votes_extraction_id", "votes", ["extraction_id"])


def downgrade() -> None:
    op.drop_index("ix_votes_extraction_id", table_name="votes")
    op.drop_table("votes")
    op.drop_column("extractions", "judgement")
