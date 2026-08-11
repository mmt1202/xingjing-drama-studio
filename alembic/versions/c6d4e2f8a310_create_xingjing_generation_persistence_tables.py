"""create Xingjing M06 generation persistence tables

Revision ID: c6d4e2f8a310
Revises: b5c3d1e7f209
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c6d4e2f8a310"
down_revision: str | Sequence[str] | None = "b5c3d1e7f209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "xingjing_generation_tasks",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_scope", sa.String(length=257), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "project_id", "task_id"),
        sa.UniqueConstraint("workspace_id", "idempotency_scope", name="uq_xj_generation_idempotency"),
    )
    op.create_table(
        "xingjing_generation_provider_evidence",
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("evidence_id"),
        sa.UniqueConstraint("workspace_id", "evidence_id", name="uq_xj_generation_provider_evidence"),
    )
    op.create_table(
        "xingjing_generation_cost_evidence",
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.create_table(
        "xingjing_generation_audit",
        sa.Column("audit_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )


def downgrade() -> None:
    op.drop_table("xingjing_generation_audit")
    op.drop_table("xingjing_generation_cost_evidence")
    op.drop_table("xingjing_generation_provider_evidence")
    op.drop_table("xingjing_generation_tasks")
