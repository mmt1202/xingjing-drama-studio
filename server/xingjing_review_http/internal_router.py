"""Trusted internal review endpoints; public review links keep their own contract."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from server.xingjing_review_runtime.internal_rounds import InternalReviewOperations
from server.xingjing_review_runtime.runtime import ReviewPermissionDenied, ReviewRuntime, StaffReviewContext


def register_internal_review_routes(router: APIRouter, runtime: ReviewRuntime) -> None:
    from server.xingjing_review_http.router import PRIVATE_HEADERS, _json_object, _required_header, _success

    operations = InternalReviewOperations(runtime)

    async def context(request: Request, project_id: str, permission: str) -> StaffReviewContext:
        result = await runtime.staff_context(request, project_id=project_id, permission=permission)
        if result.trusted.role == "API_KEY":
            raise ReviewPermissionDenied(permission)
        expected_workspace = request.headers.get("X-Workspace-Id")
        if expected_workspace and expected_workspace != result.scope.workspace_id:
            raise ReviewPermissionDenied(permission)
        return result

    @router.get("/projects/{project_id}/internal-reviews")
    async def list_rounds(
        request: Request,
        project_id: str,
        q: str = "",
        state: str = "",
        offset: int = 0,
        limit: int = 20,
        finalVideoVersionId: str | None = None,
        episodeId: str | None = None,
    ) -> JSONResponse:
        staff = await context(request, project_id, "review.view")
        if offset < 0 or limit < 1 or limit > 50 or len(q) > 160:
            raise ValueError("INTERNAL_REVIEW_PAGINATION_INVALID")
        return _success(
            request,
            await asyncio.to_thread(
                operations.list, staff, project_id, q, state, offset, limit, finalVideoVersionId, episodeId
            ),
        )

    @router.get("/projects/{project_id}/internal-reviews/{round_id}")
    async def detail(request: Request, project_id: str, round_id: str) -> JSONResponse:
        staff = await context(request, project_id, "review.view")
        return _success(request, await asyncio.to_thread(operations.detail, staff, project_id, round_id))

    async def execute(
        request: Request, project_id: str, action: str, round_id: str | None = None, comment_id: str | None = None
    ) -> JSONResponse:
        staff = await context(request, project_id, "review.view" if action == "handoff" else "review.manage")
        if action == "handoff" and "export.view" not in staff.trusted.permissions:
            raise ReviewPermissionDenied("export.view")
        body = await _json_object(request)
        key = _required_header(request, "Idempotency-Key", "IDEMPOTENCY_KEY_REQUIRED")
        return _success(
            request,
            await asyncio.to_thread(operations.command, staff, project_id, action, body, key, round_id, comment_id),
        )

    @router.post("/projects/{project_id}/internal-reviews")
    async def create(request: Request, project_id: str) -> JSONResponse:
        return await execute(request, project_id, "create")

    @router.post("/projects/{project_id}/internal-reviews/{round_id}/comments")
    async def comment(request: Request, project_id: str, round_id: str) -> JSONResponse:
        return await execute(request, project_id, "comment", round_id)

    @router.post("/projects/{project_id}/internal-reviews/{round_id}/comments/{comment_id}/status")
    async def comment_status(request: Request, project_id: str, round_id: str, comment_id: str) -> JSONResponse:
        return await execute(request, project_id, "status", round_id, comment_id)

    @router.post("/projects/{project_id}/internal-reviews/{round_id}/decision")
    async def decision(request: Request, project_id: str, round_id: str) -> JSONResponse:
        return await execute(request, project_id, "decision", round_id)

    @router.post("/projects/{project_id}/internal-reviews/{round_id}/lock")
    async def lock(request: Request, project_id: str, round_id: str) -> JSONResponse:
        return await execute(request, project_id, "lock", round_id)

    @router.post("/projects/{project_id}/internal-reviews/{round_id}/reopen")
    async def reopen(request: Request, project_id: str, round_id: str) -> JSONResponse:
        return await execute(request, project_id, "reopen", round_id)

    @router.get("/internal-review-media/{round_id}")
    async def media(round_id: str, ticket: str) -> FileResponse:
        if len(ticket) > 4096:
            raise ValueError("INTERNAL_REVIEW_TICKET_INVALID")
        verified = await asyncio.to_thread(operations.media, round_id, ticket)
        return FileResponse(
            verified.path, media_type=verified.media_type, content_disposition_type="inline", headers=PRIVATE_HEADERS
        )

    @router.post("/projects/{project_id}/internal-reviews/{round_id}/handoff")
    async def handoff(request: Request, project_id: str, round_id: str) -> JSONResponse:
        return await execute(request, project_id, "handoff", round_id)
