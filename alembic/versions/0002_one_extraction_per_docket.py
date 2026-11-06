"""one extraction per docket per job

Revision ID: 0002
Revises: 0001

A restart re-downloads the docket sheet, and the portal re-signs it, so the PDF
bytes and their hash move. Keying extraction results on the source document let
a restart append a second export row for the same docket. The result is keyed on
(job_id, docket_id) instead; the newest document remains the evidence pointer.
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_extraction_job_docket"


def upgrade() -> None:
    bind = op.get_bind()
    # Collapse any pre-existing duplicates before the index goes on: keep the
    # newest result for each (job, docket) and drop the older append.
    bind.exec_driver_sql(
        """
        DELETE FROM extraction_results
        WHERE docket_id IS NOT NULL
          AND id NOT IN (
            SELECT MAX(id) FROM extraction_results
            WHERE docket_id IS NOT NULL
            GROUP BY job_id, docket_id
          )
        """
    )
    bind.exec_driver_sql(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
        "ON extraction_results (job_id, docket_id)"
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(f"DROP INDEX IF EXISTS {INDEX_NAME}")
