"""persist M17 audit exports

Revision ID: j8s3w7y1z596
Revises: i7r2v6x0y485
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "j8s3w7y1z596"
down_revision: str | Sequence[str] | None = "i7r2v6x0y485"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M17 governance requires PostgreSQL")
    op.create_table(
        "xingjing_admin_security_audit_exports",
        sa.Column("export_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("csv_content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("row_count >= 0", name="ck_xj_admin_audit_export_rows"),
    )
    op.create_index(
        "ix_xj_admin_audit_export_scope",
        "xingjing_admin_security_audit_exports",
        ["tenant_id", "workspace_id", "created_at"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M17 governance requires PostgreSQL")
    op.drop_index(
        "ix_xj_admin_audit_export_scope",
        table_name="xingjing_admin_security_audit_exports",
    )
    op.drop_table("xingjing_admin_security_audit_exports")
