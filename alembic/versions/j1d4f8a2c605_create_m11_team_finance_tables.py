"""create M11 team finance tables

Revision ID: j1d4f8a2c605
Revises: h8c1e4f2a309
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_billing_persistence import BillingPersistenceBase

revision: str = "j1d4f8a2c605"
down_revision: str | Sequence[str] | None = "h8c1e4f2a309"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    BillingPersistenceBase.metadata.create_all(op.get_bind(), checkfirst=True)
    op.execute(
        """
        CREATE FUNCTION xingjing_team_billing_audit_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'xingjing_team_billing_audit_events are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_xingjing_team_billing_audit_immutable
        BEFORE UPDATE OR DELETE ON xingjing_team_billing_audit_events
        FOR EACH ROW EXECUTE FUNCTION xingjing_team_billing_audit_immutable()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_xingjing_team_billing_audit_immutable ON xingjing_team_billing_audit_events")
    op.execute("DROP FUNCTION IF EXISTS xingjing_team_billing_audit_immutable()")
    BillingPersistenceBase.metadata.drop_all(op.get_bind(), checkfirst=True)
