"""Retain director candidates, confirmations and Smart storyboard input identity.

Revision ID: d2a7aa12000c
Revises: c1f6f9f1000b
"""

import sqlalchemy as sa

from alembic import op
from server.xingjing_content_persistence.repository import DirectorConfirmationRow, DirectorVersionRow

revision = "d2a7aa12000c"
down_revision = "c1f6f9f1000b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    DirectorVersionRow.__table__.create(bind, checkfirst=True)
    DirectorConfirmationRow.__table__.create(bind, checkfirst=True)
    if "input_payload" not in {
        column["name"] for column in sa.inspect(bind).get_columns("xingjing_smart_storyboard_requests")
    }:
        op.add_column("xingjing_smart_storyboard_requests", sa.Column("input_payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Director confirmation and generation provenance are retained")
