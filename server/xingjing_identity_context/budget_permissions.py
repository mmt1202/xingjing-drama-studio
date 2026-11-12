"""Upgrade untouched built-in roles while preserving custom finance permissions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, insert, inspect, select, update

from server.xingjing_team_persistence.repository import AuditRow, RoleRow, WorkspaceRow

from .permissions import DEFAULT_ROLE_PERMISSIONS


def backfill_builtin_budget_permissions(connection: Connection) -> None:
    if not inspect(connection).has_table(RoleRow.__tablename__):
        return
    permission = "billing.budget.manage"
    changed: set[tuple[str, str]] = set()
    rows = connection.execute(
        select(RoleRow.__table__).where(RoleRow.role_id.in_(("owner", "admin"))).with_for_update()
    ).mappings()
    for row in rows:
        raw = row["permissions"]
        permissions = json.loads(raw) if isinstance(raw, str) else raw
        expected = DEFAULT_ROLE_PERMISSIONS[row["role_id"]]
        if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
            continue
        if frozenset(str(item) for item in permissions) != expected - {permission}:
            continue
        event_id = str(
            uuid5(NAMESPACE_URL, f"budget-permission:{row['tenant_id']}:{row['workspace_id']}:{row['role_id']}")
        )
        already_migrated = connection.execute(
            select(AuditRow.event_id).where(
                AuditRow.tenant_id == row["tenant_id"],
                AuditRow.workspace_id == row["workspace_id"],
                AuditRow.event_id == event_id,
            )
        ).first()
        if already_migrated is not None:
            continue
        criteria = (
            RoleRow.tenant_id == row["tenant_id"],
            RoleRow.workspace_id == row["workspace_id"],
            RoleRow.role_id == row["role_id"],
            RoleRow.version == row["version"],
        )
        updated = connection.execute(
            update(RoleRow).where(*criteria).values(permissions=sorted(expected), version=RoleRow.version + 1)
        )
        if not updated.rowcount:
            raise RuntimeError("Built-in role changed during budget permission migration")
        changed.add((row["tenant_id"], row["workspace_id"]))
        connection.execute(
            insert(AuditRow).values(
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                event_id=event_id,
                actor_id="schema-migration",
                action="role.budget_permission_registered",
                object_type="role",
                object_id=row["role_id"],
                before_payload={"permissions": permissions, "version": row["version"]},
                after_payload={"permissions": sorted(expected), "version": row["version"] + 1},
                request_id=event_id,
                result="success",
                occurred_at=datetime.now(UTC),
            )
        )
    for tenant_id, workspace_id in changed:
        connection.execute(
            update(WorkspaceRow)
            .where(WorkspaceRow.tenant_id == tenant_id, WorkspaceRow.workspace_id == workspace_id)
            .values(permission_version=WorkspaceRow.permission_version + 1)
        )
