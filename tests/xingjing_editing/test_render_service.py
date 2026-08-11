from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_editing import (
    AccessContext,
    ClipReference,
    FinalVideoVersion,
    IdempotencyConflict,
    InvalidRenderTransition,
    Permission,
    RenderCallback,
    RenderCallbackDisposition,
    RenderCallbackOutcome,
    RenderCompletion,
    RendererSubmission,
    RenderFailure,
    RenderOutput,
    RenderProfile,
    RenderService,
    RenderStatus,
    RenderTask,
    RequestRenderCommand,
    StoredRenderObject,
    TimelineTrack,
    TimelineVersion,
    TrackKind,
    VersionConflict,
)
from server.xingjing_editing.contracts import canonical_sha256, timeline_content_sha256

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequenceIds:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def new_id(self, kind: str) -> str:
        del kind
        return next(self._values)


class AuditRecorderStub:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record(self, event: object) -> None:
        self.events.append(event)


class RenderRepositoryStub:
    def __init__(self, timeline: TimelineVersion) -> None:
        self.timeline = timeline
        self.tasks_by_key: dict[tuple[str, str, str], tuple[str, RenderTask]] = {}
        self.versions_by_dedupe: dict[tuple[str, str, str], FinalVideoVersion] = {}

    async def get_timeline_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
        version_id: str,
    ) -> TimelineVersion | None:
        if (
            self.timeline.tenant_id,
            self.timeline.workspace_id,
            self.timeline.timeline_id,
            self.timeline.version_id,
        ) != (tenant_id, workspace_id, timeline_id, version_id):
            return None
        return self.timeline

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
            existing_fingerprint, existing_task = existing
            if existing_fingerprint != request_fingerprint:
                raise IdempotencyConflict()
            return existing_task, False
        self.tasks_by_key[key] = (request_fingerprint, task)
        return task, True

    async def get_render_task_by_idempotency(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        idempotency_key: str,
    ) -> RenderTask | None:
        existing = self.tasks_by_key.get((tenant_id, workspace_id, idempotency_key))
        return None if existing is None else existing[1]

    async def get_render_task(self, *, tenant_id: str, workspace_id: str, task_id: str) -> RenderTask | None:
        for _, task in self.tasks_by_key.values():
            if (task.tenant_id, task.workspace_id, task.task_id) == (tenant_id, workspace_id, task_id):
                return task
        return None

    async def save_render_task(self, task: RenderTask, *, expected_revision: int) -> RenderTask:
        for key, (fingerprint, current) in self.tasks_by_key.items():
            if current.task_id == task.task_id:
                assert current.task_revision == expected_revision
                self.tasks_by_key[key] = (fingerprint, task)
                return task
        raise AssertionError("task not found")

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
        assert current.task_revision == expected_revision
        key = (version.tenant_id, version.workspace_id, version.deduplication_key)
        stored_version = self.versions_by_dedupe.get(key)
        created = stored_version is None
        if stored_version is None:
            stored_version = version
            self.versions_by_dedupe[key] = stored_version
        stored_task = RenderTask.model_validate(
            task.model_copy(update={"output_version_id": stored_version.version_id}).model_dump()
        )
        await self.save_render_task(stored_task, expected_revision=expected_revision)
        return RenderCompletion(task=stored_task, version=stored_version, version_created=created)


class SaveFailsOnceRenderRepository(RenderRepositoryStub):
    def __init__(self, timeline: TimelineVersion) -> None:
        super().__init__(timeline)
        self._fail_once = True

    async def save_render_task(self, task: RenderTask, *, expected_revision: int) -> RenderTask:
        if self._fail_once:
            self._fail_once = False
            raise VersionConflict(expected_version=expected_revision, current_version=expected_revision + 1)
        return await super().save_render_task(task, expected_revision=expected_revision)

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
        assert current.task_revision == expected_revision
        key = (version.tenant_id, version.workspace_id, version.deduplication_key)
        stored_version = self.versions_by_dedupe.get(key)
        created = stored_version is None
        if stored_version is None:
            stored_version = version
            self.versions_by_dedupe[key] = stored_version
        stored_task = task.model_copy(update={"output_version_id": stored_version.version_id})
        stored_task = RenderTask.model_validate(stored_task.model_dump())
        await self.save_render_task(stored_task, expected_revision=expected_revision)
        return RenderCompletion(task=stored_task, version=stored_version, version_created=created)


class RendererStub:
    def __init__(self, accepted_times: tuple[datetime, ...] = (NOW + timedelta(seconds=1),)) -> None:
        self.submissions: list[tuple[str, int, str]] = []
        self.cancellations: list[str] = []
        self._accepted_times = iter(accepted_times)

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
        return RendererSubmission(renderer_job_id=f"renderer-job-{attempt}", accepted_at=next(self._accepted_times))

    async def cancel(self, *, renderer_job_id: str) -> None:
        self.cancellations.append(renderer_job_id)


class ObjectStorageStub:
    def __init__(self) -> None:
        self.lookups: list[tuple[str, str, str]] = []

    async def stat(self, *, tenant_id: str, workspace_id: str, object_key: str) -> StoredRenderObject | None:
        self.lookups.append((tenant_id, workspace_id, object_key))
        if object_key != "tenant-1/workspace-1/renders/output.mp4":
            return None
        return StoredRenderObject(
            object_key=object_key,
            content_sha256="c" * 64,
            size_bytes=8_192,
            duration_ms=4_000,
        )


def timeline_version() -> TimelineVersion:
    tracks = (
        TimelineTrack(
            track_id="track-video-main",
            kind=TrackKind.VIDEO,
            clips=(
                ClipReference(
                    clip_id="clip-1",
                    shot_id="shot-1",
                    source_asset_id="video-asset-1",
                    source_version_id="video-version-5",
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


def context() -> AccessContext:
    return AccessContext(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        actor_id="editor-1",
        request_id="request-render",
        permissions=frozenset({Permission.FINAL_MANAGE}),
    )


def render_command(*, width: int = 1_920) -> RequestRenderCommand:
    return RequestRenderCommand(
        project_id="project-1",
        timeline_id="timeline-1",
        timeline_version_id="timeline-version-4",
        expected_timeline_revision=4,
        profile=RenderProfile(
            container="mp4",
            video_codec="h264",
            audio_codec="aac",
            width=width,
            height=1_080,
            frame_rate_milli=25_000,
        ),
        deadline_at=NOW + timedelta(hours=1),
        max_attempts=3,
    )


@pytest.mark.asyncio
async def test_render_request_is_durable_idempotent_and_does_not_forge_success() -> None:
    repository = RenderRepositoryStub(timeline_version())
    audit = AuditRecorderStub()
    service = RenderService(
        repository=repository,
        renderer=RendererStub(),
        object_storage=ObjectStorageStub(),
        audit=audit,
        ids=SequenceIds("render-task-1", "audit-render-request"),
        clock=FixedClock(),
    )

    first = await service.request_render(context(), render_command(), idempotency_key="render-timeline-v4")
    duplicate = await service.request_render(context(), render_command(), idempotency_key="render-timeline-v4")

    assert duplicate == first
    assert first.status is RenderStatus.QUEUED
    assert first.output_version_id is None
    assert first.timeline_version_id == "timeline-version-4"
    assert first.preview.input_snapshot_sha256 == canonical_sha256(
        {
            "timeline_id": "timeline-1",
            "timeline_version_id": "timeline-version-4",
            "timeline_revision": 4,
            "composition_sha256": first.preview.composition_sha256,
            "tracks": [
                {
                    "track_id": "track-video-main",
                    "kind": "video",
                    "clips": [
                        {
                            "clip_id": "clip-1",
                            "shot_id": "shot-1",
                            "source_asset_id": "video-asset-1",
                            "source_version_id": "video-version-5",
                            "source_sha256": "a" * 64,
                        }
                    ],
                }
            ],
        }
    )
    assert RenderTask.model_validate_json(first.model_dump_json()) == first
    assert len(repository.tasks_by_key) == 1
    assert len(audit.events) == 1

    with pytest.raises(IdempotencyConflict):
        await service.request_render(context(), render_command(width=1_280), idempotency_key="render-timeline-v4")


@pytest.mark.asyncio
async def test_start_render_submits_through_idempotent_gateway_and_only_marks_running() -> None:
    repository = RenderRepositoryStub(timeline_version())
    renderer = RendererStub()
    service = RenderService(
        repository=repository,
        renderer=renderer,
        object_storage=ObjectStorageStub(),
        audit=AuditRecorderStub(),
        ids=SequenceIds("render-task-1", "audit-request", "audit-start"),
        clock=FixedClock(),
    )
    queued = await service.request_render(context(), render_command(), idempotency_key="render-start-1")

    running = await service.start_render(context(), task_id=queued.task_id, expected_task_revision=1)

    assert running.status is RenderStatus.RUNNING
    assert running.attempt == 1
    assert running.task_revision == 2
    assert running.renderer_job_id == "renderer-job-1"
    assert running.output_version_id is None
    assert renderer.submissions == [(queued.task_id, 1, queued.preview.input_snapshot_sha256)]


@pytest.mark.asyncio
async def test_success_callback_requires_storage_evidence_and_deduplicates_final_version() -> None:
    repository = RenderRepositoryStub(timeline_version())
    storage = ObjectStorageStub()
    service = RenderService(
        repository=repository,
        renderer=RendererStub(),
        object_storage=storage,
        audit=AuditRecorderStub(),
        ids=SequenceIds("render-task-1", "audit-request", "audit-start", "final-version-1", "audit-complete"),
        clock=FixedClock(),
    )
    queued = await service.request_render(context(), render_command(), idempotency_key="render-complete-1")
    running = await service.start_render(context(), task_id=queued.task_id, expected_task_revision=1)
    callback = RenderCallback(
        event_id="callback-success-1",
        renderer_job_id="renderer-job-1",
        attempt=1,
        outcome=RenderCallbackOutcome.SUCCEEDED,
        occurred_at=NOW + timedelta(seconds=2),
        output=RenderOutput(
            object_key="tenant-1/workspace-1/renders/output.mp4",
            content_sha256="c" * 64,
            rendered_input_snapshot_sha256=running.preview.input_snapshot_sha256,
            rendered_composition_sha256=running.preview.composition_sha256,
        ),
    )

    completed = await service.handle_callback(
        context(),
        task_id=running.task_id,
        expected_task_revision=2,
        callback=callback,
    )

    assert completed.disposition is RenderCallbackDisposition.APPLIED
    assert completed.task.status is RenderStatus.SUCCEEDED
    assert completed.task.output_version_id == "final-version-1"
    assert completed.version is not None
    assert completed.version.output.input_snapshot_sha256 == completed.version.preview.input_snapshot_sha256
    assert completed.version.output.composition_sha256 == completed.version.preview.composition_sha256
    assert completed.version.output.content_sha256 == "c" * 64
    assert FinalVideoVersion.model_validate_json(completed.version.model_dump_json()) == completed.version
    assert storage.lookups == [("tenant-1", "workspace-1", "tenant-1/workspace-1/renders/output.mp4")]
    assert len(repository.versions_by_dedupe) == 1

    duplicate = await service.handle_callback(
        context(),
        task_id=running.task_id,
        expected_task_revision=3,
        callback=callback,
    )
    assert duplicate.disposition is RenderCallbackDisposition.DUPLICATE
    assert len(repository.versions_by_dedupe) == 1


@pytest.mark.asyncio
async def test_running_cancel_is_two_phase_and_late_success_callback_cannot_resurrect_task() -> None:
    repository = RenderRepositoryStub(timeline_version())
    renderer = RendererStub()
    service = RenderService(
        repository=repository,
        renderer=renderer,
        object_storage=ObjectStorageStub(),
        audit=AuditRecorderStub(),
        ids=SequenceIds("render-task-1", "audit-request", "audit-start", "audit-cancelling", "audit-cancelled"),
        clock=FixedClock(),
    )
    queued = await service.request_render(context(), render_command(), idempotency_key="render-cancel-1")
    running = await service.start_render(context(), task_id=queued.task_id, expected_task_revision=1)

    cancelling = await service.cancel_render(
        context(),
        task_id=running.task_id,
        expected_task_revision=2,
        reason="导演停止当前合成",
    )
    assert cancelling.status is RenderStatus.CANCELLING
    assert renderer.cancellations == ["renderer-job-1"]

    late_success = await service.handle_callback(
        context(),
        task_id=running.task_id,
        expected_task_revision=3,
        callback=RenderCallback(
            event_id="late-success-after-cancel",
            renderer_job_id="renderer-job-1",
            attempt=1,
            outcome=RenderCallbackOutcome.SUCCEEDED,
            occurred_at=NOW + timedelta(seconds=2),
            output=RenderOutput(
                object_key="tenant-1/workspace-1/renders/output.mp4",
                content_sha256="c" * 64,
                rendered_input_snapshot_sha256=running.preview.input_snapshot_sha256,
                rendered_composition_sha256=running.preview.composition_sha256,
            ),
        ),
    )
    assert late_success.disposition is RenderCallbackDisposition.IGNORED_LATE
    assert late_success.task.status is RenderStatus.CANCELLING

    cancelled = await service.handle_callback(
        context(),
        task_id=running.task_id,
        expected_task_revision=3,
        callback=RenderCallback(
            event_id="cancel-confirmed",
            renderer_job_id="renderer-job-1",
            attempt=1,
            outcome=RenderCallbackOutcome.CANCELLED,
            occurred_at=NOW + timedelta(seconds=2),
        ),
    )
    assert cancelled.disposition is RenderCallbackDisposition.APPLIED
    assert cancelled.task.status is RenderStatus.CANCELLED
    with pytest.raises(InvalidRenderTransition):
        await service.cancel_render(
            context(),
            task_id=running.task_id,
            expected_task_revision=4,
            reason="重复取消",
        )


@pytest.mark.asyncio
async def test_retryable_failure_requeues_once_and_rejects_old_attempt_callback() -> None:
    repository = RenderRepositoryStub(timeline_version())
    renderer = RendererStub((NOW + timedelta(seconds=1), NOW + timedelta(seconds=4)))
    service = RenderService(
        repository=repository,
        renderer=renderer,
        object_storage=ObjectStorageStub(),
        audit=AuditRecorderStub(),
        ids=SequenceIds(
            "render-task-1",
            "audit-request",
            "audit-start-1",
            "audit-failed",
            "audit-retry",
            "audit-start-2",
        ),
        clock=FixedClock(),
    )
    queued = await service.request_render(context(), render_command(), idempotency_key="render-retry-1")
    running = await service.start_render(context(), task_id=queued.task_id, expected_task_revision=1)
    failed = await service.handle_callback(
        context(),
        task_id=running.task_id,
        expected_task_revision=2,
        callback=RenderCallback(
            event_id="render-failed-1",
            renderer_job_id="renderer-job-1",
            attempt=1,
            outcome=RenderCallbackOutcome.FAILED,
            occurred_at=NOW + timedelta(seconds=2),
            failure=RenderFailure(code="PROVIDER_BUSY", message="供应商繁忙", retryable=True),
        ),
    )
    assert failed.task.status is RenderStatus.FAILED

    retrying = await service.retry_render(
        context(),
        task_id=running.task_id,
        expected_task_revision=3,
        idempotency_key="retry-render-1",
    )
    repeated = await service.retry_render(
        context(),
        task_id=running.task_id,
        expected_task_revision=3,
        idempotency_key="retry-render-1",
    )
    assert retrying.status is RenderStatus.RETRYING
    assert retrying.task_revision == 4
    assert repeated == retrying

    running_again = await service.start_render(context(), task_id=running.task_id, expected_task_revision=4)
    assert (running_again.status, running_again.attempt, running_again.renderer_job_id) == (
        RenderStatus.RUNNING,
        2,
        "renderer-job-2",
    )
    old_callback = await service.handle_callback(
        context(),
        task_id=running.task_id,
        expected_task_revision=5,
        callback=RenderCallback(
            event_id="old-attempt-success",
            renderer_job_id="renderer-job-1",
            attempt=1,
            outcome=RenderCallbackOutcome.SUCCEEDED,
            occurred_at=NOW + timedelta(seconds=5),
            output=RenderOutput(
                object_key="tenant-1/workspace-1/renders/output.mp4",
                content_sha256="c" * 64,
                rendered_input_snapshot_sha256=running.preview.input_snapshot_sha256,
                rendered_composition_sha256=running.preview.composition_sha256,
            ),
        ),
    )
    assert old_callback.disposition is RenderCallbackDisposition.IGNORED_STALE_ATTEMPT
    assert old_callback.task == running_again


@pytest.mark.asyncio
async def test_retrying_start_after_persistence_failure_reuses_renderer_submission_identity() -> None:
    repository = SaveFailsOnceRenderRepository(timeline_version())
    renderer = RendererStub((NOW + timedelta(seconds=1), NOW + timedelta(seconds=1)))
    service = RenderService(
        repository=repository,
        renderer=renderer,
        object_storage=ObjectStorageStub(),
        audit=AuditRecorderStub(),
        ids=SequenceIds("render-task-1", "audit-request", "audit-start"),
        clock=FixedClock(),
    )
    queued = await service.request_render(context(), render_command(), idempotency_key="render-recover-1")

    with pytest.raises(VersionConflict):
        await service.start_render(context(), task_id=queued.task_id, expected_task_revision=1)

    recovered = await service.start_render(context(), task_id=queued.task_id, expected_task_revision=1)
    assert recovered.status is RenderStatus.RUNNING
    assert recovered.renderer_job_id == "renderer-job-1"
    assert renderer.submissions == [
        (queued.task_id, 1, queued.preview.input_snapshot_sha256),
        (queued.task_id, 1, queued.preview.input_snapshot_sha256),
    ]
