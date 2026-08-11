"""add M07 audio task billing holds and journals

Revision ID: g7b9d3e1f206
Revises: f6a8c2d4e105
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_audio_persistence.models import audio_billing_holds, audio_billing_journals

revision: str = "g7b9d3e1f206"
down_revision: str | None = "f6a8c2d4e105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M07 billing persistence requires PostgreSQL")
    audio_billing_holds.create(bind, checkfirst=True)
    audio_billing_journals.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    audio_billing_journals.drop(bind, checkfirst=True)
    audio_billing_holds.drop(bind, checkfirst=True)
