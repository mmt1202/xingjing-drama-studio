"""persist S06 supplier reconciliation evidence

Revision ID: e1s5w9x3y817
Revises: d0r4v8w2x706
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e1s5w9x3y817"
down_revision: str | Sequence[str] | None = "d0r4v8w2x706"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE xingjing_admin_finance_operations "
        "ADD COLUMN IF NOT EXISTS result_payload JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE xingjing_admin_finance_operations DROP COLUMN IF EXISTS result_payload")
