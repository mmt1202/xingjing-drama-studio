from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from .models import (
    Actor,
    AuditEvent,
    EnterpriseProfile,
    EnterpriseProfileUpdate,
    Invitation,
    Role,
    RoleUpdate,
    SeatUsage,
)


class TeamRepository(Protocol):
    """持久化端口；所有命令方法必须在单个原子事务内完成状态、版本、幂等与审计写入。"""

    def create_workspace(self, workspace_id: str, *, seat_limit: int, owner: Actor) -> None: ...
    def permission_version(self, workspace_id: str) -> int: ...
    def permissions_for(self, workspace_id: str, member_id: str) -> frozenset[str] | None: ...
    def create_invitation(
        self,
        *,
        workspace_id: str,
        email: str,
        role_id: str,
        expires_at: datetime,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> Invitation: ...
    def invitation(self, invitation_id: str, *, now: datetime) -> Invitation: ...
    def accept_invitation(
        self,
        *,
        invitation_id: str,
        actor: Actor,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> Invitation: ...
    def revoke_invitation(
        self,
        *,
        invitation_id: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> Invitation: ...
    def seat_usage(self, workspace_id: str) -> SeatUsage: ...
    def update_role(
        self,
        *,
        workspace_id: str,
        role_id: str,
        update: RoleUpdate,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> Role: ...
    def add_member(self, workspace_id: str, member_id: str, email: str, role_id: str) -> None: ...
    def update_enterprise_profile(
        self,
        *,
        workspace_id: str,
        update: EnterpriseProfileUpdate,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> EnterpriseProfile: ...
    def enterprise_profile(self, workspace_id: str) -> EnterpriseProfile | None: ...
    def audit_events(
        self,
        workspace_id: str,
        *,
        request_id: str | None = None,
        search: str | None = None,
        actor_id: str | None = None,
        action: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 51,
    ) -> list[AuditEvent]: ...


RepositoryPath = str | Path
