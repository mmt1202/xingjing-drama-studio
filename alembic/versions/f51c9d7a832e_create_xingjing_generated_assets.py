"""create controlled M06 generated assets

Revision ID: f51c9d7a832e
Revises: d8b1e7a42f09
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

from server.xingjing_generation_persistence.repository import GeneratedAssetRow


revision: str = "f51c9d7a832e"
down_revision: str | Sequence[str] | None = "d8b1e7a42f09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    GeneratedAssetRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    GeneratedAssetRow.__table__.drop(op.get_bind(), checkfirst=True)
