"""complete M19 open platform and external integration

Revision ID: q7d0a4b8c261
Revises: p6c9a3f7b150
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "q7d0a4b8c261"
down_revision: str | Sequence[str] | None = "p6c9a3f7b150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M19 open platform requires PostgreSQL")
    op.create_table(
        "xingjing_open_api_clients",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("rate_limit", sa.Integer(), nullable=False),
        sa.Column("rate_window_seconds", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_xj_open_client_status"),
        sa.CheckConstraint("version >= 1", name="ck_xj_open_client_version"),
        sa.CheckConstraint("rate_limit > 0", name="ck_xj_open_client_rate_limit"),
        sa.CheckConstraint("rate_window_seconds > 0", name="ck_xj_open_client_rate_window"),
    )
    op.create_table(
        "xingjing_open_api_credentials",
        sa.Column("credential_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("secret_salt", sa.String(64), nullable=False),
        sa.Column("secret_digest", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("rotated_to_id", sa.String(64)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "client_id"],
            [
                "xingjing_open_api_clients.tenant_id",
                "xingjing_open_api_clients.workspace_id",
                "xingjing_open_api_clients.client_id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked','rotated')",
            name="ck_xj_open_credential_status",
        ),
    )
    op.create_index(
        "ix_xj_open_credential_client",
        "xingjing_open_api_credentials",
        ["tenant_id", "workspace_id", "client_id", "created_at"],
    )
    op.create_table(
        "xingjing_open_webhook_endpoints",
        sa.Column("endpoint_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("secret_salt", sa.String(64), nullable=False),
        sa.Column("secret_digest", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "client_id"],
            [
                "xingjing_open_api_clients.tenant_id",
                "xingjing_open_api_clients.workspace_id",
                "xingjing_open_api_clients.client_id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_xj_open_webhook_status"),
        sa.CheckConstraint("version >= 1", name="ck_xj_open_webhook_version"),
    )
    op.create_index(
        "ix_xj_open_webhook_client",
        "xingjing_open_webhook_endpoints",
        ["tenant_id", "workspace_id", "client_id", "updated_at"],
    )
    op.create_table(
        "xingjing_open_api_usage",
        sa.Column("usage_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("credential_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "client_id"],
            [
                "xingjing_open_api_clients.tenant_id",
                "xingjing_open_api_clients.workspace_id",
                "xingjing_open_api_clients.client_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["xingjing_open_api_credentials.credential_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_xj_open_usage_query",
        "xingjing_open_api_usage",
        ["tenant_id", "workspace_id", "client_id", "occurred_at"],
    )
    op.create_table(
        "xingjing_open_platform_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(71), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_xj_open_platform_command"),
    )
    op.create_table(
        "xingjing_open_platform_audit",
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
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_xj_open_platform_audit_query",
        "xingjing_open_platform_audit",
        ["workspace_id", "request_id", "occurred_at"],
    )
    op.execute("""
      CREATE FUNCTION xingjing_open_platform_audit_immutable() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'open platform audit is immutable'; END;
      $$ LANGUAGE plpgsql;
      CREATE TRIGGER trg_xingjing_open_platform_audit_immutable
      BEFORE UPDATE OR DELETE ON xingjing_open_platform_audit
      FOR EACH ROW EXECUTE FUNCTION xingjing_open_platform_audit_immutable();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M19 open platform requires PostgreSQL")
    op.execute("DROP TRIGGER IF EXISTS trg_xingjing_open_platform_audit_immutable ON xingjing_open_platform_audit")
    op.execute("DROP FUNCTION IF EXISTS xingjing_open_platform_audit_immutable()")
    op.drop_index("ix_xj_open_platform_audit_query", table_name="xingjing_open_platform_audit")
    op.drop_table("xingjing_open_platform_audit")
    op.drop_table("xingjing_open_platform_commands")
    op.drop_index("ix_xj_open_usage_query", table_name="xingjing_open_api_usage")
    op.drop_table("xingjing_open_api_usage")
    op.drop_index("ix_xj_open_webhook_client", table_name="xingjing_open_webhook_endpoints")
    op.drop_table("xingjing_open_webhook_endpoints")
    op.drop_index("ix_xj_open_credential_client", table_name="xingjing_open_api_credentials")
    op.drop_table("xingjing_open_api_credentials")
    op.drop_table("xingjing_open_api_clients")
