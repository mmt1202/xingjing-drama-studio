"""scope M19 open platform command idempotency

Revision ID: i7r2v6x0y485
Revises: h6q1u5w9x374
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "i7r2v6x0y485"
down_revision: str | Sequence[str] | None = "h6q1u5w9x374"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M19 open platform requires PostgreSQL")
    op.add_column(
        "xingjing_open_platform_commands",
        sa.Column("tenant_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "xingjing_open_platform_commands",
        sa.Column("workspace_id", sa.String(128), nullable=True),
    )
    op.execute("""
      UPDATE xingjing_open_platform_commands command
      SET tenant_id = audit.tenant_id, workspace_id = audit.workspace_id
      FROM LATERAL (
        SELECT tenant_id, workspace_id
        FROM xingjing_open_platform_audit
        WHERE actor_id = command.actor_id
          AND occurred_at <= command.created_at
        ORDER BY occurred_at DESC, audit_id DESC
        LIMIT 1
      ) audit
    """)
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (
          SELECT 1 FROM xingjing_open_platform_commands
          WHERE tenant_id IS NULL OR workspace_id IS NULL
        ) THEN
          RAISE EXCEPTION 'cannot scope existing M19 commands';
        END IF;
      END $$
    """)
    op.alter_column("xingjing_open_platform_commands", "tenant_id", nullable=False)
    op.alter_column("xingjing_open_platform_commands", "workspace_id", nullable=False)
    op.drop_constraint(
        "uq_xj_open_platform_command",
        "xingjing_open_platform_commands",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_xj_open_platform_command",
        "xingjing_open_platform_commands",
        ["tenant_id", "workspace_id", "actor_id", "idempotency_key"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M19 open platform requires PostgreSQL")
    op.drop_constraint(
        "uq_xj_open_platform_command",
        "xingjing_open_platform_commands",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_xj_open_platform_command",
        "xingjing_open_platform_commands",
        ["actor_id", "idempotency_key"],
    )
    op.drop_column("xingjing_open_platform_commands", "workspace_id")
    op.drop_column("xingjing_open_platform_commands", "tenant_id")
