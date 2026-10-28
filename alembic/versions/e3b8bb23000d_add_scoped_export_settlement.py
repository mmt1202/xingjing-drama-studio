"""Retain exact delivery-input settlement evidence alongside legacy receipts.

Revision ID: e3b8bb23000d
Revises: d2a7aa12000c
"""

from alembic import op
from server.xingjing_compliance_persistence.repository import ScopedBillingSettlementRow

revision = "e3b8bb23000d"
down_revision = "d2a7aa12000c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ScopedBillingSettlementRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Delivery-input settlement evidence and superseded receipts are retained")
