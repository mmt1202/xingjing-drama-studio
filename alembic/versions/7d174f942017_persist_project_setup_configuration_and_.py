"""persist project setup configuration and drafts

Revision ID: 7d174f942017
Revises: k9t4m7p2r608
Create Date: 2026-10-22 15:14:10.153406

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d174f942017"
down_revision: str | Sequence[str] | None = "k9t4m7p2r608"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("xingjing_projects")}
    additions = (
        sa.Column("setup_state", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("configuration", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("creation_digest", sa.String(64), nullable=True),
        sa.Column("last_setup_key", sa.String(128), nullable=True),
        sa.Column("last_setup_digest", sa.String(64), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("xingjing_projects", column)


def downgrade() -> None:
    for name in ("last_setup_digest", "last_setup_key", "creation_digest", "configuration", "setup_state"):
        op.drop_column("xingjing_projects", name)
