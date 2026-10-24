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
    inspector = sa.inspect(op.get_bind())
    table = "xingjing_editing_render_tasks"
    if "deadline_at" not in {column["name"] for column in inspector.get_columns(table)}:
        op.add_column(table, sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
    # Tasks without a deadline use updated_at so the sweeper can reconcile billing holds.
    op.execute("UPDATE xingjing_editing_render_tasks SET deadline_at = updated_at WHERE deadline_at IS NULL")
    op.alter_column("xingjing_editing_render_tasks", "deadline_at", nullable=False)
    if "ix_xj_editing_render_deadline" not in {index["name"] for index in inspector.get_indexes(table)}:
        op.create_index("ix_xj_editing_render_deadline", table, ["status", "deadline_at", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_xj_editing_render_deadline", table_name="xingjing_editing_render_tasks")
    op.drop_column("xingjing_editing_render_tasks", "deadline_at")
