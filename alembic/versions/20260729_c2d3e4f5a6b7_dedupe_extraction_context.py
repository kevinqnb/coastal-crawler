"""move OCR context out of extractions.data into a per-paper table

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-29

Every extraction row embedded a full copy of its paper's OCR text in
`data->'context'` (ExtractionLM merges the per-document context dict into
every measurement record it extracts — see extraction/extraction_lm.py's
`fit()`). For a paper with N measurements that's N copies of the same
~55KB text, which is why `extractions` was 914MB for only ~15,700 rows
(virtually all of it TOAST-stored context) and why every JSONB field
extraction on that table forced an expensive per-row detoast. Migration
b1c2d3e4f5a6's indexes turned out not to be enough on their own — confirmed
via EXPLAIN ANALYZE that even a forced Index Scan over all rows still took
6+ seconds, because Postgres detoasts the JSONB value per row regardless of
which index served the scan.

This migration:
1. Creates `paper_ocr_context` — one row per paper.
2. Backfills it from any one extraction row per paper that still has
   `data->'context'` (they're identical copies from the same OCR run, so any
   one of them is representative).
3. Strips the `context` key from every extractions.data value.

VACUUM FULL is intentionally NOT run here — it takes an ACCESS EXCLUSIVE
lock and should be run manually right after this migration lands (see
WEBSITE.md), once the row counts have been sanity-checked.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_ocr_context",
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), primary_key=True),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        """
        INSERT INTO paper_ocr_context (paper_id, context)
        SELECT DISTINCT ON (paper_id) paper_id, data->>'context'
        FROM extractions
        WHERE data ? 'context' AND data->>'context' IS NOT NULL
        ORDER BY paper_id, id DESC
        """
    )
    op.execute("UPDATE extractions SET data = data - 'context' WHERE data ? 'context'")


def downgrade() -> None:
    # Data merge-back is intentionally not supported — that would require
    # re-embedding context into every extraction row's data, defeating the
    # point of this migration. Restore from a backup if you need to revert.
    op.drop_table("paper_ocr_context")
