"""complete M17 compliance and security governance

Revision ID: p6c9a3f7b150
Revises: n5b8f2e6a049
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "p6c9a3f7b150"
down_revision: str | Sequence[str] | None = "n5b8f2e6a049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M17 governance requires PostgreSQL")
    op.add_column("xingjing_admin_business_audit", sa.Column("tenant_id", sa.String(128)))
    op.add_column("xingjing_admin_business_audit", sa.Column("workspace_id", sa.String(128)))
    op.create_table(
        "xingjing_admin_governance_objects",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), primary_key=True),
        sa.Column("resource", sa.String(64), primary_key=True),
        sa.Column("object_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_xj_admin_governance_object_version"),
    )
    op.create_table(
        "xingjing_admin_sensitive_approvals",
        sa.Column("approval_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("votes", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending','approved','rejected','cancelled')",
                           name="ck_xj_admin_sensitive_approval_status"),
        sa.CheckConstraint("version >= 1", name="ck_xj_admin_sensitive_approval_version"),
    )
    op.create_index(
        "ix_xj_admin_sensitive_approval_queue",
        "xingjing_admin_sensitive_approvals",
        ["tenant_id", "workspace_id", "status", "updated_at"],
    )
    op.create_table(
        "xingjing_admin_governance_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(71), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_xj_admin_governance_command"),
    )
    op.create_table(
        "xingjing_admin_security_audit",
        sa.Column("audit_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("before_payload", postgresql.JSONB(), nullable=False),
        sa.Column("after_payload", postgresql.JSONB(), nullable=False),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("device", sa.String(256)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_xj_admin_security_audit_query",
        "xingjing_admin_security_audit",
        ["workspace_id", "request_id", "occurred_at"],
    )
    op.execute("""
      CREATE FUNCTION xingjing_admin_security_audit_immutable() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'admin security audit is immutable'; END;
      $$ LANGUAGE plpgsql;
      CREATE TRIGGER trg_xingjing_admin_security_audit_immutable
      BEFORE UPDATE OR DELETE ON xingjing_admin_security_audit
      FOR EACH ROW EXECUTE FUNCTION xingjing_admin_security_audit_immutable();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M17 governance requires PostgreSQL")
    op.execute("DROP TRIGGER IF EXISTS trg_xingjing_admin_security_audit_immutable ON xingjing_admin_security_audit")
    op.execute("DROP FUNCTION IF EXISTS xingjing_admin_security_audit_immutable()")
    op.drop_index("ix_xj_admin_security_audit_query", table_name="xingjing_admin_security_audit")
    op.drop_table("xingjing_admin_security_audit")
    op.drop_table("xingjing_admin_governance_commands")
    op.drop_index("ix_xj_admin_sensitive_approval_queue", table_name="xingjing_admin_sensitive_approvals")
    op.drop_table("xingjing_admin_sensitive_approvals")
    op.drop_table("xingjing_admin_governance_objects")
    op.drop_column("xingjing_admin_business_audit", "workspace_id")
    op.drop_column("xingjing_admin_business_audit", "tenant_id")
