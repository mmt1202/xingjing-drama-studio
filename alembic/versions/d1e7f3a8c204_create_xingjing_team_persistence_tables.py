"""create_xingjing_team_persistence_tables

Revision ID: d1e7f3a8c204
Revises: c7a6e4b9d102
Create Date: 2026-07-18 00:00:00.000000

M10 is PostgreSQL-only because invitation seat allocation and immutable audit
events rely on row locks, partial indexes and database-side trigger protection.
"""

from collections.abc import Sequence

from alembic import op

from server.xingjing_team_persistence import TeamPersistenceBase


revision: str = "d1e7f3a8c204"
down_revision: str | Sequence[str] | None = "c7a6e4b9d102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M10 team persistence requires PostgreSQL; SQLite is not a production substitute")
    for table in TeamPersistenceBase.metadata.sorted_tables:
        table.create(bind=bind, checkfirst=False)
    op.execute(
        """
        CREATE FUNCTION xingjing_team_audit_events_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'xingjing_team_audit_events are immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_xingjing_team_audit_events_immutable
        BEFORE UPDATE OR DELETE ON xingjing_team_audit_events
        FOR EACH ROW EXECUTE FUNCTION xingjing_team_audit_events_immutable();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M10 team persistence requires PostgreSQL; SQLite is not a production substitute")
    op.execute("DROP TRIGGER trg_xingjing_team_audit_events_immutable ON xingjing_team_audit_events")
    op.execute("DROP FUNCTION xingjing_team_audit_events_immutable()")
    for table in reversed(TeamPersistenceBase.metadata.sorted_tables):
        table.drop(bind=bind, checkfirst=False)
