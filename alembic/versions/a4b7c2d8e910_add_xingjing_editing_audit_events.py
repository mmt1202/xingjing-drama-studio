"""add durable Xingjing editing audit events

Revision ID: a4b7c2d8e910
Revises: e6f2a9c3d710
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_editing_persistence import EditingPersistenceBase

revision: str = "a4b7c2d8e910"
down_revision: str | Sequence[str] | None = "e6f2a9c3d710"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    EditingPersistenceBase.metadata.tables["xingjing_editing_audit_events"].create(
        op.get_bind(), checkfirst=True
    )


def downgrade() -> None:
    EditingPersistenceBase.metadata.tables["xingjing_editing_audit_events"].drop(
        op.get_bind(), checkfirst=True
    )
