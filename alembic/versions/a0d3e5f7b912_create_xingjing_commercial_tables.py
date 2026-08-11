"""create_xingjing_commercial_tables

Revision ID: a0d3e5f7b912
Revises: f9a2b6c8d701
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_commercial_persistence import CommercialPersistenceBase

revision: str = "a0d3e5f7b912"
down_revision: str | Sequence[str] | None = "f9a2b6c8d701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M14 commercial persistence requires PostgreSQL; SQLite is not a production substitute")
    for table in CommercialPersistenceBase.metadata.sorted_tables:
        table.create(bind=bind, checkfirst=False)
    op.execute(
        """
        CREATE FUNCTION xingjing_commercial_audit_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'xingjing_commercial_audit is immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_xingjing_commercial_audit_immutable
        BEFORE UPDATE OR DELETE ON xingjing_commercial_audit
        FOR EACH ROW EXECUTE FUNCTION xingjing_commercial_audit_immutable();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M14 commercial persistence requires PostgreSQL; SQLite is not a production substitute")
    op.execute("DROP TRIGGER trg_xingjing_commercial_audit_immutable ON xingjing_commercial_audit")
    op.execute("DROP FUNCTION xingjing_commercial_audit_immutable()")
    for table in reversed(CommercialPersistenceBase.metadata.sorted_tables):
        table.drop(bind=bind, checkfirst=False)
