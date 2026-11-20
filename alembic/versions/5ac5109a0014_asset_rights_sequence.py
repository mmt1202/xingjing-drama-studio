"""Preserve the registration order and expiry of asset usage permissions."""

from datetime import UTC, date, datetime, time, timedelta

import sqlalchemy as sa

from alembic import op

revision = "5ac5109a0014"
down_revision = "49b400890013"
branch_labels = None
depends_on = None

TABLE = "xingjing_asset_rights_records"
INDEX = "ix_xj_rights_sequence"


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "sequence" in columns and "expires_at" in columns:
        return
    op.add_column(TABLE, sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(TABLE, sa.Column("expires_at", sa.DateTime(timezone=True)))
    metadata = sa.MetaData()
    rights = sa.Table(TABLE, metadata, autoload_with=bind)
    receipts = sa.Table("xingjing_asset_idempotency", metadata, autoload_with=bind)
    keys = ("tenant_id", "workspace_id", "asset_id")
    for item in bind.execute(sa.select(*(rights.c[k] for k in keys)).distinct()).mappings().all():
        scope = dict(item)
        rows = bind.execute(
            sa.select(rights.c.rights_id, rights.c.payload).filter_by(**scope).order_by(rights.c.rights_id)
        ).all()
        commits = (
            bind.execute(sa.select(receipts.c.result_payload).filter_by(operation_kind="asset.commit", **scope))
            .scalars()
            .all()
        )
        # Each receipt contains the complete asset at that revision. Use the first
        # appearance of a record, even when a later receipt loaded UUID-sorted history.
        order = {}
        for commit in sorted(commits, key=lambda c: c.get("revision", 0)):
            for record in commit.get("rights", ()):
                order.setdefault(record["rights_id"], len(order))
        for seq, (rid, payload) in enumerate(sorted(rows, key=lambda r: order.get(r[0], len(order))), 1):
            raw = payload.get("valid_until")
            expiry = None
            if raw:
                try:
                    if len(raw) == 10:
                        expiry = datetime.combine(date.fromisoformat(raw), time.min, UTC) + timedelta(days=1)
                    else:
                        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        expiry = stamp.astimezone(UTC) if stamp.tzinfo else stamp.replace(tzinfo=UTC)
                except (ValueError, OverflowError):
                    expiry = datetime(1970, 1, 1, tzinfo=UTC)
            bind.execute(rights.update().filter_by(rights_id=rid, **scope).values(sequence=seq, expires_at=expiry))
    op.create_index(INDEX, TABLE, [*keys, "sequence"], unique=True)


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "expires_at")
    op.drop_column(TABLE, "sequence")
