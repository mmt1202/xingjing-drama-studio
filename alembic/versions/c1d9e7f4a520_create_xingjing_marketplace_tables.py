"""create Xingjing marketplace and Fork persistence tables

Revision ID: c1d9e7f4a520
Revises: bd25b66f82e8
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_marketplace_persistence import Base
from server.xingjing_platform_persistence import Base as PlatformBase

# revision identifiers, used by Alembic.
revision: str = "c1d9e7f4a520"
down_revision: str | Sequence[str] | None = "bd25b66f82e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the shared S03/S04 tables before modules reference them."""
    PlatformBase.metadata.create_all(op.get_bind(), checkfirst=True)
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Remove the M13 tables in foreign-key-safe dependency order."""
    Base.metadata.drop_all(op.get_bind(), checkfirst=True)
    PlatformBase.metadata.drop_all(op.get_bind(), checkfirst=True)
