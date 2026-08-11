"""add M09 review action versioning and audit trail

Revision ID: b2f7d4c9e806
Revises: a8d5e2c6b913
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from server.xingjing_compliance_persistence.repository import CompliancePersistenceBase

revision: str = "b2f7d4c9e806"
down_revision: str | Sequence[str] | None = "a8d5e2c6b913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "xingjing_compliance_authorizations",
        sa.Column("evidence_object_key", sa.String(1024), nullable=True),
    )
    op.add_column(
        "xingjing_compliance_reviews",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("xingjing_compliance_reviews", "version", server_default=None)
    CompliancePersistenceBase.metadata.tables["xingjing_compliance_audit_events"].create(
        op.get_bind(), checkfirst=True
    )
    op.add_column("xingjing_compliance_export_deliveries", sa.Column("status", sa.String(32), nullable=True))
    op.add_column("xingjing_compliance_export_deliveries", sa.Column("format", sa.String(32), nullable=True))
    op.add_column("xingjing_compliance_export_deliveries", sa.Column("object_key", sa.String(1024), nullable=True))
    op.add_column("xingjing_compliance_export_deliveries", sa.Column("content_sha256", sa.String(71), nullable=True))
    op.add_column("xingjing_compliance_export_deliveries", sa.Column("size_bytes", sa.Integer(), nullable=True))
    op.add_column("xingjing_compliance_export_deliveries", sa.Column("download_path", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("xingjing_compliance_export_deliveries", "download_path")
    op.drop_column("xingjing_compliance_export_deliveries", "size_bytes")
    op.drop_column("xingjing_compliance_export_deliveries", "content_sha256")
    op.drop_column("xingjing_compliance_export_deliveries", "object_key")
    op.drop_column("xingjing_compliance_export_deliveries", "format")
    op.drop_column("xingjing_compliance_export_deliveries", "status")
    CompliancePersistenceBase.metadata.tables["xingjing_compliance_audit_events"].drop(
        op.get_bind(), checkfirst=True
    )
    op.drop_column("xingjing_compliance_reviews", "version")
    op.drop_column("xingjing_compliance_authorizations", "evidence_object_key")
