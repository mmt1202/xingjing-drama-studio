"""add_audio_task_cancelling_state

Revision ID: e7b4c1d9a605
Revises: d1e7f3a8c204
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "e7b4c1d9a605"
down_revision: str | Sequence[str] | None = "d1e7f3a8c204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M07 audio persistence requires PostgreSQL; SQLite is not a production substitute")
    op.drop_constraint("ck_xj_audio_task_status", "xingjing_audio_generation_tasks", type_="check")
    op.create_check_constraint(
        "ck_xj_audio_task_status",
        "xingjing_audio_generation_tasks",
        "status IN ('pending', 'queued', 'running', 'cancelling', 'retrying', 'succeeded', 'failed', 'cancelled', 'timed_out')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M07 audio persistence requires PostgreSQL; SQLite is not a production substitute")
    op.execute("UPDATE xingjing_audio_generation_tasks SET status = 'cancelled' WHERE status = 'cancelling'")
    op.drop_constraint("ck_xj_audio_task_status", "xingjing_audio_generation_tasks", type_="check")
    op.create_check_constraint(
        "ck_xj_audio_task_status",
        "xingjing_audio_generation_tasks",
        "status IN ('pending', 'queued', 'running', 'retrying', 'succeeded', 'failed', 'cancelled', 'timed_out')",
    )
