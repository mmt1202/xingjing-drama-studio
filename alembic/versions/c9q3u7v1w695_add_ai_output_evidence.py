"""add structured AI output evidence

Revision ID: c9q3u7v1w695
Revises: b8p2t6u0v584
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9q3u7v1w695"
down_revision: str | Sequence[str] | None = "b8p2t6u0v584"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("xingjing_content_ai_requests")}
    if "raw_response" not in columns:
        op.add_column("xingjing_content_ai_requests", sa.Column("raw_response", sa.Text()))
    if "evidence" not in columns:
        op.add_column("xingjing_content_ai_requests", sa.Column("evidence", sa.JSON()))


def downgrade() -> None:
    op.drop_column("xingjing_content_ai_requests", "evidence")
    op.drop_column("xingjing_content_ai_requests", "raw_response")
