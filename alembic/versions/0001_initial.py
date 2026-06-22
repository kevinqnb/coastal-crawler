"""Initial schema: papers, extractions, crawl_state.

Revision ID: 0001
Revises:
Create Date: 2026-06-11

papers
  - doi is the primary cross-source dedup key (nullable, unique).
  - openalex_id and semantic_scholar_id are both nullable so a paper
    discovered by any single source can be inserted without the others.

crawl_state
  - Keyed by source name so each discovery source tracks its own watermark
    independently.  Rows: "openalex", "semantic_scholar", "wiley".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("main",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("doi", sa.String(), nullable=True),
        sa.Column("openalex_id", sa.String(), nullable=True),
        sa.Column("semantic_scholar_id", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("oa_pdf_url", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'discovered'"),
        ),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doi"),
        sa.UniqueConstraint("openalex_id"),
        sa.UniqueConstraint("semantic_scholar_id"),
    )
    op.create_index("ix_papers_status", "papers", ["status"])

    op.create_table(
        "extractions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("data", JSONB(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("provenance", JSONB(), nullable=True),
        sa.Column("latitude", sa.Double(), nullable=True),
        sa.Column("longitude", sa.Double(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extractions_paper_id", "extractions", ["paper_id"])

    # source is the PK — one row per discovery source.
    op.create_table(
        "crawl_state",
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("watermark", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("source"),
    )


def downgrade() -> None:
    op.drop_table("crawl_state")
    op.drop_index("ix_extractions_paper_id", table_name="extractions")
    op.drop_table("extractions")
    op.drop_index("ix_papers_status", table_name="papers")
    op.drop_table("papers")
