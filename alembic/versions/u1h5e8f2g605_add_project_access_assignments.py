"""add project-scoped member and permission assignments

Revision ID: u1h5e8f2g605
Revises: t0g4d7e1f594
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "u1h5e8f2g605"
down_revision: str | Sequence[str] | None = "t0g4d7e1f594"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "xingjing_project_members",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("member_id", sa.String(length=128), nullable=False),
        sa.Column("production_role", sa.String(length=128), nullable=False),
        sa.Column("data_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_xj_project_member_version"),
        sa.ForeignKeyConstraint(["project_id"], ["xingjing_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "member_id"],
            [
                "xingjing_team_members.tenant_id",
                "xingjing_team_members.workspace_id",
                "xingjing_team_members.member_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "project_id", "member_id"),
    )
    op.create_index(
        "ix_xj_project_member_scope",
        "xingjing_project_members",
        ["tenant_id", "workspace_id", "project_id", "active", "member_id"],
    )
    op.create_table(
        "xingjing_project_access_receipts",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "project_id",
            "action",
            "idempotency_key",
        ),
    )
    op.create_table(
        "xingjing_project_access_audit_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=128), nullable=False),
        sa.Column("before_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_xj_project_access_audit_lookup",
        "xingjing_project_access_audit_events",
        ["tenant_id", "workspace_id", "project_id", "occurred_at", "event_id"],
    )
    op.create_index(
        "ix_xj_project_access_audit_request",
        "xingjing_project_access_audit_events",
        ["request_id"],
    )
    op.execute(
        """
        CREATE FUNCTION xingjing_project_access_audit_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'xingjing_project_access_audit_events are immutable';
        END;
        $$;

        CREATE TRIGGER trg_xingjing_project_access_audit_immutable
        BEFORE UPDATE OR DELETE ON xingjing_project_access_audit_events
        FOR EACH ROW EXECUTE FUNCTION xingjing_project_access_audit_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_xingjing_project_access_audit_immutable "
        "ON xingjing_project_access_audit_events"
    )
    op.execute("DROP FUNCTION IF EXISTS xingjing_project_access_audit_immutable()")
    op.drop_index("ix_xj_project_access_audit_request", table_name="xingjing_project_access_audit_events")
    op.drop_index("ix_xj_project_access_audit_lookup", table_name="xingjing_project_access_audit_events")
    op.drop_table("xingjing_project_access_audit_events")
    op.drop_table("xingjing_project_access_receipts")
    op.drop_index("ix_xj_project_member_scope", table_name="xingjing_project_members")
    op.drop_table("xingjing_project_members")
