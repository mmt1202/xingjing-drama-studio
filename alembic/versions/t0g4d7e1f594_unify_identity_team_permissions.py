"""unify identity membership and team permission truth

Revision ID: t0g4d7e1f594
Revises: s9f3c6d0e483
"""

import json
from collections.abc import Sequence

from alembic import op
from server.xingjing_identity_context.permissions import DEFAULT_ROLE_PERMISSIONS

revision: str = "t0g4d7e1f594"
down_revision: str | Sequence[str] | None = "s9f3c6d0e483"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_permissions(role_id: str) -> str:
    return json.dumps(sorted(DEFAULT_ROLE_PERMISSIONS[role_id]), separators=(",", ":")).replace("'", "''")


def upgrade() -> None:
    owner_permissions = _json_permissions("owner")
    admin_permissions = _json_permissions("admin")
    member_permissions = _json_permissions("member")

    op.execute(
        f"""
        CREATE FUNCTION ensure_team_workspace_from_identity(identity_workspace_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO xingjing_team_workspaces (
                tenant_id, workspace_id, seat_limit, permission_version
            ) VALUES (
                identity_workspace_id::text, identity_workspace_id::text, 5, 1
            )
            ON CONFLICT (tenant_id, workspace_id) DO NOTHING;

            INSERT INTO xingjing_team_roles (
                tenant_id, workspace_id, role_id, name, permissions, version
            ) VALUES
                (
                    identity_workspace_id::text, identity_workspace_id::text,
                    'owner', '所有者', '{owner_permissions}'::jsonb, 1
                ),
                (
                    identity_workspace_id::text, identity_workspace_id::text,
                    'admin', '管理员', '{admin_permissions}'::jsonb, 1
                ),
                (
                    identity_workspace_id::text, identity_workspace_id::text,
                    'member', '成员', '{member_permissions}'::jsonb, 1
                )
            ON CONFLICT (tenant_id, workspace_id, role_id) DO NOTHING;
        END;
        $$;
        """
    )

    op.execute("SELECT ensure_team_workspace_from_identity(id) FROM identity.workspaces")
    op.execute(
        f"""
        UPDATE xingjing_team_roles SET
            permissions = CASE lower(role_id)
                WHEN 'owner' THEN '{owner_permissions}'::jsonb
                WHEN 'admin' THEN '{admin_permissions}'::jsonb
                ELSE '{member_permissions}'::jsonb
            END
        WHERE lower(role_id) IN ('owner', 'admin', 'member')
          AND version = 1;
        """
    )
    op.execute(
        """
        UPDATE xingjing_team_members
        SET role_id = lower(role_id)
        WHERE role_id IN ('OWNER', 'ADMIN', 'MEMBER');

        UPDATE xingjing_team_invitations
        SET role_id = lower(role_id)
        WHERE role_id IN ('OWNER', 'ADMIN', 'MEMBER');

        DELETE FROM xingjing_team_roles
        WHERE role_id IN ('OWNER', 'ADMIN', 'MEMBER');
        """
    )

    op.execute(
        """
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
        """
    )
    op.execute(
        """
        CREATE FUNCTION sync_identity_member_to_team() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            member_email text;
            normalized_role text;
            old_role text;
            old_active boolean;
        BEGIN
            PERFORM ensure_team_workspace_from_identity(NEW.workspace_id);
            SELECT lower(email) INTO member_email FROM identity.users WHERE id = NEW.user_id;
            normalized_role := CASE upper(NEW.role_key)
                WHEN 'OWNER' THEN 'owner'
                WHEN 'ADMIN' THEN 'admin'
                ELSE 'member'
            END;

            SELECT role_id, active INTO old_role, old_active
            FROM xingjing_team_members
            WHERE tenant_id = NEW.workspace_id::text
              AND workspace_id = NEW.workspace_id::text
              AND member_id = NEW.user_id::text;

            INSERT INTO xingjing_team_members (
                tenant_id, workspace_id, member_id, email, role_id, active
            ) VALUES (
                NEW.workspace_id::text,
                NEW.workspace_id::text,
                NEW.user_id::text,
                member_email,
                normalized_role,
                NEW.status = 'ACTIVE'
            )
            ON CONFLICT (tenant_id, workspace_id, member_id) DO UPDATE SET
                email = excluded.email,
                role_id = excluded.role_id,
                active = excluded.active;

            IF old_role IS DISTINCT FROM normalized_role
               OR old_active IS DISTINCT FROM (NEW.status = 'ACTIVE') THEN
                UPDATE xingjing_team_workspaces
                SET permission_version = permission_version + 1
                WHERE tenant_id = NEW.workspace_id::text
                  AND workspace_id = NEW.workspace_id::text;
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_sync_identity_member_to_team
        AFTER INSERT OR UPDATE OF role_key, status ON identity.workspace_members
        FOR EACH ROW EXECUTE FUNCTION sync_identity_member_to_team();
        """
    )
    op.execute(
        """
        INSERT INTO xingjing_team_members (
            tenant_id, workspace_id, member_id, email, role_id, active
        )
        SELECT
            member.workspace_id::text,
            member.workspace_id::text,
            member.user_id::text,
            lower(identity_user.email),
            CASE upper(member.role_key)
                WHEN 'OWNER' THEN 'owner'
                WHEN 'ADMIN' THEN 'admin'
                ELSE 'member'
            END,
            member.status = 'ACTIVE'
        FROM identity.workspace_members AS member
        JOIN identity.users AS identity_user ON identity_user.id = member.user_id
        ON CONFLICT (tenant_id, workspace_id, member_id) DO UPDATE SET
            email = excluded.email,
            role_id = excluded.role_id,
            active = excluded.active;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_identity_member_to_team ON identity.workspace_members")
    op.execute("DROP FUNCTION IF EXISTS sync_identity_member_to_team()")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_identity_workspace_to_team ON identity.workspaces")
    op.execute("DROP FUNCTION IF EXISTS sync_identity_workspace_to_team()")
    op.execute("DROP FUNCTION IF EXISTS ensure_team_workspace_from_identity(uuid)")
