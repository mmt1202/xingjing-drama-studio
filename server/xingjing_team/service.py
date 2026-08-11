from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .errors import Forbidden
from .models import (
    Actor,
    Authorization,
    EnterpriseProfile,
    EnterpriseProfileUpdate,
    Invitation,
    Role,
    RoleUpdate,
    SeatUsage,
)
from .ports import TeamRepository


class TeamService:
    def __init__(self, repository: TeamRepository, *, clock: Callable[[], datetime] | None = None):
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def _require(self, workspace_id: str, actor: Actor, permission: str) -> None:
        permissions = self._repository.permissions_for(workspace_id, actor.id)
        if permissions is None or permission not in permissions:
            raise Forbidden(permission)

    def authorize(self, workspace_id: str, member_id: str, permission: str) -> Authorization:
        permissions = self._repository.permissions_for(workspace_id, member_id)
        if permissions is None or permission not in permissions:
            raise Forbidden(permission)
        return Authorization(workspace_id, member_id, permission, self._repository.permission_version(workspace_id))

    def invite_member(
        self,
        workspace_id: str,
        actor: Actor,
        *,
        email: str,
        role_id: str,
        expires_at: datetime,
        request_id: str,
        idempotency_key: str,
    ) -> Invitation:
        self._require(workspace_id, actor, "workspace.member.manage")
        return self._repository.create_invitation(
            workspace_id=workspace_id,
            email=email,
            role_id=role_id,
            expires_at=expires_at,
            actor_id=actor.id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )

    def get_invitation(self, invitation_id: str, actor: Actor) -> Invitation:
        invitation = self._repository.invitation(invitation_id, now=self._clock())
        self._require(invitation.workspace_id, actor, "workspace.member.view")
        return invitation

    def accept_invitation(
        self, invitation_id: str, actor: Actor, *, request_id: str, idempotency_key: str
    ) -> Invitation:
        return self._repository.accept_invitation(
            invitation_id=invitation_id,
            actor=actor,
            request_id=request_id,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )

    def revoke_invitation(
        self, invitation_id: str, actor: Actor, *, request_id: str, idempotency_key: str
    ) -> Invitation:
        invitation = self._repository.invitation(invitation_id, now=self._clock())
        self._require(invitation.workspace_id, actor, "workspace.member.manage")
        return self._repository.revoke_invitation(
            invitation_id=invitation_id,
            actor_id=actor.id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )

    def seat_usage(self, workspace_id: str, actor: Actor) -> SeatUsage:
        self._require(workspace_id, actor, "workspace.member.view")
        return self._repository.seat_usage(workspace_id)

    def update_role(
        self,
        workspace_id: str,
        actor: Actor,
        role_id: str,
        update: RoleUpdate,
        *,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> Role:
        self._require(workspace_id, actor, "workspace.member.manage")
        return self._repository.update_role(
            workspace_id=workspace_id,
            role_id=role_id,
            update=update,
            expected_version=expected_version,
            actor_id=actor.id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )

    def add_member_for_provisioning(self, workspace_id: str, member_id: str, email: str, role_id: str) -> None:
        """主线身份同步边界：仅供受信任的 provisioning adapter 调用。"""
        self._repository.add_member(workspace_id, member_id, email, role_id)

    def update_enterprise_profile(
        self,
        workspace_id: str,
        actor: Actor,
        update: EnterpriseProfileUpdate,
        *,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> EnterpriseProfile:
        self._require(workspace_id, actor, "workspace.enterprise.manage")
        return self._repository.update_enterprise_profile(
            workspace_id=workspace_id,
            update=update,
            expected_version=expected_version,
            actor_id=actor.id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )

    def enterprise_profile(self, workspace_id: str, actor: Actor) -> EnterpriseProfile | None:
        self._require(workspace_id, actor, "workspace.enterprise.view")
        return self._repository.enterprise_profile(workspace_id)

    def audit_events(
        self,
        workspace_id: str,
        actor: Actor,
        *,
        request_id: str | None = None,
        search: str | None = None,
        actor_id: str | None = None,
        action: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 51,
    ):
        self._require(workspace_id, actor, "workspace.audit.view")
        return self._repository.audit_events(
            workspace_id,
            request_id=request_id,
            search=search,
            actor_id=actor_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            cursor=cursor,
            limit=limit,
        )
