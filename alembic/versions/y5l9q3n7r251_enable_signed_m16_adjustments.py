"""enable signed M16 finance adjustments

Revision ID: y5l9q3n7r251
Revises: x4k8p2m6q140
"""

from collections.abc import Sequence

from alembic import op

revision: str = "y5l9q3n7r251"
down_revision: str | Sequence[str] | None = "x4k8p2m6q140"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M16 finance and models requires PostgreSQL")
    op.drop_constraint(
        "ck_xj_admin_finance_amount",
        "xingjing_admin_finance_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_xj_admin_finance_amount",
        "xingjing_admin_finance_operations",
        "amount_minor <> 0 OR operation_type = 'reconciliation'",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M16 finance and models requires PostgreSQL")
    op.drop_constraint(
        "ck_xj_admin_finance_amount",
        "xingjing_admin_finance_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_xj_admin_finance_amount",
        "xingjing_admin_finance_operations",
        "amount_minor >= 0",
    )
