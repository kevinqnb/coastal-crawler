"""add paper_representations table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-19

Queue/tracking table for the new `coastal-crawler repr` stage (RepresentationLM
term-embedding collection). One row per (paper, run_id); embeddings themselves
live on disk under REPR_DIR/<run_id>/shards, never in Postgres. papers.status
is untouched — this stage branches off cleaned text in parallel with
extraction.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_representations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("n_occurrences", sa.Integer(), nullable=True),
        sa.Column("shard_path", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("terms_hash", sa.String(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("paper_id", "run_id", name="uq_paper_representations_paper_run"),
    )
    op.create_index(
        "ix_paper_representations_run_status", "paper_representations", ["run_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_paper_representations_run_status", table_name="paper_representations")
    op.drop_table("paper_representations")
