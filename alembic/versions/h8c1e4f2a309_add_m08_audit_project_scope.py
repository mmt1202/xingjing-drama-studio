"""add M08 audit project scope

Revision ID: h8c1e4f2a309
Revises: g7b9d3e1f206
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from server.xingjing_editing_persistence import EditingPersistenceBase

revision: str = "h8c1e4f2a309"
down_revision: str | Sequence[str] | None = "g7b9d3e1f206"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table = "xingjing_editing_audit_events"
    if "project_id" not in {column["name"] for column in inspector.get_columns(table)}:
        op.add_column(table, sa.Column("project_id", sa.String(length=128), nullable=True))
    if "ix_xj_editing_audit_project_time" not in {index["name"] for index in inspector.get_indexes(table)}:
        op.create_index(
            "ix_xj_editing_audit_project_time",
            table,
            ["tenant_id", "workspace_id", "project_id", "occurred_at", "event_id"],
        )
    EditingPersistenceBase.metadata.tables["xingjing_editing_render_billing_holds"].create(bind, checkfirst=True)
    EditingPersistenceBase.metadata.tables["xingjing_editing_render_billing_journals"].create(bind, checkfirst=True)
    EditingPersistenceBase.metadata.tables["xingjing_editing_final_video_selections"].create(bind, checkfirst=True)
    EditingPersistenceBase.metadata.tables["xingjing_editing_final_video_selection_receipts"].create(
        bind, checkfirst=True
    )


def downgrade() -> None:
    bind = op.get_bind()
    EditingPersistenceBase.metadata.tables["xingjing_editing_final_video_selection_receipts"].drop(
        bind, checkfirst=True
    )
    EditingPersistenceBase.metadata.tables["xingjing_editing_final_video_selections"].drop(bind, checkfirst=True)
    EditingPersistenceBase.metadata.tables["xingjing_editing_render_billing_journals"].drop(bind, checkfirst=True)
    EditingPersistenceBase.metadata.tables["xingjing_editing_render_billing_holds"].drop(bind, checkfirst=True)
    op.drop_index(
        "ix_xj_editing_audit_project_time",
        table_name="xingjing_editing_audit_events",
    )
    op.drop_column("xingjing_editing_audit_events", "project_id")
