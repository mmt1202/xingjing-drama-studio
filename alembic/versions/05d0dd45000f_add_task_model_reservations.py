"""Retain task-bound model capacity reservations and releases."""

from alembic import op
from server.xingjing_model_control.runtime import ModelRouteReservationRow

revision = "05d0dd45000f"
down_revision = "f4c9cc34000e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ModelRouteReservationRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Task model reservations and release evidence are retained")
