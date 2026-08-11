from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server.xingjing_storyboard.errors import IdempotencyConflict
from server.xingjing_storyboard.models import (
    GenerationStatus,
    Shot,
    Storyboard,
    StoryboardStatus,
    StoryboardVersion,
    storyboard_version_digest,
)
from server.xingjing_storyboard.ports import AuditEvent, StoryboardScope
from server.xingjing_storyboard_persistence import Base, SqlAlchemyStoryboardRepository


@pytest.fixture
def repository() -> SqlAlchemyStoryboardRepository:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return SqlAlchemyStoryboardRepository(sessionmaker(engine, expire_on_commit=False))


def _storyboard(scope: StoryboardScope, *, storyboard_id: str = "storyboard-stable-1") -> Storyboard:
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    shot = Shot(
        shot_id="shot-stable-1",
        position=1,
        shot_number="S01",
        shot_size="medium",
        camera_movement="static",
        dialogue="",
        duration_ms=1_000,
        prompt="cinematic",
        model_strategy="balanced",
        cost_tier="standard",
        asset_references=(),
        selected_media_id=None,
        generation_status=GenerationStatus.NOT_STARTED,
    )
    version = StoryboardVersion(
        number=1,
        action="storyboard.created",
        actor_id="actor-1",
        occurred_at=now,
        shots=(shot,),
        digest=storyboard_version_digest((shot,)),
    )
    return Storyboard(
        storyboard_id=storyboard_id,
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        episode_id="episode-1",
        version=1,
        status=StoryboardStatus.DRAFT,
        shots=(shot,),
        versions=(version,),
        created_at=now,
        updated_at=now,
    )


def _audit(scope: StoryboardScope, *, object_id: str, action: str = "storyboard.created") -> AuditEvent:
    return AuditEvent(
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        request_id="request-1",
        actor_id="actor-1",
        action=action,
        object_id=object_id,
        result="succeeded",
        occurred_at=datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
        before={},
        after={"version": 1},
    )


def _next_storyboard(storyboard: Storyboard, *, dialogue: str) -> Storyboard:
    shot = replace(storyboard.shots[0], dialogue=dialogue)
    version_number = storyboard.version + 1
    version = StoryboardVersion(
        number=version_number,
        action="storyboard.batch_edited",
        actor_id="actor-1",
        occurred_at=storyboard.updated_at,
        shots=(shot,),
        digest=storyboard_version_digest((shot,)),
    )
    return replace(
        storyboard,
        version=version_number,
        shots=(shot,),
        versions=(*storyboard.versions, version),
    )


@pytest.mark.uses_db
def test_commit_preserves_stable_ids_and_hides_storyboard_outside_full_scope(
    repository: SqlAlchemyStoryboardRepository,
) -> None:
    owner = StoryboardScope("tenant-a", "workspace-a", "project-a")
    same_workspace_other_project = StoryboardScope("tenant-a", "workspace-a", "project-b")
    other_workspace = StoryboardScope("tenant-a", "workspace-b", "project-a")
    storyboard = _storyboard(owner)

    outcome = repository.commit(
        owner,
        storyboard,
        expected_version=None,
        idempotency_key="create-1",
        fingerprint="fingerprint-create-1",
        audit=_audit(owner, object_id=storyboard.storyboard_id),
    )

    assert outcome.storyboard.storyboard_id == "storyboard-stable-1"
    assert outcome.storyboard.shots[0].shot_id == "shot-stable-1"
    assert repository.get(owner, storyboard.storyboard_id) == storyboard
    assert repository.list(owner) == (storyboard,)
    assert repository.get(same_workspace_other_project, storyboard.storyboard_id) is None
    assert repository.get(other_workspace, storyboard.storyboard_id) is None
    assert repository.list(same_workspace_other_project) == ()


@pytest.mark.uses_db
def test_commit_replays_once_per_scope_and_fingerprint_without_duplicate_audit(
    repository: SqlAlchemyStoryboardRepository,
) -> None:
    scope = StoryboardScope("tenant-a", "workspace-a", "project-a")
    storyboard = _storyboard(scope)
    audit = _audit(scope, object_id=storyboard.storyboard_id)

    first = repository.commit(
        scope,
        storyboard,
        expected_version=None,
        idempotency_key="create-1",
        fingerprint="fingerprint-create-1",
        audit=audit,
    )
    repeated = repository.commit(
        scope,
        storyboard,
        expected_version=None,
        idempotency_key="create-1",
        fingerprint="fingerprint-create-1",
        audit=audit,
    )

    assert first.replayed is False
    assert repeated == type(repeated)(storyboard=storyboard, replayed=True)
    assert (
        repository.replay(
            scope,
            idempotency_key="create-1",
            fingerprint="fingerprint-create-1",
        )
        == storyboard
    )
    assert repository.list_audit(scope) == (audit,)

    with pytest.raises(IdempotencyConflict, match="IDEMPOTENCY_KEY_REUSED"):
        repository.replay(
            scope,
            idempotency_key="create-1",
            fingerprint="different-fingerprint",
        )


@pytest.mark.uses_db
def test_version_cas_rejects_stale_writer_without_receipt_or_success_audit(
    repository: SqlAlchemyStoryboardRepository,
) -> None:
    from server.xingjing_storyboard.errors import VersionConflict

    scope = StoryboardScope("tenant-a", "workspace-a", "project-a")
    original = _storyboard(scope)
    repository.commit(
        scope,
        original,
        expected_version=None,
        idempotency_key="create-1",
        fingerprint="fingerprint-create-1",
        audit=_audit(scope, object_id=original.storyboard_id),
    )
    accepted = _next_storyboard(original, dialogue="已提交")
    repository.commit(
        scope,
        accepted,
        expected_version=1,
        idempotency_key="edit-1",
        fingerprint="fingerprint-edit-1",
        audit=_audit(scope, object_id=original.storyboard_id, action="storyboard.batch_edited"),
    )
    stale = _next_storyboard(original, dialogue="陈旧覆盖")

    with pytest.raises(VersionConflict) as captured:
        repository.commit(
            scope,
            stale,
            expected_version=1,
            idempotency_key="edit-stale",
            fingerprint="fingerprint-edit-stale",
            audit=_audit(scope, object_id=original.storyboard_id, action="storyboard.batch_edited"),
        )

    assert captured.value.details == {"current_version": 2}
    assert repository.get(scope, original.storyboard_id) == accepted
    assert (
        repository.replay(
            scope,
            idempotency_key="edit-stale",
            fingerprint="fingerprint-edit-stale",
        )
        is None
    )
    assert [event.action for event in repository.list_audit(scope)] == [
        "storyboard.created",
        "storyboard.batch_edited",
    ]


@pytest.mark.uses_db
def test_export_receipt_is_atomic_replayable_and_separate_from_storyboard_keys(
    repository: SqlAlchemyStoryboardRepository,
) -> None:
    scope = StoryboardScope("tenant-a", "workspace-a", "project-a")
    storyboard = _storyboard(scope)
    repository.commit(
        scope,
        storyboard,
        expected_version=None,
        idempotency_key="shared-key",
        fingerprint="storyboard-fingerprint",
        audit=_audit(scope, object_id=storyboard.storyboard_id),
    )
    export_result: dict[str, object] = {
        "format": "json",
        "filename": "storyboard.json",
        "media_type": "application/json",
        "sha256": "a" * 64,
        "content_base64": "e30=",
    }
    export_audit = _audit(scope, object_id=storyboard.storyboard_id, action="storyboard.exported")

    first, first_replayed = repository.commit_export(
        scope,
        idempotency_key="shared-key",
        fingerprint="export-fingerprint",
        result=export_result,
        audit=export_audit,
    )
    repeated, repeated_replayed = repository.commit_export(
        scope,
        idempotency_key="shared-key",
        fingerprint="export-fingerprint",
        result={"format": "csv"},
        audit=export_audit,
    )

    assert (first, first_replayed) == (export_result, False)
    assert (repeated, repeated_replayed) == (export_result, True)
    assert (
        repository.replay_export(
            scope,
            idempotency_key="shared-key",
            fingerprint="export-fingerprint",
        )
        == export_result
    )
    assert [event.action for event in repository.list_audit(scope)] == [
        "storyboard.created",
        "storyboard.exported",
    ]

    with pytest.raises(IdempotencyConflict, match="IDEMPOTENCY_KEY_REUSED"):
        repository.replay_export(
            scope,
            idempotency_key="shared-key",
            fingerprint="different-export-fingerprint",
        )


@pytest.mark.uses_db
def test_audit_query_filters_by_request_actor_action_and_full_scope(
    repository: SqlAlchemyStoryboardRepository,
) -> None:
    scope = StoryboardScope("tenant-a", "workspace-a", "project-a")
    other_project = StoryboardScope("tenant-a", "workspace-a", "project-b")
    first = _audit(scope, object_id="storyboard-1", action="storyboard.created")
    second = replace(
        _audit(scope, object_id="storyboard-2", action="storyboard.batch_edited"),
        request_id="request-2",
        actor_id="actor-2",
    )
    repository.append_audit(first)
    repository.append_audit(second)
    repository.append_audit(_audit(other_project, object_id="storyboard-1", action="storyboard.created"))

    assert repository.query_audit(scope, request_id="request-2") == (second,)
    assert repository.query_audit(scope, actor_id="actor-1") == (first,)
    assert repository.query_audit(scope, action="storyboard.created") == (first,)
    assert repository.query_audit(scope, storyboard_id="storyboard-2") == (second,)
    assert (
        repository.query_audit(
            StoryboardScope("tenant-a", "workspace-b", "project-a"),
            request_id="request-2",
        )
        == ()
    )
