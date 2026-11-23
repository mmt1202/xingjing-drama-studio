"""Track whether an admitted generation task occupies a model slot."""

import sqlalchemy as sa

from alembic import op

revision = "8bd26f6cd423"
down_revision = "5ac5109a0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "xingjing_model_route_reservations",
        sa.Column("capacity_reserved", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("xingjing_model_route_reservations", "capacity_reserved")
