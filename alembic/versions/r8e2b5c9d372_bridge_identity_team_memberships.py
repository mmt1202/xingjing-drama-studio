"""bridge identity workspace memberships with team invitation state

Revision ID: r8e2b5c9d372
Revises: q7d0a4b8c261
"""

from collections.abc import Sequence

from alembic import op

revision: str = "r8e2b5c9d372"
down_revision: str | Sequence[str] | None = "q7d0a4b8c261"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION sync_team_invitation_to_identity_membership() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            identity_user_id uuid;
        BEGIN
            SELECT id INTO identity_user_id FROM identity.users WHERE lower(email) = lower(NEW.email);
            IF identity_user_id IS NULL THEN
                RETURN NEW;
            END IF;

            IF NEW.state = 'accepted' THEN
                INSERT INTO identity.workspace_members (
                    workspace_id,user_id,role_key,status,joined_at,created_at,created_by,
                    updated_at,updated_by,version
                ) VALUES (
                    NEW.workspace_id::uuid,identity_user_id,upper(NEW.role_id),'ACTIVE',
                    current_timestamp,current_timestamp,NEW.accepted_by::uuid,
                    current_timestamp,NEW.accepted_by::uuid,0
                )
                ON CONFLICT (workspace_id,user_id) DO UPDATE SET
                    role_key=excluded.role_key,
                    status='ACTIVE',
                    joined_at=current_timestamp,
                    updated_at=current_timestamp,
                    updated_by=excluded.updated_by,
                    version=workspace_members.version+1;
            ELSIF NEW.state IN ('revoked','expired') THEN
                UPDATE identity.workspace_members SET
                    status='REMOVED',
                    updated_at=current_timestamp,
                    updated_by=coalesce(NEW.accepted_by::uuid,NEW.invited_by::uuid),
                    version=version+1
                WHERE workspace_id=NEW.workspace_id::uuid
                  AND user_id=identity_user_id
                  AND status='INVITED';
            ELSE
                INSERT INTO identity.workspace_members (
                    workspace_id,user_id,role_key,status,joined_at,created_at,created_by,
                    updated_at,updated_by,version
                ) VALUES (
                    NEW.workspace_id::uuid,identity_user_id,upper(NEW.role_id),'INVITED',
                    current_timestamp,current_timestamp,NEW.invited_by::uuid,
                    current_timestamp,NEW.invited_by::uuid,0
                )
                ON CONFLICT (workspace_id,user_id) DO UPDATE SET
                    role_key=excluded.role_key,
                    status=CASE WHEN workspace_members.status='ACTIVE' THEN 'ACTIVE' ELSE 'INVITED' END,
                    updated_at=current_timestamp,
                    updated_by=excluded.updated_by,
                    version=workspace_members.version+1;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sync_team_invitation_to_identity_membership
        AFTER INSERT OR UPDATE OF state ON xingjing_team_invitations
        FOR EACH ROW EXECUTE FUNCTION sync_team_invitation_to_identity_membership();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_sync_team_invitation_to_identity_membership ON xingjing_team_invitations"
    )
    op.execute("DROP FUNCTION sync_team_invitation_to_identity_membership()")
