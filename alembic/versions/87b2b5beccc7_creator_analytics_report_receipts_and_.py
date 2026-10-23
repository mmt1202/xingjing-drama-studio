"""Creator analytics report receipts and permissions.

Revision ID: 87b2b5beccc7
Revises: 14332a65527a
"""

import json
from collections.abc import Sequence

from sqlalchemy import select, update

from alembic import op
from server.xingjing_team_persistence.repository import RoleRow, WorkspaceRow

revision: str = "87b2b5beccc7"
down_revision: str | Sequence[str] | None = "14332a65527a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANALYTICS = {"analytics.view", "analytics.manage"}
_OWNER_BASE = frozenset(
    [
        "asset.manage",
        "asset.view",
        "audio.manage",
        "audio.view",
        "billing.manage",
        "billing.view",
        "commercial.manage",
        "commercial.view",
        "community.manage",
        "community.view",
        "compliance.manage",
        "compliance.view",
        "export.manage",
        "export.view",
        "final.manage",
        "final.view",
        "generation.manage",
        "generation.view",
        "project.manage",
        "project.view",
        "review.manage",
        "review.view",
        "script.manage",
        "script.view",
        "settings.manage",
        "settings.view",
        "shot.manage",
        "shot.view",
        "template.manage",
        "template.view",
        "workspace.audit.manage",
        "workspace.audit.view",
        "workspace.enterprise.manage",
        "workspace.enterprise.view",
        "workspace.manage",
        "workspace.member.manage",
        "workspace.member.view",
        "workspace.view",
    ]
)
_MEMBER_BASE = frozenset(
    [
        "asset.view",
        "audio.view",
        "commercial.view",
        "community.view",
        "compliance.view",
        "export.view",
        "final.view",
        "generation.view",
        "project.view",
        "review.view",
        "script.view",
        "shot.view",
        "template.view",
        "workspace.audit.view",
        "workspace.member.view",
        "workspace.view",
    ]
)


def _roles(add: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(select(RoleRow).where(RoleRow.role_id.in_(("owner", "admin")))).mappings().all()
    workspaces: set[tuple[str, str]] = set()
    for row in rows:
        before = set(row["permissions"])
        after = before | _ANALYTICS if add else before - _ANALYTICS
        if after == before:
            continue
        connection.execute(
            update(RoleRow)
            .where(
                RoleRow.tenant_id == row["tenant_id"],
                RoleRow.workspace_id == row["workspace_id"],
                RoleRow.role_id == row["role_id"],
                RoleRow.version == row["version"],
            )
            .values(permissions=sorted(after), version=RoleRow.version + 1)
        )
        workspaces.add((row["tenant_id"], row["workspace_id"]))
    for tenant_id, workspace_id in workspaces:
        connection.execute(
            update(WorkspaceRow)
            .where(WorkspaceRow.tenant_id == tenant_id, WorkspaceRow.workspace_id == workspace_id)
            .values(permission_version=WorkspaceRow.permission_version + 1)
        )
    if connection.dialect.name != "postgresql":
        return
    owner = _OWNER_BASE | _ANALYTICS if add else _OWNER_BASE
    defaults = {"owner": owner, "admin": owner - {"workspace.manage"}, "member": _MEMBER_BASE}
    names = {"owner": "所有者", "admin": "管理员", "member": "成员"}
    values = ",".join(
        "(identity_workspace_id::text,identity_workspace_id::text,"
        f"'{role}','{names[role]}','{json.dumps(sorted(permissions))}'::jsonb,1)"
        for role, permissions in defaults.items()
    )
    op.execute(
        f"""CREATE OR REPLACE FUNCTION ensure_team_workspace_from_identity(identity_workspace_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$ BEGIN
          INSERT INTO xingjing_team_workspaces (tenant_id,workspace_id,seat_limit,permission_version)
          VALUES (identity_workspace_id::text,identity_workspace_id::text,5,1)
          ON CONFLICT (tenant_id,workspace_id) DO NOTHING;
          INSERT INTO xingjing_team_roles (tenant_id,workspace_id,role_id,name,permissions,version)
          VALUES {values} ON CONFLICT (tenant_id,workspace_id,role_id) DO NOTHING;
        END; $$;"""
    )


def upgrade() -> None:
    from server.xingjing_analytics.models import AnalyticsBase

    AnalyticsBase.metadata.create_all(op.get_bind(), checkfirst=True)
    _roles(True)


def downgrade() -> None:
    from server.xingjing_analytics.models import AnalyticsBase

    _roles(False)
    AnalyticsBase.metadata.drop_all(op.get_bind(), checkfirst=True)
