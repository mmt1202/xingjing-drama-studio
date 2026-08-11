"""可信会话下的 M10 团队、成员与企业资料 HTTP 契约。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from server.xingjing_team.errors import (
    Forbidden,
    IdempotencyConflict,
    InvitationStateError,
    NotFound,
    SeatLimitReached,
    VersionConflict,
)
from server.xingjing_team.models import EnterpriseProfileUpdate, RoleUpdate
from server.xingjing_team_runtime import TeamRuntime, TeamRuntimeConfigurationError, TeamRuntimePermissionDenied


class TeamHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class TeamContractRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> JSONResponse:
            try:
                return cast(JSONResponse, await original(request))
            except TeamHttpError as error:
                return _error(request, error.code, error.status_code)
            except TeamRuntimeConfigurationError as error:
                return _error(request, str(error) or "TEAM_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
            except (TeamRuntimePermissionDenied, Forbidden, PermissionError):
                return _error(request, "PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)
            except NotFound:
                return _error(request, "TEAM_RESOURCE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except (IdempotencyConflict, InvitationStateError, SeatLimitReached, VersionConflict):
                return _error(request, "TEAM_VERSION_OR_STATE_CONFLICT", status.HTTP_409_CONFLICT)
            except HTTPException as error:
                return _error(request, "REQUEST_REJECTED", error.status_code)
            except ValueError as error:
                return _error(request, str(error) or "INVALID_TEAM_REQUEST", status.HTTP_400_BAD_REQUEST)

        return handler


def create_team_router(runtime: TeamRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces", tags=["xingjing-team"], route_class=TeamContractRoute)

    @router.get("/{workspace_id}/members")
    async def members(request: Request, workspace_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.view")
        rows = await runtime.members(request, context=context)
        return _success(context.trusted.request_id, {"members": jsonable_encoder(rows)})

    @router.get("/{workspace_id}/roles")
    async def roles(request: Request, workspace_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.view")
        rows = await runtime.roles(request, context=context)
        return _success(context.trusted.request_id, {"roles": jsonable_encoder(rows)})

    @router.post("/{workspace_id}/roles/{role_id}/impact-preview")
    async def preview_role_impact(request: Request, workspace_id: str, role_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.manage")
        body = await _json_object(request)
        impact = await runtime.preview_role_impact(
            request,
            role_id=role_id,
            permissions=frozenset(_string_list(body, "permissions")),
            context=context,
        )
        return _success(context.trusted.request_id, {"impact": jsonable_encoder(impact)})

    @router.get("/{workspace_id}/invitations")
    async def invitations(request: Request, workspace_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.view")
        rows = await runtime.invitations(request, context=context)
        return _success(context.trusted.request_id, {"invitations": jsonable_encoder(rows)})

    @router.put("/{workspace_id}/roles/{role_id}")
    async def update_role(request: Request, workspace_id: str, role_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.manage")
        body = await _json_object(request)
        role = await runtime.update_role(
            request,
            role_id=role_id,
            update=RoleUpdate(name=_text(body, "name"), permissions=frozenset(_string_list(body, "permissions"))),
            expected_version=_expected_version(request, body),
            idempotency_key=_idempotency_key(request),
            context=context,
        )
        return _success(context.trusted.request_id, {"role": jsonable_encoder(role)})

    @router.get("/{workspace_id}/enterprise-profile")
    async def enterprise_profile(request: Request, workspace_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.enterprise.view")
        profile = await runtime.enterprise_profile(request, context=context)
        return _success(context.trusted.request_id, {"profile": jsonable_encoder(profile) if profile else None})

    @router.put("/{workspace_id}/enterprise-profile")
    async def update_enterprise_profile(request: Request, workspace_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.enterprise.manage")
        body = await _json_object(request)
        current = await runtime.enterprise_profile(request, context=context)
        profile = await runtime.update_enterprise_profile(
            request,
            update=EnterpriseProfileUpdate(
                legal_name=(
                    _nullable_text(body, "legalName") if "legalName" in body
                    else current.legal_name if current else None
                ),
                invoice_title=(
                    _nullable_text(body, "invoiceTitle") if "invoiceTitle" in body
                    else current.invoice_title if current else None
                ),
                data_retention_days=(
                    _optional_integer(body.get("dataRetentionDays"), "INVALID_DATA_RETENTION_DAYS")
                    if "dataRetentionDays" in body else current.data_retention_days if current else None
                ),
                security_policy=(
                    _optional_object(body, "securityPolicy")
                    if "securityPolicy" in body else dict(current.security_policy) if current else {}
                ),
                dedicated_deployment=(
                    _optional_object(body, "dedicatedDeployment")
                    if "dedicatedDeployment" in body else dict(current.dedicated_deployment) if current else {}
                ),
            ),
            expected_version=_expected_version(request, body, allow_zero=True),
            idempotency_key=_idempotency_key(request),
            context=context,
        )
        return _success(context.trusted.request_id, {"profile": jsonable_encoder(profile)})

    @router.get("/{workspace_id}/overview")
    async def overview(request: Request, workspace_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.view")
        result = await runtime.overview(request, context=context)
        return _success(context.trusted.request_id, result)

    @router.get("/{workspace_id}/projects/{project_id}/members")
    async def project_members(
        request: Request,
        workspace_id: str,
        project_id: str,
        query: str | None = None,
        pageSize: int = 50,
        pageToken: str | None = None,
    ) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.view")
        rows, next_page_token = await runtime.project_members(
            request,
            project_id=project_id,
            query=query,
            page_size=pageSize,
            page_token=pageToken,
            context=context,
        )
        return _success(
            context.trusted.request_id,
            {"items": jsonable_encoder(rows), "nextPageToken": next_page_token},
        )

    @router.post("/{workspace_id}/projects/{project_id}/members")
    async def save_project_member(
        request: Request,
        workspace_id: str,
        project_id: str,
    ) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.manage")
        body = await _json_object(request)
        assignment = await runtime.save_project_member(
            request,
            project_id=project_id,
            member_id=_text(body, "memberId"),
            production_role=_text(body, "productionRole"),
            data_scope=tuple(_string_list(body, "dataScope")),
            permissions=tuple(_string_list(body, "permissions")),
            active=_optional_boolean(body.get("active"), default=True),
            expected_version=_expected_version(request, body, allow_zero=True),
            idempotency_key=_idempotency_key(request),
            context=context,
        )
        return _success(context.trusted.request_id, {"projectMember": jsonable_encoder(assignment)})

    @router.get("/{workspace_id}/audit-events")
    async def audit_events(
        request: Request,
        workspace_id: str,
        requestId: str | None = None,
        query: str | None = None,
        actorId: str | None = None,
        action: str | None = None,
        objectType: str | None = None,
        objectId: str | None = None,
        pageSize: int = 50,
        pageToken: str | None = None,
    ) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.audit.view")
        rows, next_page_token = await runtime.audit_events(
            request,
            request_id=requestId,
            search=query,
            actor_id=actorId,
            action=action,
            object_type=objectType,
            object_id=objectId,
            page_size=pageSize,
            page_token=pageToken,
            context=context,
        )
        return _success(
            context.trusted.request_id,
            {"auditEvents": jsonable_encoder(rows)},
            next_page_token=next_page_token,
        )

    @router.post("/{workspace_id}/invitations")
    async def create_invitation(request: Request, workspace_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.manage")
        body = await _json_object(request)
        invitation = await runtime.create_invitation(
            request,
            email=_text(body, "email").casefold(),
            role_id=_text(body, "roleId"),
            expires_at=_timestamp(body, "expiresAt"),
            idempotency_key=_idempotency_key(request),
            context=context,
        )
        return _success(context.trusted.request_id, {"invitation": jsonable_encoder(invitation)})

    @router.post("/{workspace_id}/invitations/{invitation_id}/revoke")
    async def revoke_invitation(request: Request, workspace_id: str, invitation_id: str) -> JSONResponse:
        context = await _context_for_workspace(runtime, request, workspace_id, "workspace.member.manage")
        invitation = await runtime.revoke_invitation(
            request, invitation_id=invitation_id, idempotency_key=_idempotency_key(request), context=context
        )
        return _success(context.trusted.request_id, {"invitation": jsonable_encoder(invitation)})

    @router.post("/invitations/{invitation_id}/accept")
    async def accept_invitation(request: Request, invitation_id: str) -> JSONResponse:
        invitation = await runtime.accept_invitation_from_identity(
            request,
            invitation_id=invitation_id,
            idempotency_key=_idempotency_key(request),
        )
        request_id = (request.headers.get("X-Request-Id") or "").strip()
        return _success(request_id, {"invitation": jsonable_encoder(invitation)})

    return router


def create_unavailable_team_router(reason: str) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces", tags=["xingjing-team"], route_class=TeamContractRoute)

    @router.api_route(
        "/{workspace_id}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def unavailable(request: Request, workspace_id: str, path: str) -> JSONResponse:
        del workspace_id, path
        return _error(request, reason, status.HTTP_503_SERVICE_UNAVAILABLE)

    return router


async def _context_for_workspace(runtime: TeamRuntime, request: Request, workspace_id: str, permission: str):
    context = await runtime.resolve_context(request, permission)
    if context.trusted.workspace_id != workspace_id:
        raise TeamHttpError("WORKSPACE_SCOPE_MISMATCH", status.HTTP_403_FORBIDDEN)
    return context


async def _json_object(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise TeamHttpError("INVALID_JSON") from error
    if not isinstance(value, Mapping):
        raise TeamHttpError("INVALID_REQUEST_BODY")
    return cast(Mapping[str, object], value)


def _text(body: Mapping[str, object], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise TeamHttpError(f"{name.upper()}_REQUIRED")
    return value.strip()


def _nullable_text(body: Mapping[str, object], name: str) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TeamHttpError(f"{name.upper()}_INVALID")
    return value.strip() or None


def _string_list(body: Mapping[str, object], name: str) -> list[str]:
    value = body.get(name)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise TeamHttpError(f"{name.upper()}_REQUIRED")
    return [item.strip() for item in value]


def _optional_object(body: Mapping[str, object], name: str) -> dict[str, Any]:
    value = body.get(name)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TeamHttpError(f"{name.upper()}_INVALID")
    return dict(cast(Mapping[str, Any], value))


def _optional_integer(value: object, code: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TeamHttpError(code)
    return value


def _timestamp(body: Mapping[str, object], name: str) -> datetime:
    value = _text(body, name)
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TeamHttpError(f"{name.upper()}_INVALID") from error
    if timestamp.tzinfo is None:
        raise TeamHttpError(f"{name.upper()}_TIMEZONE_REQUIRED")
    return timestamp


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value:
        raise TeamHttpError("IDEMPOTENCY_KEY_REQUIRED")
    return value


def _expected_version(request: Request, body: Mapping[str, object], *, allow_zero: bool = False) -> int:
    candidate: object = request.headers.get("If-Match", "").strip().strip('"') or body.get("version")
    if isinstance(candidate, bool) or not isinstance(candidate, int | str):
        raise TeamHttpError("IF_MATCH_REQUIRED")
    try:
        version = int(candidate)
    except ValueError as error:
        raise TeamHttpError("IF_MATCH_REQUIRED") from error
    if version < (0 if allow_zero else 1):
        raise TeamHttpError("IF_MATCH_REQUIRED")
    return version


def _optional_boolean(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TeamHttpError("BOOLEAN_VALUE_REQUIRED")
    return value


def _success(
    request_id: str, data: Mapping[str, object], *, next_page_token: str | None = None
) -> JSONResponse:
    meta: dict[str, object] = {"requestId": request_id}
    if next_page_token is not None:
        meta["page"] = {"nextToken": next_page_token}
    return JSONResponse({"data": dict(data), "meta": meta})


def _error(request: Request, code: str, status_code: int) -> JSONResponse:
    request_id = (request.headers.get("X-Request-Id") or "unknown").strip() or "unknown"
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "details": {}}, "meta": {"requestId": request_id}}
    )
