"""repair shared project persistence tables

Revision ID: z6m0r4n8s362
Revises: y5l9q3n7r251
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from server.xingjing_platform_persistence import Base

revision: str = "z6m0r4n8s362"
down_revision: str | Sequence[str] | None = "y5l9q3n7r251"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("xingjing_projects")}
    additions = {
        "project_type": sa.Column("project_type", sa.String(64), nullable=False, server_default="drama"),
        "target_platform": sa.Column("target_platform", sa.String(64), nullable=False, server_default="web"),
        "owner_id": sa.Column("owner_id", sa.String(64), nullable=False, server_default="system"),
        "production_status": sa.Column(
            "production_status", sa.String(64), nullable=False, server_default="draft"
        ),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("xingjing_projects", column)


def downgrade() -> None:
    for name in ("production_status", "owner_id", "target_platform", "project_type"):
        op.drop_column("xingjing_projects", name)
