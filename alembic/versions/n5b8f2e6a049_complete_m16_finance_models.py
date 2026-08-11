"""complete M16 platform finance and model operations

Revision ID: n5b8f2e6a049
Revises: m4a7e1d5f938
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "n5b8f2e6a049"
down_revision: str | Sequence[str] | None = "m4a7e1d5f938"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M16 finance and models requires PostgreSQL")
    op.create_table(
        "xingjing_admin_model_definitions",
        sa.Column("provider_id", sa.String(128), primary_key=True),
        sa.Column("model_id", sa.String(128), primary_key=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("health", sa.String(32), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("quota_per_minute", sa.Integer(), nullable=False),
        sa.Column("routing_weight", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("unit_price_minor >= 0", name="ck_xj_admin_model_price"),
        sa.CheckConstraint("quota_per_minute >= 0", name="ck_xj_admin_model_quota"),
        sa.CheckConstraint("routing_weight BETWEEN 0 AND 100", name="ck_xj_admin_model_weight"),
        sa.CheckConstraint("version >= 1", name="ck_xj_admin_model_version"),
    )
    op.create_table(
        "xingjing_admin_finance_operations",
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("operation_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor >= 0", name="ck_xj_admin_finance_amount"),
        sa.CheckConstraint("version >= 1", name="ck_xj_admin_finance_operation_version"),
    )
    op.create_index(
        "ix_xj_admin_finance_operation_workspace",
        "xingjing_admin_finance_operations",
        ["workspace_id", "operation_type", "updated_at"],
    )
    op.create_table(
        "xingjing_admin_finance_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(71), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_xj_admin_finance_command"),
    )
    op.create_table(
        "xingjing_admin_finance_audit",
        sa.Column("audit_id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("before_payload", postgresql.JSONB(), nullable=False),
        sa.Column("after_payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_xj_admin_finance_audit_request",
        "xingjing_admin_finance_audit",
        ["workspace_id", "request_id", "occurred_at"],
    )
    op.execute("""
      CREATE FUNCTION xingjing_admin_finance_audit_immutable() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'admin finance audit is immutable'; END;
      $$ LANGUAGE plpgsql;
      CREATE TRIGGER trg_xingjing_admin_finance_audit_immutable
      BEFORE UPDATE OR DELETE ON xingjing_admin_finance_audit
      FOR EACH ROW EXECUTE FUNCTION xingjing_admin_finance_audit_immutable();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M16 finance and models requires PostgreSQL")
    op.execute("DROP TRIGGER IF EXISTS trg_xingjing_admin_finance_audit_immutable ON xingjing_admin_finance_audit")
    op.execute("DROP FUNCTION IF EXISTS xingjing_admin_finance_audit_immutable()")
    op.drop_index("ix_xj_admin_finance_audit_request", table_name="xingjing_admin_finance_audit")
    op.drop_table("xingjing_admin_finance_audit")
    op.drop_table("xingjing_admin_finance_commands")
    op.drop_index("ix_xj_admin_finance_operation_workspace", table_name="xingjing_admin_finance_operations")
    op.drop_table("xingjing_admin_finance_operations")
    op.drop_table("xingjing_admin_model_definitions")
