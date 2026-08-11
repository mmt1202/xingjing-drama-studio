"""create durable M06 legacy queue dispatch bridge

Revision ID: d8b1e7a42f09
Revises: f4a8c2b19d07
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

from server.xingjing_generation_persistence.repository import LegacyQueueDispatchRow


revision: str = "d8b1e7a42f09"
down_revision: str | Sequence[str] | None = "f4a8c2b19d07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    LegacyQueueDispatchRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    LegacyQueueDispatchRow.__table__.drop(op.get_bind(), checkfirst=True)
