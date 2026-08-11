from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server.xingjing_editing import (
    AccessContext,
    ClipReference,
    FinalVideoVersion,
    IdempotencyConflict,
    Permission,
    RenderCompletion,
    RendererSubmission,
    RenderProfile,
    RenderService,
    RenderTask,
    SourceMediaVersion,
    StoredRenderObject,
    TimelineService,
    TimelineTrack,
    TimelineVersion,
    TrackKind,
    VersionConflict,
)
from server.xingjing_editing.contracts import timeline_content_sha256
from server.xingjing_editing_http import create_editing_dependencies, create_editing_router

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
OUTPUT_KEY = "tenant-1/workspace-1/renders/final.mp4"


class MutableClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current


class SequenceIds:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def new_id(self, kind: str) -> str:
        current = self._counts.get(kind, 0) + 1
        self._counts[kind] = current
        return f"{kind}-{current}"


class AuditRecorderStub:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record(self, event: object) -> None:
        self.events.append(event)


class MediaCatalogStub:
    async def get_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        version_id: str,
    ) -> SourceMediaVersion | None:
        del tenant_id, workspace_id, version_id
        return None


class EditingRepositoryStub:
    def __init__(self, timeline: TimelineVersion) -> None:
        self.timeline = timeline
        self.tasks_by_key: dict[tuple[str, str, str], tuple[str, RenderTask]] = {}
        self.versions_by_dedupe: dict[tuple[str, str, str], FinalVideoVersion] = {}

    async def create_timeline(
        self,
        timeline: TimelineVersion,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]:
        del idempotency_key, request_fingerprint
        self.timeline = timeline
        return timeline, True

    async def get_current_timeline(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
    ) -> TimelineVersion | None:
        if (tenant_id, workspace_id, timeline_id) != (
            self.timeline.tenant_id,
            self.timeline.workspace_id,
            self.timeline.timeline_id,
        ):
            return None
        return self.timeline

    async def append_timeline(
        self,
        timeline: TimelineVersion,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]:
        del idempotency_key, request_fingerprint
        if self.timeline.revision != expected_revision:
            raise VersionConflict(expected_version=expected_revision, current_version=self.timeline.revision)
        self.timeline = timeline
        return timeline, True

    async def get_timeline_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
        version_id: str,
    ) -> TimelineVersion | None:
        if (tenant_id, workspace_id, timeline_id, version_id) != (
            self.timeline.tenant_id,
            self.timeline.workspace_id,
            self.timeline.timeline_id,
            self.timeline.version_id,
        ):
            return None
        return self.timeline

    async def get_render_task_by_idempotency(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        idempotency_key: str,
    ) -> RenderTask | None:
        existing = self.tasks_by_key.get((tenant_id, workspace_id, idempotency_key))
        return None if existing is None else existing[1]

    async def create_render_task(
        self,
        task: RenderTask,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[RenderTask, bool]:
        key = (task.tenant_id, task.workspace_id, idempotency_key)
        existing = self.tasks_by_key.get(key)
        if existing is not None:
            fingerprint, stored = existing
            if fingerprint != request_fingerprint:
                raise IdempotencyConflict()
            return stored, False
        self.tasks_by_key[key] = (request_fingerprint, task)
        return task, True

    async def get_render_task(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        task_id: str,
    ) -> RenderTask | None:
        for _, task in self.tasks_by_key.values():
            if (task.tenant_id, task.workspace_id, task.task_id) == (tenant_id, workspace_id, task_id):
                return task
        return None

    async def save_render_task(self, task: RenderTask, *, expected_revision: int) -> RenderTask:
        for key, (fingerprint, current) in self.tasks_by_key.items():
            if current.task_id != task.task_id:
                continue
            if current.task_revision != expected_revision:
                raise VersionConflict(expected_version=expected_revision, current_version=current.task_revision)
            self.tasks_by_key[key] = (fingerprint, task)
            return task
        raise AssertionError("render task not found")

    async def complete_render(
        self,
        task: RenderTask,
        version: FinalVideoVersion,
        *,
        expected_revision: int,
    ) -> RenderCompletion:
        current = await self.get_render_task(
            tenant_id=task.tenant_id,
            workspace_id=task.workspace_id,
            task_id=task.task_id,
        )
        assert current is not None
        if current.task_revision != expected_revision:
            raise VersionConflict(expected_version=expected_revision, current_version=current.task_revision)
        key = (version.tenant_id, version.workspace_id, version.deduplication_key)
        stored_version = self.versions_by_dedupe.get(key)
        created = stored_version is None
        if stored_version is None:
            stored_version = version
            self.versions_by_dedupe[key] = version
        stored_task = RenderTask.model_validate(
            task.model_copy(update={"output_version_id": stored_version.version_id}).model_dump()
        )
        await self.save_render_task(stored_task, expected_revision=expected_revision)
        return RenderCompletion(task=stored_task, version=stored_version, version_created=created)


class RendererStub:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, int, str]] = []
        self.cancellations: list[str] = []

    async def submit(
        self,
        *,
        task_id: str,
        attempt: int,
        input_snapshot_sha256: str,
        profile: RenderProfile,
        deadline_at: datetime,
    ) -> RendererSubmission:
        del profile, deadline_at
        self.submissions.append((task_id, attempt, input_snapshot_sha256))
        return RendererSubmission(
            renderer_job_id=f"renderer-job-{attempt}",
            accepted_at=NOW + timedelta(seconds=attempt),
        )

    async def cancel(self, *, renderer_job_id: str) -> None:
        self.cancellations.append(renderer_job_id)


class ObjectStorageStub:
    def __init__(self) -> None:
        self.objects: dict[str, StoredRenderObject] = {}
        self.lookups: list[tuple[str, str, str]] = []

    async def stat(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        object_key: str,
    ) -> StoredRenderObject | None:
        self.lookups.append((tenant_id, workspace_id, object_key))
        return self.objects.get(object_key)


def timeline_version() -> TimelineVersion:
    tracks = (
        TimelineTrack(
            track_id="track-video",
            kind=TrackKind.VIDEO,
            clips=(
                ClipReference(
                    clip_id="clip-1",
                    shot_id="shot-1",
                    source_asset_id="video-asset-1",
                    source_version_id="video-version-1",
                    source_sha256="a" * 64,
                    source_in_ms=0,
                    source_out_ms=4_000,
                    timeline_start_ms=0,
                    timeline_end_ms=4_000,
                    volume_milli=1_000,
                ),
            ),
        ),
    )
    return TimelineVersion(
        timeline_id="timeline-1",
        version_id="timeline-version-4",
        final_video_id="final-video-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        project_id="project-1",
        episode_id="episode-1",
        revision=4,
        parent_version_id="timeline-version-3",
        tracks=tracks,
        content_sha256=timeline_content_sha256(project_id="project-1", episode_id="episode-1", tracks=tracks),
        created_by="editor-1",
        created_at=NOW,
    )


def access_context_resolver(request: Request) -> AccessContext:
    permissions = frozenset(
        Permission(permission) for permission in request.headers.get("X-Permissions", "").split(",") if permission
    )
    return AccessContext(
        tenant_id=request.headers.get("X-Tenant-ID", "tenant-1"),
        workspace_id=request.headers.get("X-Workspace-ID", "workspace-1"),
        actor_id=request.headers.get("X-Actor-ID", "editor-1"),
        request_id=request.headers.get("X-Request-ID", "request-http"),
        permissions=permissions,
    )


class EditingHttpHarness:
    def __init__(self) -> None:
        self.clock = MutableClock()
        self.ids = SequenceIds()
        self.audit = AuditRecorderStub()
        self.repository = EditingRepositoryStub(timeline_version())
        self.renderer = RendererStub()
        self.storage = ObjectStorageStub()
        self.timeline_service = TimelineService(
            repository=self.repository,
            media_catalog=MediaCatalogStub(),
            audit=self.audit,
            ids=self.ids,
            clock=self.clock,
        )
        self.render_service = RenderService(
            repository=self.repository,
            renderer=self.renderer,
            object_storage=self.storage,
            audit=self.audit,
            ids=self.ids,
            clock=self.clock,
        )
        dependencies = create_editing_dependencies(
            timeline_service=self.timeline_service,
            render_service=self.render_service,
            render_repository=self.repository,
            access_context_resolver=access_context_resolver,
            callback_context_resolver=access_context_resolver,
        )
        app = FastAPI()
        app.include_router(create_editing_router(dependencies))
        self.client = TestClient(app)

    @staticmethod
    def headers(*, permissions: str = "final.manage", idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "X-Tenant-ID": "tenant-1",
            "X-Workspace-ID": "workspace-1",
            "X-Actor-ID": "editor-1",
            "X-Request-ID": "request-http",
            "X-Permissions": permissions,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def render_payload(*, width: int = 1_920, expected_revision: int = 4) -> dict[str, object]:
        return {
            "timeline_id": "timeline-1",
            "timeline_version_id": "timeline-version-4",
            "expected_timeline_revision": expected_revision,
            "profile": {
                "container": "mp4",
                "video_codec": "h264",
                "audio_codec": "aac",
                "width": width,
                "height": 1_080,
                "frame_rate_milli": 25_000,
            },
            "deadline_at": (NOW + timedelta(hours=1)).isoformat(),
            "max_attempts": 3,
        }
