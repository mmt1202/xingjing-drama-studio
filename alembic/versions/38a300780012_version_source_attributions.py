"""Preserve confirmed source attributions in immutable script versions."""

import sqlalchemy as sa

from alembic import op
from server.xingjing_source_planning.mapping_models import SourceMappingBase

revision = "38a300780012"
down_revision = "27f2ff670011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = sa.inspect(op.get_bind()).get_columns("xingjing_content_script_versions")
    if not any(column["name"] == "source_attributions" for column in columns):
        op.add_column(
            "xingjing_content_script_versions",
            sa.Column("source_attributions", sa.JSON(), nullable=False, server_default="[]"),
        )
    SourceMappingBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Confirmed source attributions and audit history are retained")
