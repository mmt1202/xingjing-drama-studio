"""create Xingjing editing timeline and render persistence tables

Revision ID: d3e8f5a1b640
Revises: c1d9e7f4a520
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_editing_persistence import EditingPersistenceBase

revision: str = "d3e8f5a1b640"
down_revision: str | Sequence[str] | None = "c1d9e7f4a520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in _ORIGINAL_EDITING_TABLES:
        EditingPersistenceBase.metadata.tables[table_name].create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(_ORIGINAL_EDITING_TABLES):
        EditingPersistenceBase.metadata.tables[table_name].drop(bind, checkfirst=True)


_ORIGINAL_EDITING_TABLES = (
    "xingjing_editing_timeline_heads",
    "xingjing_editing_timeline_versions",
    "xingjing_editing_timeline_idempotency",
    "xingjing_editing_render_tasks",
    "xingjing_editing_render_callback_receipts",
    "xingjing_editing_final_video_versions",
    "xingjing_editing_render_outbox",
)
