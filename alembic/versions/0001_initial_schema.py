"""Initial normalized collection schema.

Revision ID: 0001
Revises:
"""

from alembic import op
from pa_docket.db.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
