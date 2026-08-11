"""repair M15 admin business runtime schema

Revision ID: x4k8p2m6q140
Revises: w3j7k0l4m827
"""

from collections.abc import Sequence

from alembic import op

revision: str = "x4k8p2m6q140"
down_revision: str | Sequence[str] | None = "w3j7k0l4m827"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M15 admin business requires PostgreSQL")
    # M17's historical migration already added the scope columns. This repair
    # only adds the lookup index after the merged migration head.
    op.create_index(
        "ix_xj_admin_business_audit_scope",
        "xingjing_admin_business_audit",
        ["tenant_id", "workspace_id", "occurred_at"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M15 admin business requires PostgreSQL")
    op.drop_index(
        "ix_xj_admin_business_audit_scope",
        table_name="xingjing_admin_business_audit",
    )
