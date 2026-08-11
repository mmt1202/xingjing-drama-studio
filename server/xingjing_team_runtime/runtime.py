"""M10 团队治理的同步 PostgreSQL 生产组合。

本模块不挂载 HTTP 路由、不创建 schema，也不会初始化工作区、成员或角色。
每次调用都以 Java 平台会话解析的租户、工作区、操作者和权限构造新的、严格
工作区范围的 ``SqlAlchemyTeamRepository``。
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import Request
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_billing_persistence.models import TeamPlanRow
from server.xingjing_identity_context import PlatformSessionGateway, TrustedWorkspaceContext
from server.xingjing_identity_context.permissions import DEFAULT_ROLE_PERMISSIONS
from server.xingjing_platform_persistence.persistence import ProjectRow, TaskRow
from server.xingjing_team import (
    Actor,
    AuditEvent,
    EnterpriseProfile,
    EnterpriseProfileUpdate,
    Invitation,
    InvitationState,
    NotFound,
    Role,
    RoleUpdate,
    SeatUsage,
    TeamService,
)
from server.xingjing_team_persistence import SqlAlchemyTeamRepository, TeamPersistenceScope
from server.xingjing_team_persistence.repository import InvitationRow, MemberRow, RoleRow, WorkspaceRow

from .project_access import ProjectAccessAssignment, ProjectAccessRepository

type SessionFactory = Callable[[], Session]
type TeamContextResolver = Callable[
    [Request], TeamTrustedContext | TrustedWorkspaceContext | Awaitable[TeamTrustedContext | TrustedWorkspaceContext]
]

_TEAM_PERMISSIONS = frozenset(
    {
        "workspace.member.view",
        "workspace.member.manage",
        "workspace.enterprise.view",
        "workspace.enterprise.manage",
        "workspace.audit.view",
        "workspace.audit.manage",
        "workspace.view",
        "workspace.manage",
    }
)


class TeamRuntimeConfigurationError(RuntimeError):
    """生产组合缺少必需配置或依赖时的稳定失败信号。"""


class TeamRuntimePermissionDenied(PermissionError):
    """可信会话未授予当前 M10 动作所需权限。"""


@dataclass(frozen=True, slots=True)
class TeamTrustedContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    actor_email: str | None
    request_id: str
    role: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class TeamRequestContext:
    """已解析的可信调用者；不能从 HTTP 请求体或身份头构造。"""

    trusted: TeamTrustedContext

    @property
    def actor(self) -> Actor:
        if not self.trusted.actor_email:
            raise TeamRuntimeConfigurationError("TEAM_TRUSTED_ACTOR_EMAIL_REQUIRED")
        return Actor(id=self.trusted.actor_id, email=self.trusted.actor_email)

    @property
    def scope(self) -> TeamPersistenceScope:
        return TeamPersistenceScope(self.trusted.tenant_id, self.trusted.workspace_id)


@dataclass(frozen=True, slots=True)
class TeamIdentityActor:
    actor_id: str
    email: str
    request_id: str


@dataclass(frozen=True, slots=True)
class TeamMember:
    member_id: str
    email: str
    role_id: str
    active: bool


class JavaTeamTrustedContextResolver:
    """直接读取 Java 会话返回的用户邮箱与工作区角色，拒绝客户端伪造字段。"""

    def __init__(self, gateway: PlatformSessionGateway) -> None:
        self._gateway = gateway

    async def __call__(self, request: Request) -> TeamTrustedContext:
        authorization = request.headers.get("Authorization", "")
        request_id = (request.headers.get("X-Request-Id") or str(uuid4())).strip()
        data = await self._gateway.get_current_context(authorization=authorization, request_id=request_id)
        user = _mapping(data.get("user"))
        workspace = _mapping(data.get("currentWorkspace"))
        if user is None or workspace is None or workspace.get("status") != "ACTIVE":
            raise TeamRuntimeConfigurationError("TEAM_TRUSTED_WORKSPACE_CONTEXT_REQUIRED")
        role = _text(workspace.get("role"), "TEAM_TRUSTED_ROLE_REQUIRED").upper()
        raw_permissions = data.get("permissions")
        permissions = _permissions(raw_permissions) if raw_permissions is not None else _permissions_for_role(role)
        return TeamTrustedContext(
            tenant_id=_text(
                data.get("tenantId", workspace.get("tenantId", workspace.get("id"))), "TEAM_TRUSTED_TENANT_REQUIRED"
            ),
            workspace_id=_text(workspace.get("id"), "TEAM_TRUSTED_WORKSPACE_REQUIRED"),
            actor_id=_text(user.get("id"), "TEAM_TRUSTED_ACTOR_REQUIRED"),
            actor_email=_optional_email(user.get("email")),
            request_id=request_id,
            role=role,
            permissions=permissions,
        )

    async def resolve_identity(self, request: Request) -> TeamIdentityActor:
        authorization = request.headers.get("Authorization", "")
        request_id = (request.headers.get("X-Request-Id") or str(uuid4())).strip()
        data = await self._gateway.get_current_context(authorization=authorization, request_id=request_id)
        user = _mapping(data.get("user"))
        if user is None:
            raise TeamRuntimeConfigurationError("TEAM_TRUSTED_ACTOR_REQUIRED")
        return TeamIdentityActor(
            actor_id=_text(user.get("id"), "TEAM_TRUSTED_ACTOR_REQUIRED"),
            email=_text(user.get("email"), "TEAM_TRUSTED_ACTOR_EMAIL_REQUIRED").casefold(),
            request_id=request_id,
        )


class TeamRuntime:
    """M10 的未来 HTTP 调用边界；所有写入均委托已迁移的持久化适配器。"""

    def __init__(
        self, *, session_factory: SessionFactory, context_resolver: TeamContextResolver, engine: Engine
    ) -> None:
        self._session_factory = session_factory
        self._context_resolver = context_resolver
        self._engine: Engine | None = engine

    async def members(self, request: Request, *, context: TeamRequestContext | None = None) -> tuple[TeamMember, ...]:
        context = await self._request_context(request, "workspace.member.view", context)
        return await asyncio.to_thread(self._members_sync, context)

    async def seat_usage(self, request: Request, *, context: TeamRequestContext | None = None) -> SeatUsage:
        context = await self._request_context(request, "workspace.member.view", context)
        return await asyncio.to_thread(self._service(context).seat_usage, context.trusted.workspace_id, context.actor)

    async def roles(self, request: Request, *, context: TeamRequestContext | None = None) -> tuple[Role, ...]:
        context = await self._request_context(request, "workspace.member.view", context)
        return await asyncio.to_thread(self._roles_sync, context)

    async def preview_role_impact(
        self,
        request: Request,
        *,
        role_id: str,
        permissions: frozenset[str],
        context: TeamRequestContext | None = None,
    ) -> dict[str, object]:
        context = await self._request_context(request, "workspace.member.manage", context)
        return await asyncio.to_thread(
            self._preview_role_impact_sync,
            context,
            role_id,
            permissions,
        )

    async def invitations(
        self, request: Request, *, context: TeamRequestContext | None = None
    ) -> tuple[Invitation, ...]:
        context = await self._request_context(request, "workspace.member.view", context)
        return await asyncio.to_thread(self._invitations_sync, context)

    async def overview(self, request: Request, *, context: TeamRequestContext | None = None) -> dict[str, object]:
        context = await self._request_context(request, "workspace.view", context)
        return await asyncio.to_thread(self._overview_sync, context)

    async def project_members(
        self,
        request: Request,
        *,
        project_id: str,
        query: str | None,
        page_size: int,
        page_token: str | None,
        context: TeamRequestContext | None = None,
    ) -> tuple[tuple[ProjectAccessAssignment, ...], str | None]:
        context = await self._request_context(request, "workspace.member.view", context)
        return await asyncio.to_thread(
            ProjectAccessRepository(self._session_factory, context.scope).list_members,
            project_id,
            query=query,
            page_size=page_size,
            page_token=page_token,
        )

    async def save_project_member(
        self,
        request: Request,
        *,
        project_id: str,
        member_id: str,
        production_role: str,
        data_scope: tuple[str, ...],
        permissions: tuple[str, ...],
        active: bool,
        expected_version: int,
        idempotency_key: str,
        context: TeamRequestContext | None = None,
    ) -> ProjectAccessAssignment:
        context = await self._request_context(request, "workspace.member.manage", context)
        return await asyncio.to_thread(
            ProjectAccessRepository(self._session_factory, context.scope).save_member,
            project_id,
            member_id=member_id,
            production_role=production_role,
            data_scope=data_scope,
            permissions=permissions,
            active=active,
            expected_version=expected_version,
            actor_id=context.trusted.actor_id,
            request_id=context.trusted.request_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )

    async def enterprise_profile(
        self, request: Request, *, context: TeamRequestContext | None = None
    ) -> EnterpriseProfile | None:
        context = await self._request_context(request, "workspace.enterprise.view", context)
        return await asyncio.to_thread(
            self._service(context).enterprise_profile, context.trusted.workspace_id, context.actor
        )

    async def audit_events(
        self,
        request: Request,
        *,
        request_id: str | None = None,
        search: str | None = None,
        actor_id: str | None = None,
        action: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        page_size: int = 50,
        page_token: str | None = None,
        context: TeamRequestContext | None = None,
    ) -> tuple[tuple[AuditEvent, ...], str | None]:
        context = await self._request_context(request, "workspace.audit.view", context)
        bounded_size = max(1, min(page_size, 100))
        rows = tuple(
            await asyncio.to_thread(
                self._service(context).audit_events,
                context.trusted.workspace_id,
                context.actor,
                request_id=_optional_filter(request_id, 255),
                search=_optional_filter(search, 160),
                actor_id=_optional_filter(actor_id, 128),
                action=_optional_filter(action, 64),
                object_type=_optional_filter(object_type, 64),
                object_id=_optional_filter(object_id, 128),
                cursor=_decode_audit_cursor(page_token),
                limit=bounded_size + 1,
            )
        )
        has_more = len(rows) > bounded_size
        items = rows[:bounded_size]
        return items, _encode_audit_cursor(items[-1]) if has_more and items else None

    async def create_invitation(
        self,
        request: Request,
        *,
        email: str,
        role_id: str,
        expires_at: datetime,
        idempotency_key: str,
        context: TeamRequestContext | None = None,
    ) -> Invitation:
        context = await self._request_context(request, "workspace.member.manage", context)
        return await asyncio.to_thread(
            self._service(context).invite_member,
            context.trusted.workspace_id,
            context.actor,
            email=email,
            role_id=role_id,
            expires_at=expires_at,
            request_id=context.trusted.request_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )

    async def revoke_invitation(
        self, request: Request, *, invitation_id: str, idempotency_key: str, context: TeamRequestContext | None = None
    ) -> Invitation:
        context = await self._request_context(request, "workspace.member.manage", context)
        return await asyncio.to_thread(
            self._service(context).revoke_invitation,
            invitation_id,
            context.actor,
            request_id=context.trusted.request_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )

    async def accept_invitation(
        self, request: Request, *, invitation_id: str, idempotency_key: str, context: TeamRequestContext | None = None
    ) -> Invitation:
        context = await self._request_context(request, None, context)
        return await asyncio.to_thread(
            self._service(context).accept_invitation,
            invitation_id,
            context.actor,
            request_id=context.trusted.request_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )

    async def accept_invitation_from_identity(
        self, request: Request, *, invitation_id: str, idempotency_key: str
    ) -> Invitation:
        resolver = self._context_resolver
        if not isinstance(resolver, JavaTeamTrustedContextResolver):
            raise TeamRuntimeConfigurationError("TEAM_IDENTITY_ACCEPTANCE_RESOLVER_REQUIRED")
        identity = await resolver.resolve_identity(request)
        invitation = await asyncio.to_thread(self._invitation_for_recipient_sync, invitation_id, identity.email)
        context = TeamRequestContext(
            TeamTrustedContext(
                tenant_id=invitation.workspace_id,
                workspace_id=invitation.workspace_id,
                actor_id=identity.actor_id,
                actor_email=identity.email,
                request_id=identity.request_id,
                role=invitation.role_id.upper(),
                permissions=frozenset(),
            )
        )
        return await self.accept_invitation(
            request,
            invitation_id=invitation_id,
            idempotency_key=idempotency_key,
            context=context,
        )

    async def update_role(
        self,
        request: Request,
        *,
        role_id: str,
        update: RoleUpdate,
        expected_version: int,
        idempotency_key: str,
        context: TeamRequestContext | None = None,
    ) -> Role:
        context = await self._request_context(request, "workspace.member.manage", context)
        return await asyncio.to_thread(
            self._service(context).update_role,
            context.trusted.workspace_id,
            context.actor,
            role_id,
            update,
            expected_version=expected_version,
            request_id=context.trusted.request_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )

    async def update_enterprise_profile(
        self,
        request: Request,
        *,
        update: EnterpriseProfileUpdate,
        expected_version: int,
        idempotency_key: str,
        context: TeamRequestContext | None = None,
    ) -> EnterpriseProfile:
        context = await self._request_context(request, "workspace.enterprise.manage", context)
        return await asyncio.to_thread(
            self._service(context).update_enterprise_profile,
            context.trusted.workspace_id,
            context.actor,
            update,
            expected_version=expected_version,
            request_id=context.trusted.request_id,
            idempotency_key=_idempotency_key(idempotency_key),
        )

    async def resolve_context(self, request: Request, required_permission: str | None) -> TeamRequestContext:
        resolved = self._context_resolver(request)
        trusted = await resolved if inspect.isawaitable(resolved) else resolved
        context = TeamRequestContext(_as_team_context(trusted))
        await asyncio.to_thread(self._ensure_context_provisioned_sync, context)
        current_permissions = await asyncio.to_thread(
            self._permissions_for_sync,
            context,
        )
        if current_permissions is None:
            raise TeamRuntimePermissionDenied(required_permission or "workspace.view")
        context = TeamRequestContext(replace(context.trusted, permissions=current_permissions))
        if required_permission is not None:
            if required_permission not in _TEAM_PERMISSIONS:
                raise ValueError("unsupported M10 permission")
            if required_permission not in context.trusted.permissions:
                raise TeamRuntimePermissionDenied(required_permission)
        return context

    def _permissions_for_sync(self, context: TeamRequestContext) -> frozenset[str] | None:
        return SqlAlchemyTeamRepository(
            self._session_factory,
            scope=context.scope,
        ).permissions_for(context.trusted.workspace_id, context.trusted.actor_id)

    def _ensure_context_provisioned_sync(self, context: TeamRequestContext) -> None:
        """Materialize the authoritative Java workspace membership in the team domain.

        The Java identity database remains authoritative.  This projection is
        idempotent and never trusts client-supplied tenant, workspace, actor or
        role values.
        """
        role_permissions = {role_id: sorted(permissions) for role_id, permissions in DEFAULT_ROLE_PERMISSIONS.items()}
        current_role = context.trusted.role.casefold()
        if current_role not in role_permissions:
            current_role = "member"
        try:
            with self._session_factory() as session, session.begin():
                session.execute(
                    pg_insert(WorkspaceRow)
                    .values(
                        tenant_id=context.trusted.tenant_id,
                        workspace_id=context.trusted.workspace_id,
                        seat_limit=max(int(os.environ.get("XINGJING_DEFAULT_TEAM_SEATS", "5")), 1),
                        permission_version=1,
                    )
                    .on_conflict_do_nothing()
                )
                for role_id, permissions in role_permissions.items():
                    session.execute(
                        pg_insert(RoleRow)
                        .values(
                            tenant_id=context.trusted.tenant_id,
                            workspace_id=context.trusted.workspace_id,
                            role_id=role_id,
                            name=role_id.title(),
                            permissions=permissions,
                            version=1,
                        )
                        .on_conflict_do_nothing()
                    )
                if context.trusted.actor_email:
                    session.execute(
                        pg_insert(MemberRow)
                        .values(
                            tenant_id=context.trusted.tenant_id,
                            workspace_id=context.trusted.workspace_id,
                            member_id=context.trusted.actor_id,
                            email=context.trusted.actor_email,
                            role_id=current_role,
                            active=True,
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                MemberRow.tenant_id,
                                MemberRow.workspace_id,
                                MemberRow.member_id,
                            ],
                            set_={
                                "email": context.trusted.actor_email,
                                "role_id": current_role,
                                "active": True,
                            },
                        )
                    )
        except (SQLAlchemyError, ValueError) as error:
            raise TeamRuntimeConfigurationError("TEAM_IDENTITY_PROJECTION_UNAVAILABLE") from error

    async def _request_context(
        self, request: Request, required_permission: str | None, context: TeamRequestContext | None
    ) -> TeamRequestContext:
        if context is None:
            return await self.resolve_context(request, required_permission)
        if required_permission is not None and required_permission not in context.trusted.permissions:
            raise TeamRuntimePermissionDenied(required_permission)
        return context

    async def close(self) -> None:
        engine = self._engine
        if engine is not None:
            self._engine = None
            await asyncio.to_thread(engine.dispose)

    def _members_sync(self, context: TeamRequestContext) -> tuple[TeamMember, ...]:
        try:
            with self._session_factory() as session:
                rows = session.scalars(
                    select(MemberRow)
                    .where(
                        MemberRow.tenant_id == context.trusted.tenant_id,
                        MemberRow.workspace_id == context.trusted.workspace_id,
                    )
                    .order_by(MemberRow.member_id)
                ).all()
                return tuple(TeamMember(row.member_id, row.email, row.role_id, row.active) for row in rows)
        except SQLAlchemyError as error:
            raise TeamRuntimeConfigurationError("TEAM_MEMBER_READ_UNAVAILABLE") from error

    def _roles_sync(self, context: TeamRequestContext) -> tuple[Role, ...]:
        try:
            with self._session_factory() as session:
                rows = session.scalars(
                    select(RoleRow)
                    .where(
                        RoleRow.tenant_id == context.trusted.tenant_id,
                        RoleRow.workspace_id == context.trusted.workspace_id,
                    )
                    .order_by(RoleRow.role_id)
                ).all()
                return tuple(
                    Role(row.role_id, row.workspace_id, row.name, frozenset(row.permissions), row.version)
                    for row in rows
                )
        except SQLAlchemyError as error:
            raise TeamRuntimeConfigurationError("TEAM_ROLE_READ_UNAVAILABLE") from error

    def _preview_role_impact_sync(
        self,
        context: TeamRequestContext,
        role_id: str,
        proposed_permissions: frozenset[str],
    ) -> dict[str, object]:
        try:
            with self._session_factory() as session:
                role = session.scalar(
                    select(RoleRow).where(
                        RoleRow.tenant_id == context.trusted.tenant_id,
                        RoleRow.workspace_id == context.trusted.workspace_id,
                        RoleRow.role_id == role_id,
                    )
                )
                if role is None:
                    raise NotFound("role not found")
                current_permissions = frozenset(role.permissions)
                active_members = session.scalar(
                    select(func.count())
                    .select_from(MemberRow)
                    .where(
                        MemberRow.tenant_id == context.trusted.tenant_id,
                        MemberRow.workspace_id == context.trusted.workspace_id,
                        MemberRow.role_id == role_id,
                        MemberRow.active.is_(True),
                    )
                ) or 0
                project_assignments = session.execute(
                    text(
                        """
                        SELECT count(*) FROM xingjing_project_members AS project_member
                        JOIN xingjing_team_members AS member
                          ON member.tenant_id = project_member.tenant_id
                         AND member.workspace_id = project_member.workspace_id
                         AND member.member_id = project_member.member_id
                        WHERE project_member.tenant_id = :tenant_id
                          AND project_member.workspace_id = :workspace_id
                          AND project_member.active = true
                          AND member.active = true
                          AND member.role_id = :role_id
                        """
                    ),
                    {
                        "tenant_id": context.trusted.tenant_id,
                        "workspace_id": context.trusted.workspace_id,
                        "role_id": role_id,
                    },
                ).scalar_one()
                return {
                    "role_id": role.role_id,
                    "role_name": role.name,
                    "current_version": role.version,
                    "affected_active_members": int(active_members),
                    "project_assignments": int(project_assignments),
                    "added_permissions": sorted(proposed_permissions - current_permissions),
                    "removed_permissions": sorted(current_permissions - proposed_permissions),
                    "unchanged": proposed_permissions == current_permissions,
                }
        except SQLAlchemyError as error:
            raise TeamRuntimeConfigurationError("TEAM_ROLE_IMPACT_PREVIEW_UNAVAILABLE") from error

    def _invitations_sync(self, context: TeamRequestContext) -> tuple[Invitation, ...]:
        try:
            with self._session_factory() as session:
                rows = session.scalars(
                    select(InvitationRow)
                    .where(
                        InvitationRow.tenant_id == context.trusted.tenant_id,
                        InvitationRow.workspace_id == context.trusted.workspace_id,
                    )
                    .order_by(InvitationRow.expires_at.desc(), InvitationRow.invitation_id)
                ).all()
                return tuple(
                    Invitation(
                        id=row.invitation_id,
                        workspace_id=row.workspace_id,
                        email=row.email,
                        role_id=row.role_id,
                        state=InvitationState(row.state),
                        expires_at=row.expires_at,
                        invited_by=row.invited_by,
                        accepted_by=row.accepted_by,
                        version=row.version,
                    )
                    for row in rows
                )
        except SQLAlchemyError as error:
            raise TeamRuntimeConfigurationError("TEAM_INVITATION_READ_UNAVAILABLE") from error

    def _invitation_for_recipient_sync(self, invitation_id: str, email: str) -> Invitation:
        try:
            with self._session_factory() as session:
                row = session.scalar(
                    select(InvitationRow).where(
                        InvitationRow.invitation_id == invitation_id,
                        InvitationRow.email == email,
                    )
                )
                if row is None:
                    raise TeamRuntimePermissionDenied("invitation recipient mismatch")
                return Invitation(
                    id=row.invitation_id,
                    workspace_id=row.workspace_id,
                    email=row.email,
                    role_id=row.role_id,
                    state=InvitationState(row.state),
                    expires_at=row.expires_at,
                    invited_by=row.invited_by,
                    accepted_by=row.accepted_by,
                    version=row.version,
                )
        except SQLAlchemyError as error:
            raise TeamRuntimeConfigurationError("TEAM_INVITATION_READ_UNAVAILABLE") from error

    def _overview_sync(self, context: TeamRequestContext) -> dict[str, object]:
        try:
            with self._session_factory() as session:
                workspace = session.scalar(
                    select(WorkspaceRow).where(
                        WorkspaceRow.tenant_id == context.trusted.tenant_id,
                        WorkspaceRow.workspace_id == context.trusted.workspace_id,
                    )
                )
                if workspace is None:
                    raise TeamRuntimeConfigurationError("TEAM_WORKSPACE_NOT_PROVISIONED")
                members = session.scalars(
                    select(MemberRow).where(
                        MemberRow.tenant_id == context.trusted.tenant_id,
                        MemberRow.workspace_id == context.trusted.workspace_id,
                    )
                ).all()
                roles = session.scalars(
                    select(RoleRow).where(
                        RoleRow.tenant_id == context.trusted.tenant_id,
                        RoleRow.workspace_id == context.trusted.workspace_id,
                    )
                ).all()
                project_count = session.scalar(
                    select(func.count())
                    .select_from(ProjectRow)
                    .where(
                        ProjectRow.tenant_id == context.trusted.tenant_id,
                        ProjectRow.workspace_id == context.trusted.workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                ) or 0
                task_counts = {
                    str(status): int(count)
                    for status, count in session.execute(
                        select(TaskRow.status, func.count())
                        .where(
                            TaskRow.tenant_id == context.trusted.tenant_id,
                            TaskRow.workspace_id == context.trusted.workspace_id,
                        )
                        .group_by(TaskRow.status)
                    ).all()
                }
                plan = session.get(
                    TeamPlanRow,
                    (context.trusted.tenant_id, context.trusted.workspace_id),
                )
                latest_project_update = session.scalar(
                    select(func.max(ProjectRow.updated_at)).where(
                        ProjectRow.tenant_id == context.trusted.tenant_id,
                        ProjectRow.workspace_id == context.trusted.workspace_id,
                    )
                )
                latest_task_update = session.scalar(
                    select(func.max(TaskRow.updated_at)).where(
                        TaskRow.tenant_id == context.trusted.tenant_id,
                        TaskRow.workspace_id == context.trusted.workspace_id,
                    )
                )
                updated_candidates = [
                    value
                    for value in (latest_project_update, latest_task_update, plan.updated_at if plan else None)
                    if value is not None
                ]
                active = sum(1 for member in members if member.active)
                return {
                    "workspace_id": workspace.workspace_id,
                    "seat_limit": workspace.seat_limit,
                    "occupied_seats": active,
                    "available_seats": max(workspace.seat_limit - active, 0),
                    "member_count": len(members),
                    "active_member_count": active,
                    "role_count": len(roles),
                    "permission_version": workspace.permission_version,
                    "project_count": int(project_count),
                    "task_count": sum(task_counts.values()),
                    "running_task_count": task_counts.get("running", 0),
                    "failed_task_count": task_counts.get("failed", 0),
                    "plan_id": plan.plan_id if plan else None,
                    "plan_status": plan.status if plan else "not_configured",
                    "quota_remaining": dict(plan.quota_remaining) if plan else {},
                    "updated_at": max(updated_candidates).isoformat() if updated_candidates else None,
                }
        except SQLAlchemyError as error:
            raise TeamRuntimeConfigurationError("TEAM_OVERVIEW_READ_UNAVAILABLE") from error

    def _service(self, context: TeamRequestContext) -> TeamService:
        return TeamService(
            SqlAlchemyTeamRepository(
                self._session_factory,
                scope=context.scope,
                project_identity_membership=True,
            )
        )


def create_production_team_runtime(
    *, database_url: str | None = None, context_resolver: TeamContextResolver | None = None
) -> TeamRuntime:
    """创建仅接受同步 PostgreSQL 的 M10 生产运行时。"""
    url = (database_url or os.environ.get("XINGJING_TEAM_DATABASE_URL", "")).strip()
    if not url:
        raise TeamRuntimeConfigurationError("XINGJING_TEAM_DATABASE_URL_REQUIRED")
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError) as error:
        raise TeamRuntimeConfigurationError("XINGJING_TEAM_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC") from error
    if parsed.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise TeamRuntimeConfigurationError("XINGJING_TEAM_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    try:
        engine = create_engine(url, pool_pre_ping=True)
    except (ModuleNotFoundError, SQLAlchemyError, ValueError) as error:
        raise TeamRuntimeConfigurationError("TEAM_RUNTIME_DATABASE_UNAVAILABLE") from error
    return TeamRuntime(
        session_factory=sessionmaker(engine, expire_on_commit=False),
        context_resolver=context_resolver or JavaTeamTrustedContextResolver(PlatformSessionGateway()),
        engine=engine,
    )


def _as_team_context(value: TeamTrustedContext | TrustedWorkspaceContext) -> TeamTrustedContext:
    if isinstance(value, TeamTrustedContext):
        return value
    return TeamTrustedContext(
        tenant_id=value.tenant_id,
        workspace_id=value.workspace_id,
        actor_id=value.actor_id,
        actor_email=None,
        request_id=value.request_id,
        role=value.role.upper(),
        permissions=value.permissions,
    )


def _permissions_for_role(role: str) -> frozenset[str]:
    if role in {"OWNER", "ADMIN", "PLATFORM_ADMIN"}:
        return _TEAM_PERMISSIONS
    return frozenset()


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TeamRuntimeConfigurationError(code)
    return value.strip()


def _optional_email(value: object) -> str | None:
    return value.strip().casefold() if isinstance(value, str) and value.strip() else None


def _permissions(value: object) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise TeamRuntimeConfigurationError("TEAM_TRUSTED_PERMISSIONS_INVALID")
    return frozenset(item.strip() for item in value)


def _idempotency_key(value: str) -> str:
    if not value.strip():
        raise TeamRuntimeConfigurationError("IDEMPOTENCY_KEY_REQUIRED")
    return value.strip()


def _optional_filter(value: str | None, max_length: int) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ValueError("INVALID_AUDIT_FILTER")
    return normalized


def _decode_audit_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None or not value.strip():
        return None
    try:
        padded = value.strip() + "=" * (-len(value.strip()) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        occurred_at, event_id = raw.split("|", 1)
        parsed = datetime.fromisoformat(occurred_at)
        if parsed.tzinfo is None or not event_id:
            raise ValueError
        return parsed, event_id
    except (UnicodeError, ValueError) as error:
        raise ValueError("INVALID_PAGE_TOKEN") from error


def _encode_audit_cursor(event: AuditEvent) -> str:
    raw = f"{event.occurred_at.isoformat()}|{event.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
