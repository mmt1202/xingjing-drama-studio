"""version M16 invoice approval records

Revision ID: g5p0t4v8w263
Revises: f4n9s3u7v152
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g5p0t4v8w263"
down_revision: str | Sequence[str] | None = "f4n9s3u7v152"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M16 invoice versioning requires PostgreSQL")
    op.execute("ALTER TABLE xingjing_team_invoice_requests ADD COLUMN IF NOT EXISTS approved_by VARCHAR(128)")
    op.execute("ALTER TABLE xingjing_team_invoice_requests ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")
    op.execute("""
      DO $$ BEGIN
        ALTER TABLE xingjing_team_invoice_requests
          ADD CONSTRAINT ck_xj_team_invoice_version CHECK (version >= 1);
      EXCEPTION WHEN duplicate_object THEN NULL;
      END $$;
    """)
    op.add_column("xingjing_admin_finance_operations", sa.Column("tenant_id", sa.String(128)))
    op.add_column("xingjing_admin_finance_commands", sa.Column("tenant_id", sa.String(128)))
    op.add_column("xingjing_admin_finance_commands", sa.Column("workspace_id", sa.String(128)))
    op.add_column("xingjing_admin_finance_audit", sa.Column("tenant_id", sa.String(128)))
    op.execute("""
      UPDATE xingjing_admin_finance_operations SET tenant_id=workspace_id WHERE tenant_id IS NULL;
      UPDATE xingjing_admin_finance_audit SET tenant_id=workspace_id WHERE tenant_id IS NULL;
      UPDATE xingjing_admin_finance_commands AS command
         SET tenant_id=(SELECT audit.tenant_id FROM xingjing_admin_finance_audit AS audit
                         WHERE audit.actor_id=command.actor_id AND audit.occurred_at >= command.created_at
                         ORDER BY audit.occurred_at,audit.audit_id LIMIT 1),
             workspace_id=(SELECT audit.workspace_id FROM xingjing_admin_finance_audit AS audit
                            WHERE audit.actor_id=command.actor_id AND audit.occurred_at >= command.created_at
                            ORDER BY audit.occurred_at,audit.audit_id LIMIT 1);
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM xingjing_admin_finance_commands
                    WHERE tenant_id IS NULL OR workspace_id IS NULL) THEN
          RAISE EXCEPTION 'M16 command rows cannot be scoped from audit evidence';
        END IF;
      END $$;
    """)
    op.alter_column("xingjing_admin_finance_operations", "tenant_id", nullable=False)
    op.alter_column("xingjing_admin_finance_commands", "tenant_id", nullable=False)
    op.alter_column("xingjing_admin_finance_commands", "workspace_id", nullable=False)
    op.alter_column("xingjing_admin_finance_audit", "tenant_id", nullable=False)
    op.drop_constraint("uq_xj_admin_finance_command", "xingjing_admin_finance_commands", type_="unique")
    op.create_unique_constraint("uq_xj_admin_finance_command_scope", "xingjing_admin_finance_commands",
                                ["tenant_id", "workspace_id", "actor_id", "idempotency_key"])
    op.create_index("ix_xj_admin_finance_operation_scope", "xingjing_admin_finance_operations",
                    ["tenant_id", "workspace_id", "operation_type", "updated_at"])
    op.create_index("ix_xj_admin_finance_audit_scope", "xingjing_admin_finance_audit",
                    ["tenant_id", "workspace_id", "request_id", "occurred_at"])
    op.create_table(
        "xingjing_admin_finance_exports",
        sa.Column("export_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("export_kind", sa.String(32), nullable=False),
        sa.Column("content_csv", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(71), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("row_count >= 0", name="ck_xj_admin_finance_export_rows"),
    )
    op.create_index("ix_xj_admin_finance_export_scope", "xingjing_admin_finance_exports",
                    ["tenant_id", "workspace_id", "created_at"])


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M16 invoice versioning requires PostgreSQL")
    op.drop_index("ix_xj_admin_finance_export_scope", table_name="xingjing_admin_finance_exports")
    op.drop_table("xingjing_admin_finance_exports")
    op.drop_index("ix_xj_admin_finance_audit_scope", table_name="xingjing_admin_finance_audit")
    op.drop_index("ix_xj_admin_finance_operation_scope", table_name="xingjing_admin_finance_operations")
    op.drop_constraint("uq_xj_admin_finance_command_scope", "xingjing_admin_finance_commands", type_="unique")
    op.create_unique_constraint("uq_xj_admin_finance_command", "xingjing_admin_finance_commands",
                                ["actor_id", "idempotency_key"])
    op.drop_column("xingjing_admin_finance_audit", "tenant_id")
    op.drop_column("xingjing_admin_finance_commands", "workspace_id")
    op.drop_column("xingjing_admin_finance_commands", "tenant_id")
    op.drop_column("xingjing_admin_finance_operations", "tenant_id")
    op.drop_constraint("ck_xj_team_invoice_version", "xingjing_team_invoice_requests", type_="check")
    op.drop_column("xingjing_team_invoice_requests", "version")
    op.drop_column("xingjing_team_invoice_requests", "approved_by")
