"""Persist project version checkpoints, reports and command receipts.

Revision ID: a9d4d7deeee9
Revises: 98c3c6cfddd8
"""

from alembic import op
from server.xingjing_versions.models import VersionBase

revision = "a9d4d7deeee9"
down_revision = "98c3c6cfddd8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    VersionBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Version checkpoints, reports and audit evidence are retained")
