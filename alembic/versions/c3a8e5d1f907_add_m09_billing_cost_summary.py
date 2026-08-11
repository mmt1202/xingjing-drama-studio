"""add M09 settled cost breakdown

Revision ID: c3a8e5d1f907
Revises: b2f7d4c9e806
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3a8e5d1f907"
down_revision: str | Sequence[str] | None = "b2f7d4c9e806"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "xingjing_compliance_billing_settlements",
        sa.Column(
            "cost_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("xingjing_compliance_billing_settlements", "cost_summary", server_default=None)


def downgrade() -> None:
    op.drop_column("xingjing_compliance_billing_settlements", "cost_summary")
