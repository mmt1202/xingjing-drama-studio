"""add M05 prompt template registry

Revision ID: e5c7a9d2f104
Revises: d4b9f6e2a018
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_storyboard_persistence import PromptTemplateRow, SmartStoryboardRequestRow

revision: str = "e5c7a9d2f104"
down_revision: str | Sequence[str] | None = "d4b9f6e2a018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    PromptTemplateRow.__table__.create(op.get_bind(), checkfirst=True)
    SmartStoryboardRequestRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    SmartStoryboardRequestRow.__table__.drop(op.get_bind(), checkfirst=True)
    PromptTemplateRow.__table__.drop(op.get_bind(), checkfirst=True)
