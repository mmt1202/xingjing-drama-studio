"""create_xingjing_audio_persistence_tables

Revision ID: c7a6e4b9d102
Revises: a9e6f4c2b817
Create Date: 2026-07-18 00:00:00.000000

M07 deliberately has PostgreSQL-only JSONB and stable string identifiers.  This
migration stores object keys and digests only; it never stores media bytes.
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_audio_persistence.models import metadata

# revision identifiers, used by Alembic.
revision: str = "c7a6e4b9d102"
down_revision: str | Sequence[str] | None = "a9e6f4c2b817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the M07 PostgreSQL tables from their canonical SQLAlchemy metadata."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M07 audio persistence requires PostgreSQL; SQLite is not a production substitute")
    for table in metadata.sorted_tables:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    """Drop M07 tables in reverse dependency order."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M07 audio persistence requires PostgreSQL; SQLite is not a production substitute")
    for table in reversed(metadata.sorted_tables):
        table.drop(bind=bind, checkfirst=False)
