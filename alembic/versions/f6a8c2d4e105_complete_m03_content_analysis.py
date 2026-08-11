"""complete M03 content analysis and AI request persistence

Revision ID: f6a8c2d4e105
Revises: e5c7a9d2f104
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a8c2d4e105"
down_revision: str | None = "e5c7a9d2f104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "xingjing_content_script_versions",
        sa.Column("analysis", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("xingjing_content_script_versions", "analysis", server_default=None)
    op.create_table(
        "xingjing_content_ai_requests",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_xj_content_ai_request_status",
        ),
        sa.CheckConstraint("attempts >= 1", name="ck_xj_content_ai_request_attempts"),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "project_id",
            "idempotency_key",
            name="pk_xingjing_content_ai_requests",
        ),
    )
    op.create_index(
        "ix_xj_content_ai_request_scope_updated",
        "xingjing_content_ai_requests",
        ["tenant_id", "workspace_id", "project_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_xj_content_ai_request_scope_updated",
        table_name="xingjing_content_ai_requests",
    )
    op.drop_table("xingjing_content_ai_requests")
    op.drop_column("xingjing_content_script_versions", "analysis")
