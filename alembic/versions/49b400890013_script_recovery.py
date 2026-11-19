"""Keep the original source identity of recovered working copies and confirmation receipts."""

import sqlalchemy as sa

from alembic import op
from server.xingjing_script_recovery.models import RecoveryBase

revision = "49b400890013"
down_revision = "38a300780012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "recovery_origin" not in {column["name"] for column in sa.inspect(bind).get_columns("xingjing_content_scripts")}:
        op.add_column("xingjing_content_scripts", sa.Column("recovery_origin", sa.JSON(none_as_null=True)))
    RecoveryBase.metadata.create_all(bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Recovery receipts and working-copy provenance are retained")
