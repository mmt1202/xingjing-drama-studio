"""Source planning stays under the existing trusted project route contract."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError
from sqlalchemy import select

from lib.episode_editor import EpisodeEditError
from lib.i18n import get_locale
from lib.project_manager import get_project_manager
from server.xingjing_content_persistence.repository import ScriptRow
from server.xingjing_projects_runtime.episode_sources import scoped, source_context
from server.xingjing_projects_runtime.episode_sources_models import EpisodeWorkingScriptRow

from .contracts import ConfirmPlan, CreatePlan
from .service import SourcePlanningService

if TYPE_CHECKING:
    from server.xingjing_projects_runtime.runtime import ProjectsRuntime


def register_source_planning_routes(runtime: ProjectsRuntime, router: APIRouter) -> None:
    def service() -> SourcePlanningService:
        return SourcePlanningService(runtime._require_sessions(), get_project_manager())

    def identity(value: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as error:
            raise EpisodeEditError("SOURCE_PLAN_NOT_FOUND", 404) from error

    async def command(request: Request, model):
        from server.xingjing_projects_runtime.episodes import _body

        try:
            return model.model_validate(await _body(request))
        except ValidationError as error:
            raise EpisodeEditError("SOURCE_PLAN_INVALID", 422) from error

    @router.get("/projects/{project_id}/episode-plans/source")
    async def catalog(request: Request, project_id: str):
        context = await source_context(runtime, request, project_id)
        source_id = request.query_params.get("sourceId", "")
        if not 1 <= len(source_id) <= 1024:
            raise EpisodeEditError("SOURCE_PLAN_INVALID", 422)
        return {
            "data": await service().catalog(context, project_id, source_id),
            "meta": {"requestId": context.request_id},
        }

    @router.get("/projects/{project_id}/episode-plans")
    async def plans(request: Request, project_id: str):
        context = await source_context(runtime, request, project_id)
        return {"data": await service().list(context, project_id), "meta": {"requestId": context.request_id}}

    @router.post("/projects/{project_id}/episode-plans")
    async def propose(request: Request, project_id: str):
        context = await source_context(runtime, request, project_id, manage=True)
        result = await service().create(
            context,
            project_id,
            await command(request, CreatePlan),
            key=request.headers.get("Idempotency-Key", ""),
            locale=get_locale(request),
        )
        return {"data": result, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/episode-plans/{plan_id}")
    async def detail(request: Request, project_id: str, plan_id: str):
        context = await source_context(runtime, request, project_id)
        return {
            "data": await service().get(context, project_id, identity(plan_id)),
            "meta": {"requestId": context.request_id},
        }

    @router.post("/projects/{project_id}/episode-plans/{plan_id}/confirm")
    async def confirm(request: Request, project_id: str, plan_id: str):
        context = await source_context(runtime, request, project_id, manage=True)
        result = await service().confirm(
            context,
            project_id,
            identity(plan_id),
            await command(request, ConfirmPlan),
            key=request.headers.get("Idempotency-Key", ""),
            locale=get_locale(request),
        )
        return {"data": result, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/source-snapshots/{snapshot_id}")
    async def snapshot(request: Request, project_id: str, snapshot_id: str):
        context = await source_context(runtime, request, project_id)
        try:
            start, end = int(request.query_params.get("start", "")), int(request.query_params.get("end", ""))
        except ValueError as error:
            raise EpisodeEditError("SOURCE_PLAN_RANGE_INVALID", 422) from error
        return {
            "data": await service().read_snapshot(context, project_id, identity(snapshot_id), start, end),
            "meta": {"requestId": context.request_id},
        }

    @router.get("/projects/{project_id}/scripts/{script_id}/source-provenance")
    async def provenance(request: Request, project_id: str, script_id: str):
        context = await source_context(runtime, request, project_id)
        planning = service()
        async with planning.sessions() as session:
            await planning.project(session, context, project_id)
            document = await session.scalar(
                select(ScriptRow).where(
                    ScriptRow.workspace_id == context.workspace_id,
                    ScriptRow.project_id == project_id,
                    ScriptRow.script_id == script_id,
                )
            )
            if document is None:
                raise EpisodeEditError("EPISODE_WORKING_SCRIPT_NOT_FOUND", 404)
            link = await session.scalar(
                select(EpisodeWorkingScriptRow).where(
                    *scoped(EpisodeWorkingScriptRow, context, project_id),
                    EpisodeWorkingScriptRow.script_id == script_id,
                )
            )
        return {"data": {"sourceRef": link.source_ref if link else None}, "meta": {"requestId": context.request_id}}
