"""rebind identity and team synchronization to the identity schema

Revision ID: w3j7k0l4m827
Revises: v2i6f9g3h716
"""

import json
from collections.abc import Sequence

from alembic import op
from server.xingjing_identity_context.permissions import DEFAULT_ROLE_PERMISSIONS

revision: str = "w3j7k0l4m827"
down_revision: str | Sequence[str] | None = "v2i6f9g3h716"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _permissions(role_id: str) -> str:
    return json.dumps(sorted(DEFAULT_ROLE_PERMISSIONS[role_id]), separators=(",", ":")).replace("'", "''")


def upgrade() -> None:
    owner = _permissions("owner")
    admin = _permissions("admin")
    member = _permissions("member")
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.workspace_members') IS NOT NULL THEN
            DROP TRIGGER IF EXISTS trg_sync_identity_member_to_team ON public.workspace_members;
          END IF;
          IF to_regclass('public.workspaces') IS NOT NULL THEN
            DROP TRIGGER IF EXISTS trg_sync_identity_workspace_to_team ON public.workspaces;
          END IF;
        END $$;
        DROP TRIGGER IF EXISTS trg_sync_identity_member_to_team ON identity.workspace_members;
        DROP TRIGGER IF EXISTS trg_sync_identity_workspace_to_team ON identity.workspaces;
        DROP TRIGGER IF EXISTS trg_sync_team_invitation_to_identity_membership ON xingjing_team_invitations;
        DROP FUNCTION IF EXISTS sync_identity_member_to_team();
        DROP FUNCTION IF EXISTS sync_identity_workspace_to_team();
        DROP FUNCTION IF EXISTS sync_team_invitation_to_identity_membership();
        DROP FUNCTION IF EXISTS ensure_team_workspace_from_identity(uuid);
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION ensure_team_workspace_from_identity(identity_workspace_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO xingjing_team_workspaces (tenant_id,workspace_id,seat_limit,permission_version)
          VALUES (identity_workspace_id::text,identity_workspace_id::text,5,1)
          ON CONFLICT (tenant_id,workspace_id) DO NOTHING;
          INSERT INTO xingjing_team_roles (tenant_id,workspace_id,role_id,name,permissions,version)
          VALUES
            (identity_workspace_id::text,identity_workspace_id::text,'owner','所有者','{owner}'::jsonb,1),
            (identity_workspace_id::text,identity_workspace_id::text,'admin','管理员','{admin}'::jsonb,1),
            (identity_workspace_id::text,identity_workspace_id::text,'member','成员','{member}'::jsonb,1)
          ON CONFLICT (tenant_id,workspace_id,role_id) DO NOTHING;
        END;
        $$;

        CREATE FUNCTION sync_identity_workspace_to_team() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM ensure_team_workspace_from_identity(NEW.id);
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_sync_identity_workspace_to_team
          AFTER INSERT OR UPDATE OF status ON identity.workspaces
          FOR EACH ROW EXECUTE FUNCTION sync_identity_workspace_to_team();

        CREATE FUNCTION sync_identity_member_to_team() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE member_email text; normalized_role text; old_role text; old_active boolean;
        BEGIN
          PERFORM ensure_team_workspace_from_identity(NEW.workspace_id);
          SELECT lower(email) INTO member_email FROM identity.users WHERE id=NEW.user_id;
          normalized_role := CASE upper(NEW.role_key)
            WHEN 'OWNER' THEN 'owner' WHEN 'ADMIN' THEN 'admin' ELSE 'member' END;
          SELECT role_id,active INTO old_role,old_active FROM xingjing_team_members
            WHERE tenant_id=NEW.workspace_id::text AND workspace_id=NEW.workspace_id::text
              AND member_id=NEW.user_id::text;
          INSERT INTO xingjing_team_members (tenant_id,workspace_id,member_id,email,role_id,active)
          VALUES (NEW.workspace_id::text,NEW.workspace_id::text,NEW.user_id::text,
                  member_email,normalized_role,NEW.status='ACTIVE')
          ON CONFLICT (tenant_id,workspace_id,member_id) DO UPDATE SET
            email=excluded.email,role_id=excluded.role_id,active=excluded.active;
          IF old_role IS DISTINCT FROM normalized_role
             OR old_active IS DISTINCT FROM (NEW.status='ACTIVE') THEN
            UPDATE xingjing_team_workspaces SET permission_version=permission_version+1
              WHERE tenant_id=NEW.workspace_id::text AND workspace_id=NEW.workspace_id::text;
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_sync_identity_member_to_team
          AFTER INSERT OR UPDATE OF role_key,status ON identity.workspace_members
          FOR EACH ROW EXECUTE FUNCTION sync_identity_member_to_team();
        """
    )
    op.execute(
        """
        CREATE FUNCTION sync_team_invitation_to_identity_membership() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE identity_user_id uuid;
        BEGIN
          SELECT id INTO identity_user_id FROM identity.users WHERE lower(email)=lower(NEW.email);
          IF identity_user_id IS NULL THEN RETURN NEW; END IF;
          IF NEW.state='accepted' THEN
            INSERT INTO identity.workspace_members AS target
              (workspace_id,user_id,role_key,status,joined_at,created_at,created_by,updated_at,updated_by,version)
            VALUES (NEW.workspace_id::uuid,identity_user_id,upper(NEW.role_id),'ACTIVE',current_timestamp,
                    current_timestamp,NEW.accepted_by::uuid,current_timestamp,NEW.accepted_by::uuid,0)
            ON CONFLICT (workspace_id,user_id) DO UPDATE SET
              role_key=CASE WHEN target.role_key='OWNER' THEN target.role_key ELSE excluded.role_key END,
              status='ACTIVE',joined_at=CASE WHEN target.status='ACTIVE' THEN target.joined_at ELSE excluded.joined_at END,
              updated_at=current_timestamp,updated_by=excluded.updated_by,version=target.version+1;
          ELSIF NEW.state IN ('revoked','expired') THEN
            UPDATE identity.workspace_members SET status='REMOVED',updated_at=current_timestamp,
              updated_by=coalesce(NEW.accepted_by::uuid,NEW.invited_by::uuid),version=version+1
              WHERE workspace_id=NEW.workspace_id::uuid AND user_id=identity_user_id AND status='INVITED';
          ELSE
            INSERT INTO identity.workspace_members AS target
              (workspace_id,user_id,role_key,status,joined_at,created_at,created_by,updated_at,updated_by,version)
            VALUES (NEW.workspace_id::uuid,identity_user_id,upper(NEW.role_id),'INVITED',current_timestamp,
                    current_timestamp,NEW.invited_by::uuid,current_timestamp,NEW.invited_by::uuid,0)
            ON CONFLICT (workspace_id,user_id) DO UPDATE SET
              role_key=CASE WHEN target.role_key='OWNER' THEN target.role_key ELSE excluded.role_key END,
              status=CASE WHEN target.status='ACTIVE' THEN 'ACTIVE' ELSE 'INVITED' END,
              updated_at=current_timestamp,updated_by=excluded.updated_by,version=target.version+1;
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_sync_team_invitation_to_identity_membership
          AFTER INSERT OR UPDATE OF state ON xingjing_team_invitations
          FOR EACH ROW EXECUTE FUNCTION sync_team_invitation_to_identity_membership();
        """
    )
    op.execute("SELECT ensure_team_workspace_from_identity(id) FROM identity.workspaces")
    op.execute(
        """
        INSERT INTO xingjing_team_members (tenant_id,workspace_id,member_id,email,role_id,active)
        SELECT member.workspace_id::text,member.workspace_id::text,member.user_id::text,
               lower(identity_user.email),
               CASE upper(member.role_key) WHEN 'OWNER' THEN 'owner' WHEN 'ADMIN' THEN 'admin' ELSE 'member' END,
               member.status='ACTIVE'
          FROM identity.workspace_members AS member
          JOIN identity.users AS identity_user ON identity_user.id=member.user_id
        ON CONFLICT (tenant_id,workspace_id,member_id) DO UPDATE SET
          email=excluded.email,role_id=excluded.role_id,active=excluded.active
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_sync_team_invitation_to_identity_membership ON xingjing_team_invitations;
        DROP TRIGGER IF EXISTS trg_sync_identity_member_to_team ON identity.workspace_members;
        DROP TRIGGER IF EXISTS trg_sync_identity_workspace_to_team ON identity.workspaces;
        DROP FUNCTION IF EXISTS sync_team_invitation_to_identity_membership();
        DROP FUNCTION IF EXISTS sync_identity_member_to_team();
        DROP FUNCTION IF EXISTS sync_identity_workspace_to_team();
        DROP FUNCTION IF EXISTS ensure_team_workspace_from_identity(uuid);
        """
    )
