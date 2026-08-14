"""version M10 member mutations

Revision ID: k9t4m7p2r608
Revises: j8s3w7y1z596
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k9t4m7p2r608"
down_revision: str | Sequence[str] | None = "j8s3w7y1z596"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "xingjing_team_members",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        CREATE FUNCTION xingjing_team_member_version_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.version = OLD.version AND
               (NEW.role_id IS DISTINCT FROM OLD.role_id OR NEW.active IS DISTINCT FROM OLD.active) THEN
                NEW.version := OLD.version + 1;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_xingjing_team_member_version_guard
        BEFORE UPDATE OF role_id, active ON xingjing_team_members
        FOR EACH ROW EXECUTE FUNCTION xingjing_team_member_version_guard();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_xingjing_team_member_version_guard ON xingjing_team_members")
    op.execute("DROP FUNCTION xingjing_team_member_version_guard()")
    op.drop_column("xingjing_team_members", "version")
