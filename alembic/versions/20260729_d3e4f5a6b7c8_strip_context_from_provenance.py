"""strip context from extractions.provenance too

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-29

c2d3e4f5a6b7 stripped `context` out of extractions.data but missed that
adapter.py's _to_result() also copied the same ~55-90KB OCR text into
`provenance['context']` — every row was carrying it twice. Confirmed live:
all 16,375 rows still had provenance ? 'context' after that migration ran,
and pg_total_relation_size('extractions') was still ~497MB post-VACUUM-FULL
(down from 961MB, but provenance's copy alone accounted for the rest).
adapter.py's _to_result() was already fixed to stop writing it there for
new rows; this is the backfill-side cleanup for existing ones.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE extractions SET provenance = provenance - 'context' WHERE provenance ? 'context'")


def downgrade() -> None:
    # Not reversible — see c2d3e4f5a6b7's downgrade note.
    pass
