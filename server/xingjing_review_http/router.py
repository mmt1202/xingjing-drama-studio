"""M12 审片外链和内部链接管理的受控 HTTP 入口。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from server.xingjing_review.errors import (
    InvalidTransition,
    LinkUnavailable,
    PermissionDenied,
    ValidationError,
    VersionConflict,
)
from server.xingjing_review.models import AccessPolicy, IssueStatus
from server.xingjing_review_runtime import ReviewRuntime, ReviewRuntimeConfigurationError
from server.xingjing_review_runtime.runtime import ReviewPermissionDenied, ReviewProjectScopeDenied


class ReviewHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class ReviewContractRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> JSONResponse:
            try:
                return cast(JSONResponse, await original(request))
            except ReviewHttpError as error:
                return _error(request, error.code, error.status_code)
            except (ReviewPermissionDenied, PermissionDenied, PermissionError):
                return _error(request, "PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)
            except (ReviewProjectScopeDenied, LinkUnavailable):
                return _error(request, "REVIEW_LINK_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except (VersionConflict, InvalidTransition):
                return _error(request, "REVIEW_VERSION_OR_STATE_CONFLICT", status.HTTP_409_CONFLICT)
            except (ReviewRuntimeConfigurationError, SQLAlchemyError):
                return _error(request, "REVIEW_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, Mapping) else {}
                raw_code = detail.get("code")
                code = raw_code if isinstance(raw_code, str) else "REQUEST_REJECTED"
                return _error(request, code, error.status_code)
            except (ValidationError, ValueError) as error:
                return _error(request, str(error) or "INVALID_REVIEW_REQUEST", status.HTTP_400_BAD_REQUEST)

        return handler


def create_review_router(runtime: ReviewRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["xingjing-review"], route_class=ReviewContractRoute)

    @router.get("/review-links/{token}/context")
    async def external_context(request: Request, token: str) -> JSONResponse:
        try:
            result = await runtime.open_public_context(token=token, access_secret=_access_secret(request))
        except PermissionDenied:
            return _success(request, {"verificationRequired": True})
        return _success(request, result)

    @router.post("/review-links/{token}/actions")
    async def external_action(request: Request, token: str) -> JSONResponse:
        body = await _json_object(request)
        action = _text(body, "action")
        if action == "verify":
            payload = body.get("payload")
            fields = cast(Mapping[str, object], payload) if isinstance(payload, Mapping) else body
            result = await runtime.open_public_context(token=token, access_secret=_text(fields, "credential"))
            return _success(request, result)
        result = await runtime.public_action(
            token=token,
            session_id=_required_header(request, "X-Review-Session-Id", "REVIEW_SESSION_REQUIRED"),
            action=action,
            payload=body,
            idempotency_key=_required_header(request, "Idempotency-Key", "IDEMPOTENCY_KEY_REQUIRED"),
        )
        return _success(request, result)

    @router.get("/review-links/{token}/media")
    async def external_media(request: Request, token: str, session: str, ticket: str, download: bool = False) -> FileResponse:
        media = await runtime.public_media(token=token, session_id=session, ticket=ticket, download=download)
        return FileResponse(media.path, media_type=media.media_type,
                            filename=media.filename if download else None,
                            content_disposition_type="attachment" if download else "inline")

    @router.post("/review-links/{token}/screenshots")
    async def upload_screenshot(request: Request, token: str, timecodeMs: int) -> JSONResponse:
        content_length = request.headers.get("Content-Length")
        if content_length and int(content_length) > 10 * 1024 * 1024:
            raise ReviewHttpError("REVIEW_SCREENSHOT_TOO_LARGE", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        result = await runtime.upload_screenshot(token=token,
            session_id=_required_header(request, "X-Review-Session-Id", "REVIEW_SESSION_REQUIRED"),
            content=await request.body(), media_type=request.headers.get("Content-Type", "").split(";", 1)[0].strip(),
            timecode_ms=timecodeMs, request_id=_request_id(request))
        return _success(request, {"screenshot": result})

    @router.get("/review-links/{token}/screenshots/{screenshot_id}")
    async def external_screenshot(request: Request, token: str, screenshot_id: str, session: str, ticket: str) -> FileResponse:
        media = await runtime.public_screenshot(token=token, session_id=session, ticket=ticket, screenshot_id=screenshot_id)
        return FileResponse(media.path, media_type=media.media_type, content_disposition_type="inline")

    @router.post("/review-links/{token}/logout")
    async def external_logout(request: Request, token: str) -> JSONResponse:
        await runtime.logout_public_session(token=token,
            session_id=_required_header(request, "X-Review-Session-Id", "REVIEW_SESSION_REQUIRED"),
            request_id=_request_id(request))
        return _success(request, {"loggedOut": True})

    @router.get("/projects/{project_id}/reviews")
    async def project_reviews(request: Request, project_id: str, q: str | None = None,
                              pageSize: int = 20, pageToken: str | None = None) -> JSONResponse:
        context = await runtime.staff_context(request, project_id=project_id, permission="review.view")
        return _success(request, await runtime.list_reviews(context, project_id=project_id, query=q,
                                                            page_size=pageSize, page_token=pageToken))

    @router.post("/projects/{project_id}/review-links")
    async def create_project_link(request: Request, project_id: str) -> JSONResponse:
        context = await runtime.staff_context(request, project_id=project_id, permission="review.manage")
        return _success(request, {"reviewLink": await _create_link(runtime, context, request, project_id)})

    @router.get("/workspaces/{workspace_id}/review-links")
    async def workspace_reviews(request: Request, workspace_id: str, q: str | None = None,
                                pageSize: int = 20, pageToken: str | None = None) -> JSONResponse:
        context = await runtime.staff_context(request, project_id=None, permission="review.view")
        _require_workspace(context.trusted.workspace_id, workspace_id)
        return _success(request, await runtime.list_reviews(context, query=q, page_size=pageSize, page_token=pageToken))

    @router.post("/workspaces/{workspace_id}/review-links")
    async def create_workspace_link(request: Request, workspace_id: str) -> JSONResponse:
        body = await _json_object(request)
        project_id = _text(body, "projectId")
        context = await runtime.staff_context(request, project_id=project_id, permission="review.manage")
        _require_workspace(context.trusted.workspace_id, workspace_id)
        return _success(request, {"reviewLink": await _create_link(runtime, context, request, project_id, body)})

    @router.post("/workspaces/{workspace_id}/review-links/{link_id}/revoke")
    async def revoke_link(request: Request, workspace_id: str, link_id: str) -> JSONResponse:
        body = await _json_object(request)
        context = await runtime.staff_context(request, project_id=None, permission="review.manage")
        _require_workspace(context.trusted.workspace_id, workspace_id)
        result = await runtime.revoke_link(
            context,
            link_id=link_id,
            expected_version=_version(request, body),
            idempotency_key=_required_header(request, "Idempotency-Key", "IDEMPOTENCY_KEY_REQUIRED"),
        )
        return _success(request, {"reviewLink": result})

    @router.get("/workspaces/{workspace_id}/review-links/{link_id}")
    async def review_detail(request: Request, workspace_id: str, link_id: str) -> JSONResponse:
        context = await runtime.staff_context(request, project_id=None, permission="review.view")
        _require_workspace(context.trusted.workspace_id, workspace_id)
        return _success(request, await runtime.staff_review_detail(context, link_id=link_id))

    @router.post("/workspaces/{workspace_id}/review-comments/{comment_id}/status")
    async def change_comment_status(request: Request, workspace_id: str, comment_id: str) -> JSONResponse:
        body = await _json_object(request)
        context = await runtime.staff_context(request, project_id=None, permission="review.manage")
        _require_workspace(context.trusted.workspace_id, workspace_id)
        result = await runtime.change_comment_status(context, comment_id=comment_id,
            issue_status=IssueStatus(_text(body, "status")), expected_version=_version(request, body),
            idempotency_key=_required_header(request, "Idempotency-Key", "IDEMPOTENCY_KEY_REQUIRED"))
        return _success(request, {"comment": result})

    return router


def create_unavailable_review_router(code: str = "REVIEW_RUNTIME_NOT_CONFIGURED") -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["xingjing-review"])

    async def unavailable(request: Request) -> JSONResponse:
        return _error(request, code, status.HTTP_503_SERVICE_UNAVAILABLE)

    routes = (
        ("/review-links/{token}/context", ["GET"], "m12_review_context_unavailable"),
        ("/review-links/{token}/actions", ["POST"], "m12_review_actions_unavailable"),
        ("/review-links/{token}/media", ["GET"], "m12_review_media_unavailable"),
        ("/review-links/{token}/screenshots", ["POST"], "m12_review_screenshot_upload_unavailable"),
        ("/review-links/{token}/screenshots/{screenshot_id}", ["GET"], "m12_review_screenshot_unavailable"),
        ("/review-links/{token}/logout", ["POST"], "m12_review_logout_unavailable"),
        ("/projects/{project_id}/reviews", ["GET"], "m12_project_reviews_unavailable"),
        ("/projects/{project_id}/review-links", ["POST"], "m12_project_link_unavailable"),
        ("/workspaces/{workspace_id}/review-links", ["GET"], "m12_workspace_reviews_unavailable"),
        ("/workspaces/{workspace_id}/review-links", ["POST"], "m12_workspace_link_unavailable"),
        (
            "/workspaces/{workspace_id}/review-links/{link_id}/revoke",
            ["POST"],
            "m12_review_revoke_unavailable",
        ),
        ("/workspaces/{workspace_id}/review-links/{link_id}", ["GET"], "m12_review_detail_unavailable"),
        ("/workspaces/{workspace_id}/review-comments/{comment_id}/status", ["POST"], "m12_review_comment_status_unavailable"),
    )
    for path, methods, operation_id in routes:
        router.add_api_route(path, unavailable, methods=methods, operation_id=operation_id)
    return router


async def _create_link(runtime: ReviewRuntime, context: Any, request: Request, project_id: str, body: Mapping[str, object] | None = None) -> dict[str, object]:
    payload = body or await _json_object(request)
    policy_data = _object(payload, "policy")
    return await runtime.create_link(
        context,
        project_id=project_id,
        final_video_version_id=_text(payload, "finalVideoVersionId"),
        expires_at=_optional_timestamp(payload.get("expiresAt")),
        policy=AccessPolicy(
            comment=_boolean(policy_data, "comment"),
            approve=_boolean(policy_data, "approve"),
            download=_boolean(policy_data, "download"),
        ),
        watermark_text=_optional_text(payload.get("watermarkText")),
        access_secret=_optional_text(payload.get("accessSecret")),
        idempotency_key=_required_header(request, "Idempotency-Key", "IDEMPOTENCY_KEY_REQUIRED"),
    )


async def _json_object(request: Request) -> Mapping[str, object]:
    try:
        payload = await request.json()
    except ValueError as error:
        raise ReviewHttpError("INVALID_JSON") from error
    if not isinstance(payload, Mapping):
        raise ReviewHttpError("INVALID_REQUEST_BODY")
    return cast(Mapping[str, object], payload)


def _text(body: Mapping[str, object], name: str) -> str:
    return _required_text(body.get(name), f"{name.upper()}_REQUIRED")


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewHttpError(code)
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "INVALID_TEXT")


def _object(body: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = body.get(name)
    if not isinstance(value, Mapping):
        raise ReviewHttpError(f"{name.upper()}_REQUIRED")
    return cast(Mapping[str, object], value)


def _boolean(body: Mapping[str, object], name: str) -> bool:
    value = body.get(name)
    if not isinstance(value, bool):
        raise ReviewHttpError(f"{name.upper()}_REQUIRED")
    return value


def _optional_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    text = _required_text(value, "EXPIRES_AT_INVALID")
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReviewHttpError("EXPIRES_AT_INVALID") from error
    if result.tzinfo is None:
        raise ReviewHttpError("EXPIRES_AT_TIMEZONE_REQUIRED")
    return result


def _version(request: Request, body: Mapping[str, object]) -> int:
    raw: object = request.headers.get("If-Match", "").strip().strip('"') or body.get("version")
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        raise ReviewHttpError("IF_MATCH_REQUIRED")
    try:
        result = int(raw)
    except ValueError as error:
        raise ReviewHttpError("IF_MATCH_REQUIRED") from error
    if result < 1:
        raise ReviewHttpError("IF_MATCH_REQUIRED")
    return result


def _required_header(request: Request, name: str, code: str) -> str:
    return _required_text(request.headers.get(name, ""), code)


def _access_secret(request: Request) -> str | None:
    value = request.headers.get("X-Review-Access-Secret")
    return None if value is None else _required_text(value, "REVIEW_ACCESS_SECRET_INVALID")


def _require_workspace(actual: str, expected: str) -> None:
    if actual != expected:
        raise ReviewHttpError("WORKSPACE_SCOPE_MISMATCH", status.HTTP_403_FORBIDDEN)


def _success(request: Request, data: Mapping[str, object]) -> JSONResponse:
    return JSONResponse({"data": dict(data), "meta": {"requestId": _request_id(request)}})


def _error(request: Request, code: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "details": {}}, "meta": {"requestId": _request_id(request)}})


def _request_id(request: Request) -> str:
    return (request.headers.get("X-Request-Id") or "unknown").strip() or "unknown"
