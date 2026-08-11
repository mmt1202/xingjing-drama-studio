"""add M07 task deadline

Revision ID: f2t6x0y4z928
Revises: e1s5w9x3y817
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2t6x0y4z928"
down_revision: str | Sequence[str] | None = "e1s5w9x3y817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE xingjing_audio_generation_tasks ADD COLUMN IF NOT EXISTS timeout_at TIMESTAMPTZ")
    op.execute(
        "UPDATE xingjing_audio_generation_tasks SET timeout_at = created_at + INTERVAL '6 hours' "
        "WHERE timeout_at IS NULL"
    )
    op.execute("ALTER TABLE xingjing_audio_generation_tasks ALTER COLUMN timeout_at SET NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_xj_audio_task_timeout "
        "ON xingjing_audio_generation_tasks (status, timeout_at, id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_xj_audio_task_timeout")
    op.execute("ALTER TABLE xingjing_audio_generation_tasks DROP COLUMN IF EXISTS timeout_at")
