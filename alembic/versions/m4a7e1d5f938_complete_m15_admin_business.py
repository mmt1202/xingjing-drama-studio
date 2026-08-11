"""complete M15 admin business governance

Revision ID: m4a7e1d5f938
Revises: l3f6d0c4e827
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m4a7e1d5f938"
down_revision: str | Sequence[str] | None = "l3f6d0c4e827"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M15 admin business requires PostgreSQL")
    op.create_table(
        "xingjing_admin_business_governance",
        sa.Column("resource", sa.String(32), primary_key=True),
        sa.Column("object_id", sa.String(128), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_xj_admin_governance_version"),
    )
    op.create_table(
        "xingjing_admin_business_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(512), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_xj_admin_business_command"),
    )
    op.create_table(
        "xingjing_admin_business_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("resource", sa.String(32), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("before_status", sa.String(32), nullable=False),
        sa.Column("after_status", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_xj_admin_business_audit_request", "xingjing_admin_business_audit",
                    ["request_id", "occurred_at"])
    op.execute("""
      CREATE FUNCTION xingjing_admin_business_audit_immutable() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'admin business audit is immutable'; END;
      $$ LANGUAGE plpgsql;
      CREATE TRIGGER trg_xingjing_admin_business_audit_immutable
      BEFORE UPDATE OR DELETE ON xingjing_admin_business_audit
      FOR EACH ROW EXECUTE FUNCTION xingjing_admin_business_audit_immutable();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M15 admin business requires PostgreSQL")
    op.execute("DROP TRIGGER IF EXISTS trg_xingjing_admin_business_audit_immutable ON xingjing_admin_business_audit")
    op.execute("DROP FUNCTION IF EXISTS xingjing_admin_business_audit_immutable()")
    op.drop_index("ix_xj_admin_business_audit_request", table_name="xingjing_admin_business_audit")
    op.drop_table("xingjing_admin_business_audit")
    op.drop_table("xingjing_admin_business_commands")
    op.drop_table("xingjing_admin_business_governance")
