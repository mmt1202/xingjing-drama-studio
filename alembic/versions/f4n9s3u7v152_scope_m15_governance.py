"""scope M15 governance and idempotency to tenant workspace

Revision ID: f4n9s3u7v152
Revises: e3m8r2t6u041
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4n9s3u7v152"
down_revision: str | Sequence[str] | None = "e3m8r2t6u041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M15 governance scoping requires PostgreSQL")
    op.add_column("xingjing_admin_business_governance", sa.Column("tenant_id", sa.String(128)))
    op.add_column("xingjing_admin_business_governance", sa.Column("workspace_id", sa.String(128)))
    op.add_column("xingjing_admin_business_governance", sa.Column("settings", sa.JSON(), nullable=False,
                                                                    server_default=sa.text("'{}'")))
    op.execute("""
      UPDATE xingjing_admin_business_governance AS governance
         SET tenant_id=(SELECT audit.tenant_id FROM xingjing_admin_business_audit AS audit
                         WHERE audit.resource=governance.resource AND audit.object_id=governance.object_id
                           AND audit.tenant_id IS NOT NULL AND audit.workspace_id IS NOT NULL
                         ORDER BY audit.occurred_at DESC, audit.id DESC LIMIT 1),
             workspace_id=(SELECT audit.workspace_id FROM xingjing_admin_business_audit AS audit
                            WHERE audit.resource=governance.resource AND audit.object_id=governance.object_id
                              AND audit.tenant_id IS NOT NULL AND audit.workspace_id IS NOT NULL
                            ORDER BY audit.occurred_at DESC, audit.id DESC LIMIT 1);
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM xingjing_admin_business_governance
                    WHERE tenant_id IS NULL OR workspace_id IS NULL) THEN
          RAISE EXCEPTION 'M15 governance rows cannot be scoped from audit evidence';
        END IF;
      END $$;
    """)
    op.alter_column("xingjing_admin_business_governance", "tenant_id", nullable=False)
    op.alter_column("xingjing_admin_business_governance", "workspace_id", nullable=False)
    op.drop_constraint("xingjing_admin_business_governance_pkey", "xingjing_admin_business_governance", type_="primary")
    op.create_primary_key("pk_xj_admin_business_governance_scope", "xingjing_admin_business_governance",
                          ["tenant_id", "workspace_id", "resource", "object_id"])

    op.add_column("xingjing_admin_business_commands", sa.Column("tenant_id", sa.String(128)))
    op.add_column("xingjing_admin_business_commands", sa.Column("workspace_id", sa.String(128)))
    op.execute("""
      UPDATE xingjing_admin_business_commands AS command
         SET tenant_id=(SELECT audit.tenant_id FROM xingjing_admin_business_audit AS audit
                         WHERE audit.actor_id=command.actor_id AND audit.occurred_at >= command.created_at
                           AND audit.tenant_id IS NOT NULL AND audit.workspace_id IS NOT NULL
                         ORDER BY audit.occurred_at, audit.id LIMIT 1),
             workspace_id=(SELECT audit.workspace_id FROM xingjing_admin_business_audit AS audit
                            WHERE audit.actor_id=command.actor_id AND audit.occurred_at >= command.created_at
                              AND audit.tenant_id IS NOT NULL AND audit.workspace_id IS NOT NULL
                            ORDER BY audit.occurred_at, audit.id LIMIT 1);
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM xingjing_admin_business_commands
                    WHERE tenant_id IS NULL OR workspace_id IS NULL) THEN
          RAISE EXCEPTION 'M15 command rows cannot be scoped from audit evidence';
        END IF;
      END $$;
    """)
    op.alter_column("xingjing_admin_business_commands", "tenant_id", nullable=False)
    op.alter_column("xingjing_admin_business_commands", "workspace_id", nullable=False)
    op.drop_constraint("uq_xj_admin_business_command", "xingjing_admin_business_commands", type_="unique")
    op.create_unique_constraint("uq_xj_admin_business_command_scope", "xingjing_admin_business_commands",
                                ["tenant_id", "workspace_id", "actor_id", "idempotency_key"])


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M15 governance scoping requires PostgreSQL")
    op.drop_constraint("uq_xj_admin_business_command_scope", "xingjing_admin_business_commands", type_="unique")
    op.create_unique_constraint("uq_xj_admin_business_command", "xingjing_admin_business_commands",
                                ["actor_id", "idempotency_key"])
    op.drop_column("xingjing_admin_business_commands", "workspace_id")
    op.drop_column("xingjing_admin_business_commands", "tenant_id")
    op.drop_constraint("pk_xj_admin_business_governance_scope", "xingjing_admin_business_governance", type_="primary")
    op.create_primary_key("xingjing_admin_business_governance_pkey", "xingjing_admin_business_governance",
                          ["resource", "object_id"])
    op.drop_column("xingjing_admin_business_governance", "workspace_id")
    op.drop_column("xingjing_admin_business_governance", "tenant_id")
    op.drop_column("xingjing_admin_business_governance", "settings")
