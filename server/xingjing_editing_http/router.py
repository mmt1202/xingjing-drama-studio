from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from server.xingjing_editing import (
    AuditEvent,
    EditingError,
    FinalVideoSelection,
    FinalVideoVersion,
    Permission,
    PermissionDenied,
    PreviewSummary,
    RenderCallbackResult,
    RenderStatus,
    RenderTask,
    RenderTaskNotFound,
    TimelineVersion,
    VersionConflict,
)
from server.xingjing_editing.contracts import build_preview_summary, canonical_sha256
from server.xingjing_editing_storage import ObjectStorageError

from .dependencies import EditingDependencies
from .models import (
    AddTrackBody,
    CancelRenderBody,
    CreateTimelineBody,
    FinalDeliverySnapshot,
    ProviderRenderCallbackBody,
    ReplaceClipBody,
    RetryRenderBody,
    SelectFinalVideoVersionBody,
    SubmitRenderBody,
    TimelineSourceSummary,
    UpdateClipBody,
    UpdateOutputPolicyBody,
)

DependencyProvider = Callable[[], EditingDependencies]


def _unconfigured_dependencies() -> EditingDependencies:
    raise RuntimeError("EDITING_HTTP_DEPENDENCIES_NOT_CONFIGURED")


def _http_error(error: EditingError) -> HTTPException:
    detail: dict[str, object] = {"code": error.code, "message": str(error)}
    if isinstance(error, VersionConflict):
        detail.update(
            {
                "expected_version": error.expected_version,
                "current_version": error.current_version,
            }
        )
    return HTTPException(status_code=error.status_code, detail=detail)


class EditingContractRoute(APIRoute):
    """Keep production wiring and storage failures within the public HTTP contract."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except LookupError as error:
                return JSONResponse(status_code=404, content={"detail": {"code": str(error)}})
            except (SQLAlchemyError, ObjectStorageError):
                return JSONResponse(status_code=503, content={"detail": {"code": "EDITING_RUNTIME_UNAVAILABLE"}})
            except RuntimeError as error:
                return JSONResponse(status_code=503, content={"detail": {"code": str(error)}})

        return handler


async def _read_render_task(
    dependencies: EditingDependencies,
    request: Request,
    *,
    project_id: str,
    task_id: str,
) -> RenderTask:
    context = await dependencies.access_context(request)
    if not context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
        raise PermissionDenied()
    task = await dependencies.require_render_repository().get_render_task(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        task_id=task_id,
    )
    if task is None or task.project_id != project_id:
        raise RenderTaskNotFound()
    if dependencies.render_service is not None:
        task = await dependencies.render_service.reconcile_timeout(context, task_id=task.task_id)
    return task


def create_editing_router(dependencies: EditingDependencies | None = None) -> APIRouter:
    dependency_provider: DependencyProvider
    if dependencies is None:
        dependency_provider = _unconfigured_dependencies
    else:

        def configured_dependencies() -> EditingDependencies:
            return dependencies

        dependency_provider = configured_dependencies

    router = APIRouter(prefix="/api/v1", tags=["xingjing-editing"], route_class=EditingContractRoute)

    @router.get("/projects/{project_id}/timelines", response_model=list[PreviewSummary])
    async def list_timelines(
        project_id: str,
        request: Request,
        episode_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> list[PreviewSummary]:
        try:
            if offset < 0 or not 1 <= limit <= 100:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "INVALID_PAGE"})
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            return list(await resolved.timeline_service.list_previews(
                context, project_id=project_id, episode_id=episode_id, offset=offset, limit=limit
            ))
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/timeline-sources", response_model=list[TimelineSourceSummary])
    async def list_timeline_sources(
        project_id: str,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> list[TimelineSourceSummary]:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            sources = await resolved.timeline_service.list_source_versions(context, project_id=project_id)
            return [TimelineSourceSummary(
                asset_id=source.asset_id,
                version_id=source.version_id,
                duration_ms=source.duration_ms,
                content_sha256=source.content_sha256,
                media_kind=source.media_kind.value,
            ) for source in sources]
        except EditingError as error:
            raise _http_error(error) from error

    @router.post("/projects/{project_id}/timelines", response_model=PreviewSummary, status_code=status.HTTP_201_CREATED)
    async def create_timeline(
        project_id: str,
        body: CreateTimelineBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> PreviewSummary:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            timeline = await resolved.timeline_service.create_timeline(
                context, body.to_command(project_id=project_id), idempotency_key=idempotency_key
            )
            return build_preview_summary(timeline)
        except EditingError as error:
            raise _http_error(error) from error

    @router.post("/projects/{project_id}/timelines/{timeline_id}/clips/replace", response_model=PreviewSummary)
    async def replace_timeline_clip(
        project_id: str,
        timeline_id: str,
        body: ReplaceClipBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> PreviewSummary:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            timeline = await resolved.timeline_service.replace_clip(
                context, body.to_command(project_id=project_id, timeline_id=timeline_id), idempotency_key=idempotency_key
            )
            return await resolved.timeline_service.get_preview(context, project_id=project_id, timeline_id=timeline.timeline_id)
        except EditingError as error:
            raise _http_error(error) from error

    @router.put("/projects/{project_id}/timelines/{timeline_id}/clips/{clip_id}", response_model=PreviewSummary)
    async def update_timeline_clip(
        project_id: str,
        timeline_id: str,
        clip_id: str,
        body: UpdateClipBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> PreviewSummary:
        if body.clip_id != clip_id:
            raise HTTPException(status_code=422, detail={"code": "CLIP_ID_MISMATCH"})
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            timeline = await resolved.timeline_service.update_clip(
                context,
                body.to_command(project_id=project_id, timeline_id=timeline_id),
                idempotency_key=idempotency_key,
            )
            return build_preview_summary(timeline)
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/timelines/{timeline_id}", response_model=PreviewSummary)
    async def read_timeline(
        project_id: str,
        timeline_id: str,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> PreviewSummary:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            return await resolved.timeline_service.get_preview(
                context,
                project_id=project_id,
                timeline_id=timeline_id,
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/timelines/{timeline_id}/snapshot", response_model=TimelineVersion)
    async def read_timeline_snapshot(
        project_id: str,
        timeline_id: str,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> TimelineVersion:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            return await resolved.timeline_service.get_timeline(
                context, project_id=project_id, timeline_id=timeline_id
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.put("/projects/{project_id}/timelines/{timeline_id}/output-policy", response_model=TimelineVersion)
    async def update_output_policy(
        project_id: str,
        timeline_id: str,
        body: UpdateOutputPolicyBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> TimelineVersion:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            return await resolved.timeline_service.update_output_policy(
                context,
                body.to_command(project_id=project_id, timeline_id=timeline_id),
                idempotency_key=idempotency_key,
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.post("/projects/{project_id}/timelines/{timeline_id}/tracks", response_model=TimelineVersion)
    async def add_timeline_track(
        project_id: str,
        timeline_id: str,
        body: AddTrackBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> TimelineVersion:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            return await resolved.timeline_service.add_track(
                context,
                body.to_command(project_id=project_id, timeline_id=timeline_id),
                idempotency_key=idempotency_key,
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.post(
        "/projects/{project_id}/render-tasks",
        response_model=RenderTask,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_render(
        project_id: str,
        body: SubmitRenderBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> RenderTask:
        try:
            await resolved.authorize_project(request, project_id)
            await resolved.validate_render_input(request, project_id, body.timeline_id, body.timeline_version_id)
            context = await resolved.access_context(request)
            service = resolved.require_render_service()
            task = await service.request_render(
                context,
                body.to_command(project_id=project_id),
                idempotency_key=idempotency_key,
            )
            if task.status is RenderStatus.QUEUED:
                task = await service.start_render(
                    context,
                    task_id=task.task_id,
                    expected_task_revision=task.task_revision,
                )
            return task
        except EditingError as error:
            raise _http_error(error) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_REQUEST", "message": str(error)},
            ) from error

    @router.get("/projects/{project_id}/render-tasks/{task_id}", response_model=RenderTask)
    async def read_render_task(
        project_id: str,
        task_id: str,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> RenderTask:
        try:
            await resolved.authorize_project(request, project_id)
            return await _read_render_task(
                resolved,
                request,
                project_id=project_id,
                task_id=task_id,
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/render-tasks", response_model=list[RenderTask])
    async def list_render_tasks(
        project_id: str,
        request: Request,
        offset: int = 0,
        limit: int = 50,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> list[RenderTask]:
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "INVALID_PAGE"})
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            if not context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
                raise PermissionDenied()
            return list(
                await resolved.require_render_repository().list_render_tasks(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    offset=offset,
                    limit=limit,
                )
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/final-video-versions")
    async def list_final_video_versions(
        project_id: str,
        request: Request,
        final_video_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> list[FinalVideoVersion]:
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "INVALID_PAGE"})
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            if not context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
                raise PermissionDenied()
            return list(
                await resolved.require_render_repository().list_final_video_versions(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    final_video_id=final_video_id,
                    offset=offset,
                    limit=limit,
                )
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/final-video-versions/{version_id}")
    async def read_final_video_version(
        project_id: str,
        version_id: str,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> FinalVideoVersion:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            if not context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
                raise PermissionDenied()
            version = await resolved.require_render_repository().get_final_video_version(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                version_id=version_id,
            )
            if version is None:
                raise HTTPException(status_code=404, detail={"code": "FINAL_VIDEO_VERSION_NOT_FOUND"})
            return version
        except EditingError as error:
            raise _http_error(error) from error

    @router.get(
        "/projects/{project_id}/final-videos/{final_video_id}/selection",
        response_model=FinalVideoSelection | None,
    )
    async def read_final_video_selection(
        project_id: str,
        final_video_id: str,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> FinalVideoSelection | None:
        await resolved.authorize_project(request, project_id)
        context = await resolved.access_context(request)
        if not context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
            raise _http_error(PermissionDenied())
        return await resolved.require_render_repository().get_final_video_selection(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=project_id,
            final_video_id=final_video_id,
        )

    @router.put(
        "/projects/{project_id}/final-videos/{final_video_id}/selection",
        response_model=FinalVideoSelection,
    )
    async def select_final_video_version(
        project_id: str,
        final_video_id: str,
        body: SelectFinalVideoVersionBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> FinalVideoSelection:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            return await resolved.require_render_service().select_final_version(
                context,
                project_id=project_id,
                final_video_id=final_video_id,
                version_id=body.version_id,
                expected_revision=body.expected_revision,
                idempotency_key=idempotency_key,
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/editing-audit-events", response_model=list[AuditEvent])
    async def list_editing_audit_events(
        project_id: str,
        request: Request,
        request_id: str | None = None,
        actor_id: str | None = None,
        object_id: str | None = None,
        action: str | None = None,
        offset: int = 0,
        limit: int = 50,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> list[AuditEvent]:
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "INVALID_PAGE"})
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            if not context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
                raise PermissionDenied()
            return list(
                await resolved.require_audit_reader().list_events(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    request_id=request_id,
                    actor_id=actor_id,
                    object_id=object_id,
                    action=action,
                    offset=offset,
                    limit=limit,
                )
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/final-delivery-snapshot", response_model=FinalDeliverySnapshot)
    async def read_final_delivery_snapshot(
        project_id: str,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> FinalDeliverySnapshot:
        try:
            await resolved.authorize_project(request, project_id)
            context = await resolved.access_context(request)
            if not context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
                raise PermissionDenied()
            versions = await resolved.require_render_repository().list_final_video_versions(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                final_video_id=None,
                offset=0,
                limit=1,
            )
            if not versions:
                raise HTTPException(status_code=404, detail={"code": "FINAL_VIDEO_VERSION_NOT_FOUND"})
            latest = versions[0]
            selection = await resolved.require_render_repository().get_final_video_selection(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                final_video_id=latest.final_video_id,
            )
            if selection is None:
                raise HTTPException(status_code=409, detail={"code": "FINAL_VIDEO_VERSION_NOT_SELECTED"})
            version = await resolved.require_render_repository().get_final_video_version(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                version_id=selection.selected_version_id,
            )
            if version is None:
                raise HTTPException(status_code=409, detail={"code": "SELECTED_FINAL_VIDEO_VERSION_MISSING"})
            timeline = await resolved.timeline_service.get_timeline(
                context,
                project_id=project_id,
                timeline_id=version.timeline_id,
            )
            if timeline.version_id != version.timeline_version_id:
                timeline = await resolved.require_render_repository().get_timeline_version(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    timeline_id=version.timeline_id,
                    version_id=version.timeline_version_id,
                )
            if timeline is None:
                raise HTTPException(status_code=409, detail={"code": "DELIVERY_TIMELINE_VERSION_MISSING"})
            digest = canonical_sha256(
                {
                    "version_id": version.version_id,
                    "content_sha256": version.output.content_sha256,
                    "timeline_version_id": version.timeline_version_id,
                    "output_policy": timeline.output_policy.model_dump(mode="json"),
                }
            )
            return FinalDeliverySnapshot(
                project_id=project_id,
                final_video_version=version,
                output_policy=timeline.output_policy,
                delivery_digest=digest,
            )
        except EditingError as error:
            raise _http_error(error) from error

    @router.post(
        "/projects/{project_id}/render-tasks/{task_id}/cancel",
        response_model=RenderTask,
    )
    async def cancel_render_task(
        project_id: str,
        task_id: str,
        body: CancelRenderBody,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> RenderTask:
        try:
            await resolved.authorize_project(request, project_id)
            await _read_render_task(
                resolved,
                request,
                project_id=project_id,
                task_id=task_id,
            )
            context = await resolved.access_context(request)
            return await resolved.require_render_service().cancel_render(
                context,
                task_id=task_id,
                expected_task_revision=body.expected_task_revision,
                reason=body.reason,
            )
        except EditingError as error:
            raise _http_error(error) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_REQUEST", "message": str(error)},
            ) from error

    @router.post("/render-tasks/{task_id}/callbacks", response_model=RenderCallbackResult)
    async def receive_render_callback(
        task_id: str,
        body: ProviderRenderCallbackBody,
        request: Request,
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> RenderCallbackResult:
        context = await resolved.callback_context(request)
        try:
            return await resolved.require_render_service().handle_callback(
                context,
                task_id=task_id,
                expected_task_revision=body.expected_task_revision,
                callback=body.to_callback(),
            )
        except EditingError as error:
            raise _http_error(error) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_REQUEST", "message": str(error)},
            ) from error

    @router.post(
        "/projects/{project_id}/render-tasks/{task_id}/retry",
        response_model=RenderTask,
    )
    async def retry_render_task(
        project_id: str,
        task_id: str,
        body: RetryRenderBody,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
        resolved: EditingDependencies = Depends(dependency_provider),
    ) -> RenderTask:
        try:
            await resolved.authorize_project(request, project_id)
            await _read_render_task(
                resolved,
                request,
                project_id=project_id,
                task_id=task_id,
            )
            context = await resolved.access_context(request)
            service = resolved.require_render_service()
            task = await service.retry_render(
                context,
                task_id=task_id,
                expected_task_revision=body.expected_task_revision,
                idempotency_key=idempotency_key,
            )
            if task.status is RenderStatus.RETRYING:
                task = await service.start_render(
                    context,
                    task_id=task.task_id,
                    expected_task_revision=task.task_revision,
                )
            return task
        except EditingError as error:
            raise _http_error(error) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_REQUEST", "message": str(error)},
            ) from error

    return router


def create_unavailable_editing_router(code: str) -> APIRouter:
    """Keep M08 public paths explicit when production dependencies are absent."""
    unavailable_router = APIRouter(prefix="/api/v1", tags=["xingjing-editing"], route_class=EditingContractRoute)

    async def unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": {"code": code}})

    unavailable_router.add_api_route("/projects/{project_id}/timelines/{timeline_id}", unavailable, methods=["GET"])
    unavailable_router.add_api_route("/projects/{project_id}/timelines/{timeline_id}/snapshot", unavailable, methods=["GET"])
    unavailable_router.add_api_route(
        "/projects/{project_id}/timelines", unavailable, methods=["GET"], operation_id="unavailable_timelines_get"
    )
    unavailable_router.add_api_route(
        "/projects/{project_id}/timelines", unavailable, methods=["POST"], operation_id="unavailable_timelines_post"
    )
    unavailable_router.add_api_route("/projects/{project_id}/timeline-sources", unavailable, methods=["GET"])
    unavailable_router.add_api_route("/projects/{project_id}/timelines/{timeline_id}/clips/replace", unavailable, methods=["POST"])
    unavailable_router.add_api_route(
        "/projects/{project_id}/timelines/{timeline_id}/clips/{clip_id}", unavailable, methods=["PUT"]
    )
    unavailable_router.add_api_route(
        "/projects/{project_id}/timelines/{timeline_id}/output-policy", unavailable, methods=["PUT"]
    )
    unavailable_router.add_api_route(
        "/projects/{project_id}/timelines/{timeline_id}/tracks", unavailable, methods=["POST"]
    )
    unavailable_router.add_api_route("/projects/{project_id}/render-tasks", unavailable, methods=["POST"])
    unavailable_router.add_api_route("/projects/{project_id}/render-tasks", unavailable, methods=["GET"])
    unavailable_router.add_api_route("/projects/{project_id}/render-tasks/{task_id}", unavailable, methods=["GET"])
    unavailable_router.add_api_route("/projects/{project_id}/render-tasks/{task_id}/cancel", unavailable, methods=["POST"])
    unavailable_router.add_api_route("/projects/{project_id}/render-tasks/{task_id}/retry", unavailable, methods=["POST"])
    unavailable_router.add_api_route("/render-tasks/{task_id}/callbacks", unavailable, methods=["POST"])
    unavailable_router.add_api_route("/projects/{project_id}/final-video-versions", unavailable, methods=["GET"])
    unavailable_router.add_api_route(
        "/projects/{project_id}/final-video-versions/{version_id}", unavailable, methods=["GET"]
    )
    unavailable_router.add_api_route(
        "/projects/{project_id}/editing-audit-events", unavailable, methods=["GET"]
    )
    unavailable_router.add_api_route(
        "/projects/{project_id}/final-delivery-snapshot", unavailable, methods=["GET"]
    )
    unavailable_router.add_api_route(
        "/projects/{project_id}/final-videos/{final_video_id}/selection",
        unavailable,
        methods=["GET"],
        operation_id="unavailable_final_video_selection_get",
    )
    unavailable_router.add_api_route(
        "/projects/{project_id}/final-videos/{final_video_id}/selection",
        unavailable,
        methods=["PUT"],
        operation_id="unavailable_final_video_selection_put",
    )
    return unavailable_router


router = create_editing_router()

__all__ = ["create_editing_router", "create_unavailable_editing_router", "router"]
