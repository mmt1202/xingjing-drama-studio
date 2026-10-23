"""Authenticated project routes; every write uses a stable command receipt."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import Response

from server.xingjing_generation_persistence import GenerationBillingError, TaskScopeNotFound, VersionConflict
from server.xingjing_generation_runtime.runtime import GenerationRuntimeUnavailable

from .contracts import Command, CoverFailure, CreatePlan, Target
from .runtime import CoverRuntime


class CoverRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except CoverFailure as error:
                code, status = error.code, error.status
            except ValidationError:
                code, status = "COVER_REQUEST_INVALID", 422
            except GenerationRuntimeUnavailable as error:
                code, status = error.code, 503
            except (TaskScopeNotFound, LookupError):
                code, status = "COVER_CANDIDATE_NOT_FOUND", 404
            except (VersionConflict, IntegrityError):
                code, status = "COVER_VERSION_CONFLICT", 409
            except GenerationBillingError as error:
                code = str(error)
                status = 402 if code == "GENERATION_INSUFFICIENT_CREDITS" else 409
            except SQLAlchemyError:
                code, status = "COVER_DATABASE_UNAVAILABLE", 503
            except PermissionError:
                code, status = "COVER_PERMISSION_DENIED", 403
            except HTTPException as error:
                code, status = "COVER_REQUEST_REJECTED", error.status_code
            except ValueError:
                code, status = "COVER_REQUEST_INVALID", 422
            return JSONResponse(
                status_code=status,
                content={
                    "error": {"code": code, "message": code},
                    "meta": {"requestId": request.headers.get("X-Request-Id", "")},
                },
            )

        return handle


def _key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 128:
        raise CoverFailure("COVER_IDEMPOTENCY_KEY_REQUIRED", 422)
    return key


def create_cover_router(runtime: CoverRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["cover-plans"], route_class=CoverRoute)

    @router.get("/projects/{project_id}/cover-plans/catalog")
    async def catalog(request: Request, project_id: str):
        context = await runtime.context(request, project_id)
        return {
            "data": await runtime.catalog(context, project_id),
            "meta": {"requestId": context.request_id},
        }

    @router.get("/projects/{project_id}/cover-plans")
    async def plans(
        request: Request,
        project_id: str,
        finalVersionId: str | None = Query(None, max_length=128),
        episodeId: str | None = Query(None, max_length=128),
        target: Target | None = None,
        q: str = Query("", max_length=255),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        context = await runtime.context(request, project_id)
        value = await asyncio.to_thread(
            runtime.list,
            context,
            project_id,
            final_version_id=finalVersionId,
            episode_id=episodeId,
            target=target,
            q=q,
            offset=offset,
            limit=limit,
        )
        return {"data": value, "meta": {"requestId": context.request_id}}

    @router.post("/projects/{project_id}/cover-plans")
    async def create(request: Request, project_id: str):
        context = await runtime.context(request, project_id)
        payload = CreatePlan.model_validate(await request.json())
        value = await asyncio.to_thread(runtime.create, context, project_id, payload, _key(request))
        return {"data": value, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/cover-plans/{plan_id}")
    async def detail(request: Request, project_id: str, plan_id: str):
        context = await runtime.context(request, project_id)
        return {"data": await runtime.detail(context, project_id, plan_id), "meta": {"requestId": context.request_id}}

    @router.post("/projects/{project_id}/cover-plans/{plan_id}/actions")
    async def action(request: Request, project_id: str, plan_id: str):
        context = await runtime.context(request, project_id)
        command = Command.model_validate(await request.json())
        value = await runtime.command(context, project_id, plan_id, command, _key(request))
        return {"data": value, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/cover-plans/{plan_id}/candidates/{asset_id}/content")
    async def media(request: Request, project_id: str, plan_id: str, asset_id: str):
        context = await runtime.context(request, project_id)
        verified, path = await runtime.media(context, project_id, plan_id, asset_id)
        return FileResponse(
            path,
            media_type=verified["mimeType"],
            content_disposition_type="inline",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router
