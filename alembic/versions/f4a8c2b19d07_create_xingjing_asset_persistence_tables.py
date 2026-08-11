"""create Xingjing M04 asset persistence tables

Revision ID: f4a8c2b19d07
Revises: c6d4e2f8a310
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

from server.xingjing_assets_persistence.repository import Base


revision: str = "f4a8c2b19d07"
down_revision: str | Sequence[str] | None = "c6d4e2f8a310"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the M04 persistence metadata in the established migration chain."""
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Remove the M04 tables in reverse dependency order."""
    Base.metadata.drop_all(op.get_bind(), checkfirst=True)
