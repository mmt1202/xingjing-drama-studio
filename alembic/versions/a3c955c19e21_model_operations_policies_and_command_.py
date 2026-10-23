"""model operations policies and command audit

Revision ID: a3c955c19e21
Revises: 8e2a4c6d901f
Create Date: 2026-10-23 12:05:09.576803

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from server.xingjing_model_control.admin_models import ModelAdminBase

# revision identifiers, used by Alembic.
revision: str = "a3c955c19e21"
down_revision: str | Sequence[str] | None = "8e2a4c6d901f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("xingjing_model_routing_policies")}
    if "routing_options" not in columns:
        op.add_column(
            "xingjing_model_routing_policies",
            sa.Column("routing_options", sa.JSON(), nullable=False, server_default="{}"),
        )
    ModelAdminBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Downgrade schema."""
    ModelAdminBase.metadata.drop_all(op.get_bind(), checkfirst=True)
    op.drop_column("xingjing_model_routing_policies", "routing_options")
