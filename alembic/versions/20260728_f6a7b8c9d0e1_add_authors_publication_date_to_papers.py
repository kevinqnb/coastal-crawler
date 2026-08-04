"""add authors and publication_date columns to papers

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-28

Adds authors (JSONB, list of strings) and publication_date (DATE) to papers.
Both are needed by the results website's per-datapoint metadata display.
Discovery source mappers (sources/openalex.py, sources/semantic_scholar.py,
sources/wiley.py) previously discarded this data — publication_date was only
used transiently to compute a watermark, and no author field was fetched at
all. Existing rows are left NULL; see scripts/backfill_authors.py for a
one-off DOI-keyed Crossref backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("authors", postgresql.JSONB(), nullable=True))
    op.add_column("papers", sa.Column("publication_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("papers", "publication_date")
    op.drop_column("papers", "authors")
