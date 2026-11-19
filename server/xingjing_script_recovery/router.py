from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import ValidationError

from lib.episode_editor import EpisodeEditError
from lib.i18n import get_locale
from lib.project_manager import get_project_manager
from server.xingjing_projects_runtime.episode_sources import source_context
from server.xingjing_projects_runtime.episodes import _body
from server.xingjing_source_planning.service import SourcePlanningService

from .contracts import Confirmation, Selection
from .service import ScriptRecovery

if TYPE_CHECKING:
    from server.xingjing_projects_runtime.runtime import ProjectsRuntime


def register_recovery_routes(runtime: "ProjectsRuntime", router: APIRouter) -> None:
    path = "/projects/{project_id}/scripts/{script_id}/partial-recovery"

    def service() -> ScriptRecovery:
        return ScriptRecovery(SourcePlanningService(runtime._require_sessions(), get_project_manager()))

    async def body(request, model):
        try:
            return model.model_validate(await _body(request))
        except ValidationError as error:
            raise EpisodeEditError("SCRIPT_RECOVERY_SELECTION_INVALID", 422) from error

    @router.get(path)
    async def recovery(request: Request, project_id: str, script_id: str):
        context = await source_context(runtime, request, project_id)
        return {"data": await service().read(context, project_id, script_id), "meta": {"requestId": context.request_id}}

    @router.post(path + "/preview")
    async def preview(request: Request, project_id: str, script_id: str):
        context = await source_context(runtime, request, project_id)
        result = await service().preview(
            context, project_id, script_id, await body(request, Selection), get_locale(request)
        )
        return {"data": result, "meta": {"requestId": context.request_id}}

    @router.post(path + "/confirm")
    async def confirm(request: Request, project_id: str, script_id: str):
        context = await source_context(runtime, request, project_id)
        result = await service().confirm(
            context,
            project_id,
            script_id,
            await body(request, Confirmation),
            key=request.headers.get("Idempotency-Key", ""),
            locale=get_locale(request),
        )
        return {"data": result, "meta": {"requestId": context.request_id}}
