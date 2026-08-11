"""scope API keys to identity users and workspaces

Revision ID: s9f3c6d0e483
Revises: r8e2b5c9d372
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s9f3c6d0e483"
down_revision: str | Sequence[str] | None = "r8e2b5c9d372"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("workspace_id", sa.String(length=64), nullable=True))
    op.add_column(
        "api_keys",
        sa.Column("scopes", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column("api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("rotated_from_id", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.execute("UPDATE api_keys SET workspace_id = 'legacy' WHERE workspace_id IS NULL")
    op.alter_column("api_keys", "workspace_id", nullable=False)
    op.execute(
        """
        DO $$
        DECLARE constraint_name text;
        BEGIN
          SELECT conname INTO constraint_name
          FROM pg_constraint
          WHERE conrelid = 'api_keys'::regclass
            AND contype = 'u'
            AND pg_get_constraintdef(oid) = 'UNIQUE (name)';
          IF constraint_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE api_keys DROP CONSTRAINT %I', constraint_name);
          END IF;
        END $$;
        """
    )
    op.create_index(
        "uq_api_keys_active_scope_name",
        "api_keys",
        ["user_id", "workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_api_keys_workspace_id", "api_keys", ["workspace_id"])
    op.create_check_constraint("ck_api_keys_version_positive", "api_keys", "version > 0")
    op.create_table(
        "api_key_audit_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("before_payload", sa.JSON(), nullable=True),
        sa.Column("after_payload", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_api_key_audit_request", "api_key_audit_events", ["request_id"])
    op.create_index(
        "ix_api_key_audit_scope_time",
        "api_key_audit_events",
        ["workspace_id", "occurred_at", "event_id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_api_key_audit_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'api_key_audit_events are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER api_key_audit_no_update
          BEFORE UPDATE OR DELETE ON api_key_audit_events
          FOR EACH ROW EXECUTE FUNCTION prevent_api_key_audit_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS api_key_audit_no_update ON api_key_audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_api_key_audit_mutation()")
    op.drop_index("ix_api_key_audit_scope_time", table_name="api_key_audit_events")
    op.drop_index("ix_api_key_audit_request", table_name="api_key_audit_events")
    op.drop_table("api_key_audit_events")
    op.drop_constraint("ck_api_keys_version_positive", "api_keys", type_="check")
    op.drop_index("ix_api_keys_workspace_id", table_name="api_keys")
    op.drop_index("uq_api_keys_active_scope_name", table_name="api_keys")
    op.create_unique_constraint("uq_api_keys_name", "api_keys", ["name"])
    op.drop_column("api_keys", "version")
    op.drop_column("api_keys", "rotated_from_id")
    op.drop_column("api_keys", "revoked_at")
    op.drop_column("api_keys", "scopes")
    op.drop_column("api_keys", "workspace_id")
