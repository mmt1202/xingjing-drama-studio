"""Authenticated unified version reads and audited project commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from json import JSONDecodeError
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.responses import Response

from .actions import VersionActionFailure
from .contracts import Kind, Ref, ReportRequest, VersionAction, VersionFailure
from .runtime import VersionRuntime


class VersionRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except (VersionFailure, VersionActionFailure) as error:
                code, status = error.code, error.status
            except (ValidationError, RequestValidationError, JSONDecodeError, UnicodeDecodeError):
                code, status = "VERSION_REQUEST_INVALID", 422
            except IntegrityError:
                code, status = "VERSION_CONFLICT", 409
            except SQLAlchemyError:
                code, status = "VERSION_SOURCE_UNAVAILABLE", 503
            except HTTPException as error:
                code, status = "VERSION_CONTEXT_REJECTED", error.status_code
            except ValueError:
                code, status = "VERSION_SOURCE_INVALID", 503
            return JSONResponse(
                status_code=status,
                content={
                    "error": {"code": code, "message": code},
                    "meta": {"requestId": request.headers.get("X-Request-Id", "")},
                },
            )

        return handle


def _key(request: Request):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 128:
        raise VersionFailure("VERSION_IDEMPOTENCY_KEY_REQUIRED", 422)
    return key


def create_version_router(runtime: VersionRuntime):
    router = APIRouter(
        prefix="/api/v1/projects/{project_id}/version-history", tags=["version-history"], route_class=VersionRoute
    )

    @router.get("")
    async def listing(
        request: Request,
        project_id: str,
        kind: Kind | None = None,
        q: str = Query("", max_length=255),
        episodeId: str | None = Query(None, max_length=128),
        objectId: str | None = Query(None, max_length=128),
        shotId: str | None = Query(None, max_length=128),
        state: Literal["current", "locked", "restorable"] | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=100),
    ):
        context = await runtime.context(request, project_id)
        value = await asyncio.to_thread(
            runtime.list,
            context,
            project_id,
            kind=kind,
            q=q,
            episode_id=episodeId,
            object_id=objectId,
            shot_id=shotId,
            state=state,
            offset=offset,
            limit=limit,
        )
        return {"data": value, "meta": {"requestId": context.request_id}}

    @router.get("/detail")
    async def detail(
        request: Request, project_id: str, kind: Kind, objectId: str, versionId: str, shotId: str | None = None
    ):
        context = await runtime.context(request, project_id)
        ref = Ref(kind=kind, objectId=objectId, versionId=versionId, shotId=shotId)
        return {
            "data": await asyncio.to_thread(runtime.detail, context, project_id, ref),
            "meta": {"requestId": context.request_id},
        }

    @router.get("/compare")
    async def compare(
        request: Request,
        project_id: str,
        kind: Kind,
        objectId: str,
        baselineVersionId: str,
        candidateVersionId: str,
        shotId: str | None = None,
    ):
        context = await runtime.context(request, project_id)
        baseline = Ref(kind=kind, objectId=objectId, versionId=baselineVersionId, shotId=shotId)
        candidate = Ref(kind=kind, objectId=objectId, versionId=candidateVersionId, shotId=shotId)
        return {
            "data": await asyncio.to_thread(runtime.compare, context, project_id, baseline, candidate),
            "meta": {"requestId": context.request_id},
        }

    @router.get("/checkpoints")
    async def checkpoints(
        request: Request, project_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)
    ):
        context = await runtime.context(request, project_id)
        return {
            "data": await asyncio.to_thread(runtime.checkpoints, context, project_id, offset, limit),
            "meta": {"requestId": context.request_id},
        }

    @router.post("/actions")
    async def command(request: Request, project_id: str):
        context = await runtime.context(request, project_id)
        payload = VersionAction.model_validate(await request.json())
        return {
            "data": await runtime.command(context, project_id, payload, _key(request)),
            "meta": {"requestId": context.request_id},
        }

    @router.post("/reports")
    async def report(request: Request, project_id: str):
        context = await runtime.context(request, project_id)
        payload = ReportRequest.model_validate(await request.json())
        return {
            "data": await asyncio.to_thread(runtime.report, context, project_id, payload, _key(request)),
            "meta": {"requestId": context.request_id},
        }

    return router
