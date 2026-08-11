"""complete M13 marketplace transactions

Revision ID: l3f6d0c4e827
Revises: k2e5c9b3d716
Create Date: 2026-07-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "l3f6d0c4e827"
down_revision: str | Sequence[str] | None = "k2e5c9b3d716"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M13 marketplace persistence requires PostgreSQL")
    op.create_table(
        "xingjing_marketplace_purchases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("buyer_workspace_id", sa.String(64), nullable=False),
        sa.Column("seller_workspace_id", sa.String(64), nullable=False),
        sa.Column("market_item_id", sa.String(36), nullable=False),
        sa.Column("fork_id", sa.String(36), nullable=False),
        sa.Column("target_project_id", sa.String(36), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("revenue_rule_id", sa.String(36), nullable=False),
        sa.Column("distributions_json", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refunded_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["market_item_id"], ["xingjing_marketplace_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fork_id"], ["xingjing_marketplace_forks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_project_id"], ["xingjing_projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("buyer_workspace_id", "fork_id", name="uq_xj_marketplace_purchase_fork"),
        sa.CheckConstraint("amount_minor >= 0", name="ck_xj_marketplace_purchase_amount"),
        sa.CheckConstraint("status IN ('settled', 'refunded')", name="ck_xj_marketplace_purchase_status"),
    )
    op.create_index(
        "ix_xj_marketplace_purchase_buyer_time",
        "xingjing_marketplace_purchases",
        ["buyer_workspace_id", "created_at", "id"],
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION xingjing_marketplace_audit_immutable()
        RETURNS trigger AS $$ BEGIN
          RAISE EXCEPTION 'marketplace audit events are immutable';
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_xingjing_marketplace_audit_immutable
        BEFORE UPDATE OR DELETE ON xingjing_marketplace_audit_events
        FOR EACH ROW EXECUTE FUNCTION xingjing_marketplace_audit_immutable();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M13 marketplace persistence requires PostgreSQL")
    op.execute("DROP TRIGGER IF EXISTS trg_xingjing_marketplace_audit_immutable ON xingjing_marketplace_audit_events")
    op.execute("DROP FUNCTION IF EXISTS xingjing_marketplace_audit_immutable()")
    op.drop_index("ix_xj_marketplace_purchase_buyer_time", table_name="xingjing_marketplace_purchases")
    op.drop_table("xingjing_marketplace_purchases")
