"""add M08 render deadline sweep index

Revision ID: e3m8r2t6u041
Revises: cd5026363f1a
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3m8r2t6u041"
down_revision: str | Sequence[str] | None = "cd5026363f1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "xingjing_editing_render_tasks",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing tasks predate the indexed deadline column. Using updated_at is
    # deliberately conservative and portable: the sweeper immediately
    # reconciles them instead of retaining stale billing holds indefinitely.
    op.execute(
        "UPDATE xingjing_editing_render_tasks SET deadline_at = updated_at WHERE deadline_at IS NULL"
    )
    op.alter_column("xingjing_editing_render_tasks", "deadline_at", nullable=False)
    op.create_index(
        "ix_xj_editing_render_deadline",
        "xingjing_editing_render_tasks",
        ["status", "deadline_at", "task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_xj_editing_render_deadline", table_name="xingjing_editing_render_tasks")
    op.drop_column("xingjing_editing_render_tasks", "deadline_at")
