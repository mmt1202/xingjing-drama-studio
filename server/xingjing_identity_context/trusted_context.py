from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from starlette.requests import Request

from lib.httpx_shared import get_http_client
from server.xingjing_identity_context.permissions import (
    PLATFORM_ADMIN_PERMISSIONS,
    permissions_for_identity_role,
)


@dataclass(frozen=True, slots=True)
class TrustedWorkspaceContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    request_id: str
    permissions: frozenset[str]
    role: str


class PlatformSessionGateway:
    """Loads the selected workspace from the Java identity service, never from client headers."""

    def __init__(self, *, base_url: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = (base_url or os.environ.get("PLATFORM_IDENTITY_URL", "")).rstrip("/")
        self._client = client

    async def get_current_context(self, *, authorization: str, request_id: str) -> Mapping[str, Any]:
        if not authorization.strip():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "UNAUTHENTICATED"})
        if not self._base_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"},
            )
        client = self._client or get_http_client()
        try:
            response = await client.get(
                f"{self._base_url}/api/v1/session/context",
                headers={"Authorization": authorization, "X-Request-Id": request_id},
            )
        except httpx.HTTPError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"},
            ) from error
        if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            raise HTTPException(status_code=response.status_code, detail={"code": "UNAUTHENTICATED"})
        if response.is_error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"},
            )
        try:
            payload = response.json()
            data = payload["data"]
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "IDENTITY_CONTEXT_INVALID"},
            ) from error
        if not isinstance(data, Mapping):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "IDENTITY_CONTEXT_INVALID"},
            )
        return data


class TrustedWorkspaceContextResolver:
    """Converts a trusted Java session context into stable Python domain permissions."""

    def __init__(self, gateway: PlatformSessionGateway) -> None:
        self._gateway = gateway

    async def __call__(self, request: Request) -> TrustedWorkspaceContext:
        authorization = request.headers.get("Authorization", "")
        request_id = (request.headers.get("X-Request-Id") or str(uuid4())).strip()
        if authorization.startswith("Bearer arc-"):
            from server.auth import _verify_api_key

            payload = await _verify_api_key(authorization.removeprefix("Bearer ").strip())
            if payload is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "UNAUTHENTICATED"},
                )
            scopes = payload.get("scopes")
            if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "IDENTITY_CONTEXT_INVALID"},
                )
            workspace_id = _required_text(payload.get("workspace_id"))
            return TrustedWorkspaceContext(
                tenant_id=workspace_id,
                workspace_id=workspace_id,
                actor_id=_required_text(payload.get("user_id")),
                request_id=request_id,
                permissions=frozenset(scopes),
                role="API_KEY",
            )
        data = await self._gateway.get_current_context(authorization=authorization, request_id=request_id)
        user = data.get("user")
        workspace = data.get("currentWorkspace")
        if not isinstance(user, Mapping) or not isinstance(workspace, Mapping):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "WORKSPACE_CONTEXT_REQUIRED"},
            )
        actor_id = _required_text(user.get("id"))
        workspace_id = _required_text(workspace.get("id"))
        role = _required_text(workspace.get("role")).upper()
        if workspace.get("status") != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "WORKSPACE_CONTEXT_REQUIRED"},
            )
        project_id = _request_project_id(request)
        permissions, effective_role = await _resolve_current_permissions(
            tenant_id=workspace_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            identity_role=role,
            project_id=project_id,
        )
        return TrustedWorkspaceContext(
            tenant_id=workspace_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            request_id=request_id,
            permissions=permissions,
            role=effective_role,
        )


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "IDENTITY_CONTEXT_INVALID"},
        )
    return value.strip()


async def _resolve_current_permissions(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    identity_role: str,
    project_id: str | None,
) -> tuple[frozenset[str], str]:
    if identity_role == "PLATFORM_ADMIN":
        return PLATFORM_ADMIN_PERMISSIONS, identity_role
    database_url = os.environ.get("XINGJING_TEAM_DATABASE_URL", "").strip()
    if not database_url:
        if os.environ.get("XINGJING_DYNAMIC_PERMISSIONS_REQUIRED", "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "PERMISSION_CONTEXT_UNAVAILABLE"},
            )
        return permissions_for_identity_role(identity_role), identity_role
    try:
        return await asyncio.to_thread(
            _permission_engine(database_url).resolve,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            project_id=project_id,
        )
    except _MembershipNotActive as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "WORKSPACE_ACCESS_DENIED"},
        ) from error
    except (ArgumentError, SQLAlchemyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PERMISSION_CONTEXT_UNAVAILABLE"},
        ) from error


class _MembershipNotActive(RuntimeError):
    pass


class _DynamicPermissionEngine:
    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        if url.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
            raise ArgumentError("XINGJING_TEAM_DATABASE_URL must be a synchronous PostgreSQL URL")
        self._engine: Engine = create_engine(url, pool_pre_ping=True)

    def resolve(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        project_id: str | None,
    ) -> tuple[frozenset[str], str]:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT member.role_id, role.permissions
                    FROM xingjing_team_members AS member
                    JOIN xingjing_team_roles AS role
                      ON role.tenant_id = member.tenant_id
                     AND role.workspace_id = member.workspace_id
                     AND role.role_id = member.role_id
                    WHERE member.tenant_id = :tenant_id
                      AND member.workspace_id = :workspace_id
                      AND member.member_id = :actor_id
                      AND member.active IS TRUE
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "actor_id": actor_id,
                },
            ).one_or_none()
        if row is None:
            raise _MembershipNotActive(actor_id)
        role_id = str(row.role_id).strip()
        raw_permissions = row.permissions
        if not isinstance(raw_permissions, list) or not all(isinstance(item, str) for item in raw_permissions):
            raise ValueError("invalid persisted role permissions")
        workspace_permissions = frozenset(raw_permissions)
        if project_id is None or role_id.casefold() in {"owner", "admin"}:
            return workspace_permissions, role_id.upper()
        with self._engine.connect() as connection:
            assignment = connection.execute(
                text(
                    """
                    SELECT permissions
                    FROM xingjing_project_members
                    WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
                      AND project_id=:project_id AND member_id=:actor_id
                      AND active IS TRUE
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "project_id": project_id,
                    "actor_id": actor_id,
                },
            ).scalar_one_or_none()
        if not isinstance(assignment, list) or not all(isinstance(item, str) for item in assignment):
            raise _MembershipNotActive(f"{actor_id}:{project_id}")
        governance_permissions = {
            permission
            for permission in workspace_permissions
            if permission.startswith("workspace.")
        }
        return frozenset(assignment) | governance_permissions, role_id.upper()


@lru_cache(maxsize=4)
def _permission_engine(database_url: str) -> _DynamicPermissionEngine:
    return _DynamicPermissionEngine(database_url)


def _request_project_id(request: Request) -> str | None:
    path_value = request.path_params.get("project_id") or request.path_params.get("projectId")
    header_value = request.headers.get("X-Project-Id")
    path_project = path_value.strip() if isinstance(path_value, str) and path_value.strip() else None
    header_project = header_value.strip() if header_value and header_value.strip() else None
    if path_project and header_project and path_project != header_project:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PROJECT_SCOPE_MISMATCH"},
        )
    return path_project or header_project
