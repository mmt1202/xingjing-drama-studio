"""complete S04 task event and notification runtime

Revision ID: a7n1s5t9u473
Revises: z6m0r4n8s362
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from server.xingjing_platform_persistence import Base

revision: str = "a7n1s5t9u473"
down_revision: str | Sequence[str] | None = "z6m0r4n8s362"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind, checkfirst=True)
    existing = {column["name"] for column in sa.inspect(bind).get_columns("xingjing_tasks")}
    additions = {
        "request_fingerprint": sa.Column("request_fingerprint", sa.String(64), nullable=False, server_default=""),
        "project_id": sa.Column("project_id", sa.String(64)),
        "batch_id": sa.Column("batch_id", sa.String(64)),
        "created_by": sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        "progress": sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        "version": sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        "result": sa.Column("result", sa.JSON()),
        "provider_job_id": sa.Column("provider_job_id", sa.String(255)),
        "deadline_at": sa.Column("deadline_at", sa.DateTime(timezone=True)),
        "cancel_requested_at": sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        "completed_at": sa.Column("completed_at", sa.DateTime(timezone=True)),
    }
    for name, column in additions.items():
        if name not in existing:
            op.add_column("xingjing_tasks", column)


def downgrade() -> None:
    for table in (
        "xingjing_notification_deliveries",
        "xingjing_notification_preferences",
        "xingjing_notifications",
        "xingjing_task_dead_letters",
        "xingjing_task_events",
    ):
        op.drop_table(table)
    for name in (
        "completed_at",
        "cancel_requested_at",
        "deadline_at",
        "provider_job_id",
        "result",
        "version",
        "progress",
        "created_by",
        "batch_id",
        "project_id",
        "request_fingerprint",
    ):
        op.drop_column("xingjing_tasks", name)
