"""Persist project cover plans and frozen release materials.

Revision ID: 98c3c6cfddd8
Revises: 87b2b5beccc7
"""

from alembic import op
from server.xingjing_cover.models import CoverBase

revision = "98c3c6cfddd8"
down_revision = "87b2b5beccc7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    CoverBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Cover drafts, audit evidence and release selections are retained")
