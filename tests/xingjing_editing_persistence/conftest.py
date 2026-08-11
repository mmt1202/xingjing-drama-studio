from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_editing.contracts import (
    ClipReference,
    TimelineTrack,
    TimelineVersion,
    TrackKind,
    build_preview_summary,
    canonical_sha256,
    timeline_content_sha256,
)
from server.xingjing_editing.rendering import (
    FinalVideoVersion,
    OutputSummary,
    RenderProfile,
    RenderTask,
    start_render_task,
    succeed_render_task,
)
from server.xingjing_editing_persistence import EditingPersistenceBase


@pytest.fixture
async def editing_session_factory(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'editing.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(EditingPersistenceBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await _drop_schema(engine)


async def _drop_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(EditingPersistenceBase.metadata.drop_all)
    await engine.dispose()


def make_timeline(
    *,
    tenant_id: str = "tenant-1",
    workspace_id: str = "workspace-1",
    project_id: str = "project-1",
    timeline_id: str = "timeline-1",
    version_id: str = "timeline-version-1",
    final_video_id: str = "final-video-1",
    revision: int = 1,
    parent_version_id: str | None = None,
    source_version_id: str = "source-version-1",
    created_at: datetime | None = None,
) -> TimelineVersion:
    tracks = (
        TimelineTrack(
            track_id="video-track",
            kind=TrackKind.VIDEO,
            clips=(
                ClipReference(
                    clip_id="clip-1",
                    shot_id="shot-1",
                    source_asset_id="asset-1",
                    source_version_id=source_version_id,
                    source_sha256="a" * 64,
                    source_in_ms=0,
                    source_out_ms=4_000,
                    timeline_start_ms=0,
                    timeline_end_ms=4_000,
                    volume_milli=1_000,
                    effects={"opacity_milli": 1_000},
                ),
            ),
        ),
    )
    return TimelineVersion(
        timeline_id=timeline_id,
        version_id=version_id,
        final_video_id=final_video_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        project_id=project_id,
        episode_id="episode-1",
        revision=revision,
        parent_version_id=parent_version_id,
        tracks=tracks,
        content_sha256=timeline_content_sha256(project_id=project_id, episode_id="episode-1", tracks=tracks),
        created_by="actor-1",
        created_at=created_at or datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
    )


def make_render_task(
    timeline: TimelineVersion,
    *,
    task_id: str = "render-task-1",
    idempotency_key: str = "render-create-1",
    request_fingerprint: str = "b" * 64,
) -> RenderTask:
    return RenderTask.queued(
        task_id=task_id,
        tenant_id=timeline.tenant_id,
        workspace_id=timeline.workspace_id,
        project_id=timeline.project_id,
        timeline_id=timeline.timeline_id,
        timeline_version_id=timeline.version_id,
        timeline_revision=timeline.revision,
        final_video_id=timeline.final_video_id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        preview=build_preview_summary(timeline),
        profile=RenderProfile(
            container="mp4",
            video_codec="h264",
            audio_codec="aac",
            width=1_920,
            height=1_080,
            frame_rate_milli=25_000,
        ),
        max_attempts=3,
        created_at=timeline.created_at,
        deadline_at=timeline.created_at + timedelta(minutes=30),
    )


def make_running_task(task: RenderTask, *, renderer_job_id: str | None = None) -> RenderTask:
    return start_render_task(
        task,
        renderer_job_id=renderer_job_id or f"renderer-{task.task_id}",
        accepted_at=task.created_at + timedelta(seconds=1),
    )


def make_successful_render(
    running: RenderTask,
    *,
    version_id: str = "final-video-version-1",
    callback_event_id: str = "render-callback-1",
    content_sha256: str = "d" * 64,
) -> tuple[RenderTask, FinalVideoVersion]:
    output = OutputSummary(
        object_key=f"{running.tenant_id}/{running.workspace_id}/renders/{running.task_id}.mp4",
        content_sha256=content_sha256,
        size_bytes=1_024,
        duration_ms=4_000,
        input_snapshot_sha256=running.preview.input_snapshot_sha256,
        composition_sha256=running.preview.composition_sha256,
    )
    deduplication_key = canonical_sha256(
        {
            "request_fingerprint": running.request_fingerprint,
            "content_sha256": content_sha256,
        }
    )
    completed_at = running.updated_at + timedelta(seconds=1)
    version = FinalVideoVersion(
        version_id=version_id,
        final_video_id=running.final_video_id,
        tenant_id=running.tenant_id,
        workspace_id=running.workspace_id,
        project_id=running.project_id,
        timeline_id=running.timeline_id,
        timeline_version_id=running.timeline_version_id,
        render_task_id=running.task_id,
        render_attempt=running.attempt,
        profile=running.profile,
        preview=running.preview,
        output=output,
        deduplication_key=deduplication_key,
        created_at=completed_at,
    )
    succeeded = succeed_render_task(
        running,
        output_version_id=version.version_id,
        event_id=callback_event_id,
        occurred_at=completed_at,
    )
    return succeeded, version
