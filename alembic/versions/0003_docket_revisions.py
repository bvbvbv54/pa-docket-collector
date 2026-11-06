"""docket revision history for refreshes

Revision ID: 0003
Revises: 0002

A refresh retrieves the current docket sheet and updates the stored record. The
previous and new document snapshots are kept here so an update is auditable and
a "nothing changed" refresh writes no revision.

0001 builds from the live model metadata, so a database created after this model
existed already carries the table. The guard keeps the migration safe on both
kinds of database: one stamped at 0001 before the table existed, and one whose
0001 run created it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.types import JSON

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLE = "docket_revisions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE in inspector.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "docket_id",
            sa.Integer(),
            sa.ForeignKey("dockets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("collection_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "previous_document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "new_document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("previous_record_json", JSON(), nullable=False),
        sa.Column("new_record_json", JSON(), nullable=False),
        sa.Column("changed_fields", JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_docket_revisions_docket_id", TABLE, ["docket_id"])
    op.create_index("ix_docket_revisions_job_id", TABLE, ["job_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes(TABLE)}
    for name in ("ix_docket_revisions_job_id", "ix_docket_revisions_docket_id"):
        if name in indexes:
            op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)
