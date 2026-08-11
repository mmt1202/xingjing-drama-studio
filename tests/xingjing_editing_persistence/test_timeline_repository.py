from __future__ import annotations

import pytest

from server.xingjing_editing.errors import IdempotencyConflict, VersionConflict
from server.xingjing_editing_persistence import SqlAlchemyRenderRepository, SqlAlchemyTimelineRepository

from .conftest import make_timeline


@pytest.mark.asyncio
async def test_create_timeline_round_trips_and_enforces_tenant_workspace_isolation(editing_session_factory) -> None:
    repository = SqlAlchemyTimelineRepository(editing_session_factory)
    timeline = make_timeline()

    stored, created = await repository.create_timeline(
        timeline,
        idempotency_key="timeline-create-1",
        request_fingerprint="b" * 64,
    )

    assert (stored, created) == (timeline, True)
    assert (
        await repository.get_current_timeline(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            timeline_id="timeline-1",
        )
        == timeline
    )
    assert (
        await repository.get_current_timeline(
            tenant_id="tenant-2",
            workspace_id="workspace-1",
            timeline_id="timeline-1",
        )
        is None
    )
    assert (
        await repository.get_current_timeline(
            tenant_id="tenant-1",
            workspace_id="workspace-2",
            timeline_id="timeline-1",
        )
        is None
    )


@pytest.mark.asyncio
async def test_create_timeline_replays_same_request_and_rejects_idempotency_key_reuse(
    editing_session_factory,
) -> None:
    repository = SqlAlchemyTimelineRepository(editing_session_factory)
    first = make_timeline()

    await repository.create_timeline(
        first,
        idempotency_key="timeline-create-1",
        request_fingerprint="b" * 64,
    )
    replay, created = await repository.create_timeline(
        make_timeline(timeline_id="discarded-candidate", version_id="discarded-version"),
        idempotency_key="timeline-create-1",
        request_fingerprint="b" * 64,
    )

    assert (replay, created) == (first, False)
    with pytest.raises(IdempotencyConflict):
        await repository.create_timeline(
            first,
            idempotency_key="timeline-create-1",
            request_fingerprint="c" * 64,
        )


@pytest.mark.asyncio
async def test_append_timeline_uses_cas_replays_idempotently_and_preserves_history(
    editing_session_factory,
) -> None:
    timelines = SqlAlchemyTimelineRepository(editing_session_factory)
    renders = SqlAlchemyRenderRepository(editing_session_factory)
    first = make_timeline()
    second = make_timeline(
        version_id="timeline-version-2",
        revision=2,
        parent_version_id=first.version_id,
        source_version_id="source-version-2",
    )
    await timelines.create_timeline(
        first,
        idempotency_key="timeline-create-1",
        request_fingerprint="b" * 64,
    )

    stored, created = await timelines.append_timeline(
        second,
        expected_revision=1,
        idempotency_key="timeline-append-1",
        request_fingerprint="c" * 64,
    )
    replay, replay_created = await timelines.append_timeline(
        make_timeline(
            version_id="discarded-version",
            revision=2,
            parent_version_id=first.version_id,
            source_version_id="source-version-2",
        ),
        expected_revision=1,
        idempotency_key="timeline-append-1",
        request_fingerprint="c" * 64,
    )

    assert (stored, created) == (second, True)
    assert (replay, replay_created) == (second, False)
    assert (
        await timelines.get_current_timeline(
            tenant_id=first.tenant_id,
            workspace_id=first.workspace_id,
            timeline_id=first.timeline_id,
        )
        == second
    )
    assert (
        await renders.get_timeline_version(
            tenant_id=first.tenant_id,
            workspace_id=first.workspace_id,
            timeline_id=first.timeline_id,
            version_id=first.version_id,
        )
        == first
    )

    stale_successor = make_timeline(
        version_id="timeline-version-stale",
        revision=2,
        parent_version_id=first.version_id,
        source_version_id="source-version-stale",
    )
    with pytest.raises(VersionConflict) as conflict:
        await timelines.append_timeline(
            stale_successor,
            expected_revision=1,
            idempotency_key="timeline-append-stale",
            request_fingerprint="d" * 64,
        )
    assert (conflict.value.expected_version, conflict.value.current_version) == (1, 2)


@pytest.mark.asyncio
async def test_same_identifiers_can_exist_in_another_tenant_workspace_without_cross_project_leakage(
    editing_session_factory,
) -> None:
    repository = SqlAlchemyTimelineRepository(editing_session_factory)
    first = make_timeline(project_id="project-1")
    other_scope = make_timeline(
        tenant_id="tenant-2",
        workspace_id="workspace-2",
        project_id="project-2",
    )

    await repository.create_timeline(
        first,
        idempotency_key="same-key",
        request_fingerprint="b" * 64,
    )
    await repository.create_timeline(
        other_scope,
        idempotency_key="same-key",
        request_fingerprint="c" * 64,
    )

    stored = await repository.get_current_timeline(
        tenant_id="tenant-2",
        workspace_id="workspace-2",
        timeline_id=first.timeline_id,
    )
    assert stored is not None
    assert stored == other_scope
    assert stored.project_id == "project-2"
