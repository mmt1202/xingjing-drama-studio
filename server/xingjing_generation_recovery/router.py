"""A dedicated administrator surface and a separately authenticated receipt inbox."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response

from lib.i18n import get_translator
from server.xingjing_generation_runtime.artifact_storage import GeneratedArtifactStorageError
from server.xingjing_generation_runtime.runtime import GenerationRuntimeUnavailable

from .artifacts import verify_outputs
from .contracts import PreviewCommand, RecoveryError, ResolveCommand
from .models import RecoveryReceiptRow
from .service import GenerationRecoveryService, require
from .views import list_tasks, task_detail

if TYPE_CHECKING:
    from server.xingjing_generation_runtime.runtime import GenerationRuntime


class RecoveryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except RecoveryError as error:
                code, status = error.code, error.status
            except RequestValidationError:
                code, status = "RECOVERY_COMMAND_INVALID", 422
            except GeneratedArtifactStorageError:
                code, status = "RECOVERY_ARTIFACT_CHANGED", 409
            except HTTPException as error:
                code, status = (
                    ("RECOVERY_PERMISSION_DENIED", error.status_code)
                    if error.status_code in {401, 403}
                    else ("RECOVERY_UNAVAILABLE", error.status_code)
                )
            except (SQLAlchemyError, GenerationRuntimeUnavailable):
                code, status = "RECOVERY_UNAVAILABLE", 503
            return JSONResponse(
                status_code=status,
                content={
                    "error": {"code": code, "message": get_translator(request)(code.lower())},
                    "meta": {"requestId": request.headers.get("X-Request-Id", "unknown")},
                },
            )

        return handle


def create_recovery_router(runtime: GenerationRuntime) -> APIRouter:
    router = APIRouter(route_class=RecoveryRoute)

    def service() -> GenerationRecoveryService:
        if runtime.recovery is None or not runtime.available:
            raise RecoveryError("RECOVERY_UNAVAILABLE", 503)
        return runtime.recovery

    async def context(request: Request, *, manage: bool = False):
        trusted = await runtime.trusted_context(request)
        require(trusted, manage=manage)
        if request.headers.get("X-Workspace-Id") != trusted.workspace_id:
            raise RecoveryError("RECOVERY_WORKSPACE_CHANGED", 403)
        return trusted

    @router.get("/admin/generation-recovery")
    async def overview(
        request: Request,
        q: str = Query("", max_length=128),
        state: Literal["attention", "handled", "all"] = "attention",
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
    ):
        trusted = await context(request)
        return {
            "data": await list_tasks(service(), trusted, search=q.strip(), state=state, offset=offset, limit=limit),
            "meta": {"requestId": trusted.request_id},
        }

    @router.get("/admin/generation-recovery/projects/{project_id}/tasks/{task_id}")
    async def detail(request: Request, project_id: str, task_id: str):
        trusted = await context(request)
        return {
            "data": await task_detail(service(), trusted, project_id, task_id),
            "meta": {"requestId": trusted.request_id},
        }

    @router.post("/admin/generation-recovery/projects/{project_id}/tasks/{task_id}/preview")
    async def preview(request: Request, project_id: str, task_id: str, command: PreviewCommand):
        trusted = await context(request, manage=True)
        return {
            "data": await service().preview(trusted, project_id, task_id, command),
            "meta": {"requestId": trusted.request_id},
        }

    @router.post("/admin/generation-recovery/projects/{project_id}/tasks/{task_id}/resolve")
    async def resolve(request: Request, project_id: str, task_id: str, command: ResolveCommand):
        trusted = await context(request, manage=True)
        return {
            "data": await service().resolve(
                trusted, project_id, task_id, command, key=request.headers.get("Idempotency-Key", "")
            ),
            "meta": {"requestId": trusted.request_id},
        }

    @router.get(
        "/admin/generation-recovery/projects/{project_id}/tasks/{task_id}/receipts/{receipt_id}/artifacts/{index}"
    )
    async def artifact(request: Request, project_id: str, task_id: str, receipt_id: str, index: int):
        trusted = await context(request)
        recovery = service()
        await task_detail(recovery, trusted, project_id, task_id)
        async with recovery.sessions() as session:
            row = await session.get(RecoveryReceiptRow, receipt_id)
        if (
            row is None
            or (row.tenant_id, row.workspace_id, row.project_id, row.task_id)
            != (trusted.tenant_id, trusted.workspace_id, project_id, task_id)
            or not 0 <= index < len(row.artifacts)
        ):
            raise RecoveryError("RECOVERY_RECEIPT_NOT_FOUND", 404)
        asset = row.artifacts[index]
        await verify_outputs(recovery.store, [asset])
        assert recovery.store is not None
        metadata = asset["metadata"]
        assert isinstance(metadata, dict)
        return FileResponse(
            recovery.store.resolve_object_key(str(asset["object_key"])),
            media_type=str(metadata["mime_type"]),
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/internal/generation-recovery/receipts")
    async def receive(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 64_000:
                raise RecoveryError("RECOVERY_RECEIPT_INVALID", 413)
        return {
            "data": await service().receive(bytes(body), request.headers.get("X-Generation-Recovery-Signature", ""))
        }

    return router
