from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server.xingjing_storyboard.models import (
    GenerationStatus,
    Shot,
    Storyboard,
    StoryboardStatus,
    StoryboardVersion,
    storyboard_version_digest,
)
from server.xingjing_storyboard.ports import AuditEvent, StoryboardScope
from server.xingjing_storyboard_persistence import (
    Base,
    SqlAlchemyStoryboardRepository,
    SqlAlchemyStoryboardUploadRepository,
)


@pytest.fixture
def repositories() -> tuple[SqlAlchemyStoryboardUploadRepository, SqlAlchemyStoryboardRepository]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    return SqlAlchemyStoryboardUploadRepository(factory), SqlAlchemyStoryboardRepository(factory)


def _audit(scope: StoryboardScope, *, upload_id: str, action: str) -> AuditEvent:
    return AuditEvent(
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        request_id=f"request-{action}",
        actor_id="actor-1",
        action=action,
        object_id=upload_id,
        result="succeeded",
        occurred_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
        before={},
        after={"upload_id": upload_id},
    )


@pytest.mark.uses_db
def test_import_upload_metadata_is_scoped_idempotent_and_audited(
    repositories: tuple[SqlAlchemyStoryboardUploadRepository, SqlAlchemyStoryboardRepository],
) -> None:
    uploads, storyboards = repositories
    scope = StoryboardScope("tenant-a", "workspace-a", "project-a")
    outsider = StoryboardScope("tenant-a", "workspace-b", "project-a")
    audit = _audit(scope, upload_id="upload-stable-1", action="storyboard.upload_created")

    first, first_replayed = uploads.create(
        scope,
        upload_id="upload-stable-1",
        object_key="tenant-a/workspace-a/project-a/storyboard-imports/upload-stable-1/source.csv",
        filename="source.csv",
        media_type="text/csv",
        size_bytes=128,
        sha256="a" * 64,
        source="storyboard_import",
        lifecycle="source",
        idempotency_key="upload-create-1",
        fingerprint="upload-create-fingerprint",
        created_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
        audit=audit,
    )
    repeated, repeated_replayed = uploads.create(
        scope,
        upload_id="ignored-on-replay",
        object_key="ignored",
        filename="ignored.csv",
        media_type="text/csv",
        size_bytes=1,
        sha256="b" * 64,
        source="storyboard_import",
        lifecycle="source",
        idempotency_key="upload-create-1",
        fingerprint="upload-create-fingerprint",
        created_at=datetime(2026, 7, 16, 9, 1, tzinfo=UTC),
        audit=audit,
    )

    assert first_replayed is False
    assert repeated_replayed is True
    assert repeated == first
    assert first.upload_id == "upload-stable-1"
    assert first.object_key.endswith("/upload-stable-1/source.csv")
    assert uploads.get(scope, first.upload_id) == first
    assert uploads.get(outsider, first.upload_id) is None
    assert storyboards.list_audit(scope, storyboard_id=first.upload_id) == (audit,)


@pytest.mark.uses_db
def test_import_upload_completion_checks_digest_and_size_before_atomic_transition(
    repositories: tuple[SqlAlchemyStoryboardUploadRepository, SqlAlchemyStoryboardRepository],
) -> None:
    from server.xingjing_storyboard.errors import ContractViolation
    from server.xingjing_storyboard_persistence import ImportUploadStatus

    uploads, storyboards = repositories
    scope = StoryboardScope("tenant-a", "workspace-a", "project-a")
    upload_id = "upload-stable-1"
    uploads.create(
        scope,
        upload_id=upload_id,
        object_key="tenant-a/workspace-a/project-a/storyboard-imports/upload-stable-1/source.csv",
        filename="source.csv",
        media_type="text/csv",
        size_bytes=128,
        sha256="a" * 64,
        source="storyboard_import",
        lifecycle="source",
        idempotency_key="upload-create-1",
        fingerprint="upload-create-fingerprint",
        created_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
        audit=_audit(scope, upload_id=upload_id, action="storyboard.upload_created"),
    )
    completed_audit = _audit(scope, upload_id=upload_id, action="storyboard.upload_completed")

    with pytest.raises(ContractViolation, match="UPLOAD_METADATA_MISMATCH"):
        uploads.complete(
            scope,
            upload_id,
            size_bytes=127,
            sha256="a" * 64,
            idempotency_key="upload-complete-invalid",
            fingerprint="upload-complete-invalid-fingerprint",
            completed_at=datetime(2026, 7, 16, 9, 5, tzinfo=UTC),
            audit=completed_audit,
        )

    pending = uploads.get(scope, upload_id)
    assert pending is not None
    assert pending.status is ImportUploadStatus.PENDING
    assert (
        uploads.replay(
            scope,
            operation="complete",
            idempotency_key="upload-complete-invalid",
            fingerprint="upload-complete-invalid-fingerprint",
        )
        is None
    )

    completed, replayed = uploads.complete(
        scope,
        upload_id,
        size_bytes=128,
        sha256="a" * 64,
        idempotency_key="upload-complete-1",
        fingerprint="upload-complete-fingerprint",
        completed_at=datetime(2026, 7, 16, 9, 5, tzinfo=UTC),
        audit=completed_audit,
    )
    repeated, repeated_replayed = uploads.complete(
        scope,
        upload_id,
        size_bytes=128,
        sha256="a" * 64,
        idempotency_key="upload-complete-1",
        fingerprint="upload-complete-fingerprint",
        completed_at=datetime(2026, 7, 16, 9, 6, tzinfo=UTC),
        audit=completed_audit,
    )

    assert replayed is False
    assert repeated_replayed is True
    assert repeated == completed
    assert completed.status is ImportUploadStatus.COMPLETED
    assert completed.completed_at == datetime(2026, 7, 16, 9, 5, tzinfo=UTC)
    assert [event.action for event in storyboards.list_audit(scope, storyboard_id=upload_id)] == [
        "storyboard.upload_created",
        "storyboard.upload_completed",
    ]


@pytest.mark.uses_db
def test_completed_upload_can_only_be_consumed_by_storyboard_in_same_full_scope(
    repositories: tuple[SqlAlchemyStoryboardUploadRepository, SqlAlchemyStoryboardRepository],
) -> None:
    from server.xingjing_storyboard.errors import ContractViolation
    from server.xingjing_storyboard_persistence import ImportUploadStatus

    uploads, storyboards = repositories
    scope = StoryboardScope("tenant-a", "workspace-a", "project-a")
    other_project = StoryboardScope("tenant-a", "workspace-a", "project-b")
    upload_id = "upload-stable-1"
    uploads.create(
        scope,
        upload_id=upload_id,
        object_key="tenant-a/workspace-a/project-a/storyboard-imports/upload-stable-1/source.csv",
        filename="source.csv",
        media_type="text/csv",
        size_bytes=128,
        sha256="a" * 64,
        source="storyboard_import",
        lifecycle="source",
        idempotency_key="upload-create-1",
        fingerprint="upload-create-fingerprint",
        created_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
        audit=_audit(scope, upload_id=upload_id, action="storyboard.upload_created"),
    )
    uploads.complete(
        scope,
        upload_id,
        size_bytes=128,
        sha256="a" * 64,
        idempotency_key="upload-complete-1",
        fingerprint="upload-complete-fingerprint",
        completed_at=datetime(2026, 7, 16, 9, 5, tzinfo=UTC),
        audit=_audit(scope, upload_id=upload_id, action="storyboard.upload_completed"),
    )

    foreign_storyboard = _storyboard_for_scope(other_project, storyboard_id="storyboard-other-project")
    storyboards.commit(
        other_project,
        foreign_storyboard,
        expected_version=None,
        idempotency_key="foreign-create-1",
        fingerprint="foreign-create-fingerprint",
        audit=_audit(other_project, upload_id=foreign_storyboard.storyboard_id, action="storyboard.created"),
    )
    with pytest.raises(ContractViolation, match="STORYBOARD_NOT_FOUND"):
        uploads.consume(
            scope,
            upload_id,
            storyboard_id=foreign_storyboard.storyboard_id,
            idempotency_key="upload-consume-invalid",
            fingerprint="upload-consume-invalid-fingerprint",
            consumed_at=datetime(2026, 7, 16, 9, 10, tzinfo=UTC),
            audit=_audit(scope, upload_id=upload_id, action="storyboard.upload_consumed"),
        )

    storyboard = _storyboard_for_scope(scope, storyboard_id="storyboard-imported")
    storyboards.commit(
        scope,
        storyboard,
        expected_version=None,
        idempotency_key="storyboard-create-1",
        fingerprint="storyboard-create-fingerprint",
        audit=_audit(scope, upload_id=storyboard.storyboard_id, action="storyboard.imported"),
    )
    consumed, replayed = uploads.consume(
        scope,
        upload_id,
        storyboard_id=storyboard.storyboard_id,
        idempotency_key="upload-consume-1",
        fingerprint="upload-consume-fingerprint",
        consumed_at=datetime(2026, 7, 16, 9, 10, tzinfo=UTC),
        audit=_audit(scope, upload_id=upload_id, action="storyboard.upload_consumed"),
    )

    assert replayed is False
    assert consumed.status is ImportUploadStatus.CONSUMED
    assert consumed.storyboard_id == storyboard.storyboard_id
    assert consumed.sha256 == "a" * 64
    assert uploads.get(scope, upload_id) == consumed


def _storyboard_for_scope(scope: StoryboardScope, *, storyboard_id: str) -> Storyboard:
    now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    shot = Shot(
        shot_id=f"shot-{storyboard_id}",
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
