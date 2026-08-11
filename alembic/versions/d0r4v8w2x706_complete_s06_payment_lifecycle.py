"""complete S06 payment lifecycle

Revision ID: d0r4v8w2x706
Revises: c9q3u7v1w695
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d0r4v8w2x706"
down_revision: str | Sequence[str] | None = "c9q3u7v1w695"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE xingjing_team_billing_orders ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute("ALTER TABLE xingjing_team_billing_orders ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ")
    op.execute("ALTER TABLE xingjing_team_billing_orders ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE xingjing_team_billing_orders "
        "ADD COLUMN IF NOT EXISTS refunded_minor BIGINT NOT NULL DEFAULT 0"
    )
    op.execute("UPDATE xingjing_team_billing_orders SET expires_at = updated_at + INTERVAL '1 day' WHERE expires_at IS NULL")
    op.execute("ALTER TABLE xingjing_team_billing_orders ALTER COLUMN expires_at SET NOT NULL")
    op.execute(
        """
        DO $$ BEGIN
          ALTER TABLE xingjing_team_billing_orders
          ADD CONSTRAINT ck_xj_team_order_refunded
          CHECK (refunded_minor >= 0 AND refunded_minor <= amount_minor);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE xingjing_team_billing_orders DROP CONSTRAINT IF EXISTS ck_xj_team_order_refunded")
    op.execute("ALTER TABLE xingjing_team_billing_orders DROP COLUMN IF EXISTS refunded_minor")
    op.execute("ALTER TABLE xingjing_team_billing_orders DROP COLUMN IF EXISTS closed_at")
    op.execute("ALTER TABLE xingjing_team_billing_orders DROP COLUMN IF EXISTS paid_at")
    op.execute("ALTER TABLE xingjing_team_billing_orders DROP COLUMN IF EXISTS expires_at")
