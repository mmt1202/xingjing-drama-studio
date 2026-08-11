"""scope shared admin commands for M17 and M18

Revision ID: h6q1u5w9x374
Revises: g5p0t4v8w263
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h6q1u5w9x374"
down_revision: str | Sequence[str] | None = "g5p0t4v8w263"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M18 admin command scoping requires PostgreSQL")
    op.add_column("xingjing_admin_governance_commands", sa.Column("tenant_id", sa.String(128)))
    op.add_column("xingjing_admin_governance_commands", sa.Column("workspace_id", sa.String(128)))
    op.execute("""
      UPDATE xingjing_admin_governance_commands AS command
         SET tenant_id=(SELECT audit.tenant_id FROM xingjing_admin_security_audit AS audit
                         WHERE audit.actor_id=command.actor_id AND audit.occurred_at>=command.created_at
                         ORDER BY audit.occurred_at,audit.audit_id LIMIT 1),
             workspace_id=(SELECT audit.workspace_id FROM xingjing_admin_security_audit AS audit
                            WHERE audit.actor_id=command.actor_id AND audit.occurred_at>=command.created_at
                            ORDER BY audit.occurred_at,audit.audit_id LIMIT 1);
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM xingjing_admin_governance_commands
                    WHERE tenant_id IS NULL OR workspace_id IS NULL) THEN
          RAISE EXCEPTION 'admin command rows cannot be scoped from audit evidence';
        END IF;
      END $$;
    """)
    op.alter_column("xingjing_admin_governance_commands", "tenant_id", nullable=False)
    op.alter_column("xingjing_admin_governance_commands", "workspace_id", nullable=False)
    op.drop_constraint("uq_xj_admin_governance_command", "xingjing_admin_governance_commands", type_="unique")
    op.create_unique_constraint(
        "uq_xj_admin_governance_command_scope", "xingjing_admin_governance_commands",
        ["tenant_id", "workspace_id", "actor_id", "idempotency_key"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M18 admin command scoping requires PostgreSQL")
    op.drop_constraint("uq_xj_admin_governance_command_scope", "xingjing_admin_governance_commands", type_="unique")
    op.create_unique_constraint("uq_xj_admin_governance_command", "xingjing_admin_governance_commands",
                                ["actor_id", "idempotency_key"])
    op.drop_column("xingjing_admin_governance_commands", "workspace_id")
    op.drop_column("xingjing_admin_governance_commands", "tenant_id")
