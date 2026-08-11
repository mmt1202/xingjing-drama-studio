"""create Xingjing storyboard persistence tables

Revision ID: e6f2a9c3d710
Revises: d3e8f5a1b640
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_storyboard_persistence import Base

revision: str = "e6f2a9c3d710"
down_revision: str | Sequence[str] | None = "d3e8f5a1b640"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), checkfirst=True)
