"""Initialize missing team projections from an authenticated Java membership only."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from server.xingjing_identity_context.permissions import DEFAULT_ROLE_PERMISSIONS


def provision_missing_team_membership(
    executor: Connection | Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    actor_email: str | None,
    identity_role: str,
    request_id: str,
) -> bool:
    """Preserve existing roles, permissions, membership state and role definitions.

    The caller must obtain these facts from the Java session gateway. Client
    headers, request bodies and domain permission sets are not membership proof.
    The caller owns the transaction so projection and permission lookup observe
    the same committed membership, including a concurrent revocation.
    """
    from server.xingjing_team_persistence.repository import AuditRow, MemberRow, RoleRow, WorkspaceRow

    member_key = (
        MemberRow.tenant_id == tenant_id,
        MemberRow.workspace_id == workspace_id,
        MemberRow.member_id == actor_id,
    )
    if executor.scalar(select(MemberRow.member_id).where(*member_key)) is not None:
        return False
    role_id = identity_role.casefold()
    if (
        role_id not in DEFAULT_ROLE_PERMISSIONS
        or not actor_email
        or not actor_email.strip()
        or len(actor_email) > 320
        or any(not value.strip() or len(value) > 128 for value in (tenant_id, workspace_id, actor_id))
    ):
        raise ValueError("authoritative Java membership facts are required")
    executor.execute(
        insert(WorkspaceRow)
        .values(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            seat_limit=max(int(os.environ.get("XINGJING_DEFAULT_TEAM_SEATS", "5")), 1),
            permission_version=1,
        )
        .on_conflict_do_nothing()
    )
    for key, permissions in DEFAULT_ROLE_PERMISSIONS.items():
        executor.execute(
            insert(RoleRow)
            .values(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                role_id=key,
                name=key.title(),
                permissions=sorted(permissions),
                version=1,
            )
            .on_conflict_do_nothing()
        )
    inserted = executor.scalar(
        insert(MemberRow)
        .values(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            member_id=actor_id,
            email=actor_email.strip().casefold(),
            role_id=role_id,
            active=True,
            version=1,
        )
        .on_conflict_do_nothing()
        .returning(MemberRow.member_id)
    )
    if inserted is None:
        return False
    executor.execute(
        insert(AuditRow).values(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            event_id=str(uuid4()),
            actor_id=actor_id,
            action="identity.membership_projected",
            object_type="member",
            object_id=actor_id,
            before_payload=None,
            after_payload={
                "memberId": actor_id,
                "roleId": role_id,
                "email": actor_email.strip().casefold(),
                "active": True,
            },
            request_id=request_id,
            result="success",
            occurred_at=datetime.now(UTC),
        )
    )
    return True
