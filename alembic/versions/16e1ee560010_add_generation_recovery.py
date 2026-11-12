"""Retain verified generation receipts and administrator recovery decisions."""

from alembic import op
from server.xingjing_generation_recovery.models import RecoveryBase

revision = "16e1ee560010"
down_revision = "05d0dd45000f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    RecoveryBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Generation recovery receipts, decisions and audit evidence are retained")
