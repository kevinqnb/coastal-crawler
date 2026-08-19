"""add judge columns to extractions and an attributions table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-19

Supports the new `coastal-crawler judge` pipeline stage (see
notes/coastal-crawler/builds/2026-08-18-judgement-attribution-01.md).

`extractions.probe_score` is the trained-probe validity score — distinct
from `extractions.confidence` (existing REAL stub column, now written by
JudgementLM's next-token p_true) and from `extractions.judgement` (the
pre-existing human-vote majority outcome; unrelated to LLM judgement).

`extractions.judge_status` is the claim/queue state for the new stage
(NULL/'pending'/'judging'/'judged'/'judge_failed'), mirroring
`papers.status`'s claim-batch pattern but at extraction-row granularity.
Deliberately **not backfilled** for existing rows — they stay NULL
(structurally invisible to claim_batch_for_judge's `WHERE judge_status =
'pending'` filter) per this build's "going forward only, no backfill of
pre-existing extracted rows" requirement. Only newly inserted extraction
rows (store.py's insert_extraction(), going forward) get 'pending'.

`extractions.judge_error` mirrors `papers.error` — descriptive text on a
judge failure, fail-loud per CLAUDE.md rather than a silent skip.

`attributions` stores one row per (extraction_id, method) for both
ContrastiveGradientAttribution and ProbeAttribution scores. `scores`/
`token_indices`/`tokens` are JSONB arrays (one score per context token, in
the same order); `snippet` is the exact context string the judge/attribution
call was run against (the same text find_snippet() already produces).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("extractions", sa.Column("probe_score", sa.REAL(), nullable=True))
    op.add_column("extractions", sa.Column("judge_error", sa.Text(), nullable=True))
    op.add_column("extractions", sa.Column("judge_status", sa.String(), nullable=True))
    op.create_index("ix_extractions_judge_status", "extractions", ["judge_status"])

    op.create_table(
        "attributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "extraction_id",
            sa.Integer(),
            sa.ForeignKey("extractions.id"),
            nullable=False,
        ),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("scores", postgresql.JSONB(), nullable=False),
        sa.Column("token_indices", postgresql.JSONB(), nullable=False),
        sa.Column("tokens", postgresql.JSONB(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_attributions_extraction_id", "attributions", ["extraction_id"])


def downgrade() -> None:
    op.drop_index("ix_attributions_extraction_id", table_name="attributions")
    op.drop_table("attributions")
    op.drop_index("ix_extractions_judge_status", table_name="extractions")
    op.drop_column("extractions", "judge_status")
    op.drop_column("extractions", "judge_error")
    op.drop_column("extractions", "probe_score")
