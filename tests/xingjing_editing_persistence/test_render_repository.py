from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from server.xingjing_editing.errors import IdempotencyConflict, VersionConflict
from server.xingjing_editing.rendering import RenderFailure, RenderTask, fail_render_task
from server.xingjing_editing_persistence import SqlAlchemyRenderRepository, SqlAlchemyTimelineRepository
from server.xingjing_editing_persistence.models import FinalVideoVersionRow, RenderOutboxRow

from .conftest import (
    make_render_task,
    make_running_task,
    make_successful_render,
    make_timeline,
)


async def _store_timeline(editing_session_factory):
    timeline = make_timeline()
    timelines = SqlAlchemyTimelineRepository(editing_session_factory)
    await timelines.create_timeline(
        timeline,
        idempotency_key="timeline-create-1",
        request_fingerprint="a" * 64,
    )
    return timeline


@pytest.mark.asyncio
async def test_create_render_task_is_database_idempotent_and_scope_isolated(editing_session_factory) -> None:
    timeline = await _store_timeline(editing_session_factory)
    repository = SqlAlchemyRenderRepository(editing_session_factory)
    task = make_render_task(timeline)

    stored, created = await repository.create_render_task(
        task,
        idempotency_key=task.idempotency_key,
        request_fingerprint=task.request_fingerprint,
    )
    replay, replay_created = await repository.create_render_task(
        make_render_task(timeline, task_id="discarded-task"),
        idempotency_key=task.idempotency_key,
        request_fingerprint=task.request_fingerprint,
    )

    assert (stored, created) == (task, True)
    assert (replay, replay_created) == (task, False)
    assert (
        await repository.get_render_task_by_idempotency(
            tenant_id=task.tenant_id,
            workspace_id=task.workspace_id,
            idempotency_key=task.idempotency_key,
        )
        == task
    )
    assert (
        await repository.get_render_task(
            tenant_id="tenant-2",
            workspace_id=task.workspace_id,
            task_id=task.task_id,
        )
        is None
    )
    with pytest.raises(IdempotencyConflict):
        await repository.create_render_task(
            make_render_task(timeline, request_fingerprint="c" * 64),
            idempotency_key=task.idempotency_key,
            request_fingerprint="c" * 64,
        )

    forged = RenderTask.model_validate(
        task.model_copy(
            update={
                "task_id": "forged-project-task",
                "project_id": "project-2",
                "idempotency_key": "forged-project",
                "idempotency_scope": "tenant-1:workspace-1:forged-project",
            }
        ).model_dump()
    )
    with pytest.raises(ValueError, match="跨项目"):
        await repository.create_render_task(
            forged,
            idempotency_key=forged.idempotency_key,
            request_fingerprint=forged.request_fingerprint,
        )


@pytest.mark.asyncio
async def test_save_render_task_uses_cas_and_deduplicates_callback_event_ids(editing_session_factory) -> None:
    timeline = await _store_timeline(editing_session_factory)
    repository = SqlAlchemyRenderRepository(editing_session_factory)
    queued = make_render_task(timeline)
    await repository.create_render_task(
        queued,
        idempotency_key=queued.idempotency_key,
        request_fingerprint=queued.request_fingerprint,
    )
    running = make_running_task(queued)

    assert await repository.save_render_task(running, expected_revision=1) == running
    assert await repository.save_render_task(running, expected_revision=1) == running

    failed = fail_render_task(
        running,
        failure=RenderFailure(code="PROVIDER_BUSY", message="供应商繁忙", retryable=True),
        event_id="callback-event-1",
        occurred_at=running.updated_at + timedelta(seconds=1),
    )
    assert await repository.save_render_task(failed, expected_revision=2) == failed
    assert await repository.save_render_task(failed, expected_revision=2) == failed

    stale = running.model_copy(update={"renderer_job_id": "stale-job"})
    with pytest.raises(VersionConflict) as conflict:
        await repository.save_render_task(stale, expected_revision=2)
    assert (conflict.value.expected_version, conflict.value.current_version) == (2, 3)

    other = make_render_task(timeline, task_id="render-task-2", idempotency_key="render-create-2")
    await repository.create_render_task(
        other,
        idempotency_key=other.idempotency_key,
        request_fingerprint=other.request_fingerprint,
    )
    other_running = make_running_task(other)
    await repository.save_render_task(other_running, expected_revision=1)
    reused_event = fail_render_task(
        other_running,
        failure=RenderFailure(code="PROVIDER_BUSY", message="供应商繁忙", retryable=True),
        event_id="callback-event-1",
        occurred_at=other_running.updated_at + timedelta(seconds=1),
    )
    with pytest.raises(IdempotencyConflict):
        await repository.save_render_task(reused_event, expected_revision=2)


@pytest.mark.asyncio
async def test_complete_render_deduplicates_final_video_version_and_repoints_task(editing_session_factory) -> None:
    timeline = await _store_timeline(editing_session_factory)
    repository = SqlAlchemyRenderRepository(editing_session_factory)
    first = make_render_task(timeline)
    second = make_render_task(timeline, task_id="render-task-2", idempotency_key="render-create-2")
    for task in (first, second):
        await repository.create_render_task(
            task,
            idempotency_key=task.idempotency_key,
            request_fingerprint=task.request_fingerprint,
        )
    first_running = make_running_task(first)
    second_running = make_running_task(second)
    await repository.save_render_task(first_running, expected_revision=1)
    await repository.save_render_task(second_running, expected_revision=1)
    first_succeeded, first_version = make_successful_render(first_running)
    second_succeeded, second_version = make_successful_render(
        second_running,
        version_id="final-video-version-2",
        callback_event_id="render-callback-2",
    )

    first_completion = await repository.complete_render(
        first_succeeded,
        first_version,
        expected_revision=2,
    )
    second_completion = await repository.complete_render(
        second_succeeded,
        second_version,
        expected_revision=2,
    )

    assert first_completion.version_created is True
    assert second_completion.version_created is False
    assert second_completion.version == first_completion.version
    assert second_completion.task.output_version_id == first_completion.version.version_id
    assert (
        await repository.get_render_task(
            tenant_id=second.tenant_id,
            workspace_id=second.workspace_id,
            task_id=second.task_id,
        )
        == second_completion.task
    )
    async with editing_session_factory() as session:
        version_count = await session.scalar(select(func.count()).select_from(FinalVideoVersionRow))
        outbox_count = await session.scalar(select(func.count()).select_from(RenderOutboxRow))
    assert version_count == 1
    assert outbox_count == 2


@pytest.mark.asyncio
async def test_complete_render_rolls_back_task_when_version_identity_conflicts(editing_session_factory) -> None:
    timeline = await _store_timeline(editing_session_factory)
    repository = SqlAlchemyRenderRepository(editing_session_factory)
    first = make_render_task(timeline)
    second = make_render_task(
        timeline,
        task_id="render-task-2",
        idempotency_key="render-create-2",
        request_fingerprint="c" * 64,
    )
    for task in (first, second):
        await repository.create_render_task(
            task,
            idempotency_key=task.idempotency_key,
            request_fingerprint=task.request_fingerprint,
        )
    first_running = make_running_task(first)
    second_running = make_running_task(second)
    await repository.save_render_task(first_running, expected_revision=1)
    await repository.save_render_task(second_running, expected_revision=1)
    first_succeeded, first_version = make_successful_render(first_running)
    await repository.complete_render(first_succeeded, first_version, expected_revision=2)
    conflicting_task, conflicting_version = make_successful_render(
        second_running,
        version_id=first_version.version_id,
        callback_event_id="render-callback-2",
        content_sha256="e" * 64,
    )

    with pytest.raises(IdempotencyConflict):
        await repository.complete_render(
            conflicting_task,
            conflicting_version,
            expected_revision=2,
        )

    assert (
        await repository.get_render_task(
            tenant_id=second.tenant_id,
            workspace_id=second.workspace_id,
            task_id=second.task_id,
        )
        == second_running
    )
    async with editing_session_factory() as session:
        version_count = await session.scalar(select(func.count()).select_from(FinalVideoVersionRow))
        outbox_count = await session.scalar(select(func.count()).select_from(RenderOutboxRow))
    assert version_count == 1
    assert outbox_count == 1
