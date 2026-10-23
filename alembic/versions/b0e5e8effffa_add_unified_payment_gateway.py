"""Persist unified gateway evidence, commands and refund reserves.

Revision ID: b0e5e8effffa
Revises: a9d4d7deeee9
"""

from alembic import op
from server.xingjing_payments.models import PaymentBase

revision = "b0e5e8effffa"
down_revision = "a9d4d7deeee9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    PaymentBase.metadata.create_all(bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        op.execute("""CREATE OR REPLACE FUNCTION xingjing_payment_evidence_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'payment evidence is append-only'; END $$""")
        for table in ("xingjing_payment_audit", "xingjing_payment_gateway_events"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION xingjing_payment_evidence_immutable()"
            )
        op.execute("""CREATE TRIGGER trg_xj_payment_journal_immutable BEFORE UPDATE OR DELETE
        ON xingjing_generation_billing_journals FOR EACH ROW
        WHEN (OLD.action IN ('payment_grant','refund_reserve','refund_release','refund_settle'))
        EXECUTE FUNCTION xingjing_payment_evidence_immutable()""")


def downgrade() -> None:
    raise RuntimeError("Payment and refund settlement evidence is retained")
