"""complete S05 model control plane

Revision ID: b8p2t6u0v584
Revises: a7n1s5t9u473
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_model_control import ModelControlBase

revision: str = "b8p2t6u0v584"
down_revision: str | Sequence[str] | None = "a7n1s5t9u473"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ModelControlBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    ModelControlBase.metadata.drop_all(op.get_bind(), checkfirst=True)
