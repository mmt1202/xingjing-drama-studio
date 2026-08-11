from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from server.xingjing_storyboard.errors import (
    IdempotencyConflict,
    PermissionDenied,
    StoryboardNotFound,
    VersionConflict,
)
from server.xingjing_storyboard.models import AssetReference
from server.xingjing_storyboard.ports import AssetReferenceFailure

NOW = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)


@dataclass(slots=True)
class SequentialIds:
    counters: dict[str, int] = field(default_factory=dict)

    def new(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"{kind}-{self.counters[kind]}"


@dataclass(slots=True)
class AcceptingAssets:
    calls: list[tuple[str, str, str, tuple[AssetReference, ...]]] = field(default_factory=list)

    def validate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        references: tuple[AssetReference, ...],
    ) -> tuple[AssetReferenceFailure, ...]:
        self.calls.append((tenant_id, workspace_id, project_id, references))
        return ()


def context(
    *,
    tenant_id: str = "tenant-a",
    workspace_id: str = "workspace-a",
    project_id: str = "project-a",
    request_id: str = "request-1",
    permissions: frozenset[str] = frozenset({"shot.view", "shot.manage"}),
):
    from server.xingjing_storyboard.service import AccessContext

    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        project_id=project_id,
        actor_id="user-1",
        request_id=request_id,
        permissions=permissions,
    )


def service_at(path: Path, assets: AcceptingAssets | None = None):
    from server.xingjing_storyboard.repository import AtomicFileStoryboardRepository
    from server.xingjing_storyboard.service import StoryboardService

    return StoryboardService(
        AtomicFileStoryboardRepository(path),
        assets or AcceptingAssets(),
        identifiers=SequentialIds(),
        clock=lambda: NOW,
    )


def drafts():
    from server.xingjing_storyboard.service import ShotDraft

    return (
        ShotDraft(
            shot_number="S01",
            duration_ms=2_000,
            dialogue="开场",
            shot_size="wide",
            camera_movement="static",
            prompt="opening",
        ),
        ShotDraft(
            shot_number="S02",
            duration_ms=3_000,
            dialogue="推进",
            shot_size="medium",
            camera_movement="dolly_in",
            prompt="move closer",
        ),
    )


def test_batch_edit_and_reorder_keep_stable_ids_and_survive_restart(tmp_path: Path) -> None:
    from server.xingjing_storyboard.service import ShotEdit

    service = service_at(tmp_path)
    created = service.create_storyboard(
        context(), episode_id="episode-1", shots=drafts(), idempotency_key="create-1"
    ).storyboard
    original_ids = tuple(shot.shot_id for shot in created.shots)

    edited = service.batch_edit(
        context(request_id="request-2"),
        storyboard_id=created.storyboard_id,
        expected_version=1,
        edits=(ShotEdit(shot_id=original_ids[0], shot_size="close_up", duration_ms=2_500),),
        idempotency_key="edit-1",
    ).storyboard
    reordered = service.reorder_shots(
        context(request_id="request-3"),
        storyboard_id=created.storyboard_id,
        expected_version=2,
        ordered_shot_ids=tuple(reversed(original_ids)),
        idempotency_key="reorder-1",
    ).storyboard

    assert edited.shots[0].shot_id == original_ids[0]
    assert (edited.shots[0].shot_size, edited.shots[0].duration_ms) == ("close_up", 2_500)
    assert tuple(shot.shot_id for shot in reordered.shots) == tuple(reversed(original_ids))
    assert [version.number for version in reordered.versions] == [1, 2, 3]
    assert [shot.position for shot in reordered.shots] == [1, 2]
    assert service_at(tmp_path).get_storyboard(context(), created.storyboard_id) == reordered


def test_permissions_are_checked_per_request_and_tenant_scope_does_not_leak(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    created = service.create_storyboard(
        context(), episode_id="episode-1", shots=drafts(), idempotency_key="create-1"
    ).storyboard

    with pytest.raises(PermissionDenied, match="PERMISSION_DENIED"):
        service.get_storyboard(context(permissions=frozenset()), created.storyboard_id)
    with pytest.raises(StoryboardNotFound, match="STORYBOARD_NOT_FOUND"):
        service.get_storyboard(context(tenant_id="tenant-b"), created.storyboard_id)


def test_idempotency_replays_original_result_and_rejects_key_reuse(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    first = service.create_storyboard(context(), episode_id="episode-1", shots=drafts(), idempotency_key="same-key")
    replay = service.create_storyboard(
        context(request_id="retry-request"),
        episode_id="episode-1",
        shots=drafts(),
        idempotency_key="same-key",
    )

    assert replay.replayed is True
    assert replay.storyboard == first.storyboard
    assert len(service.list_storyboards(context()).items) == 1
    with pytest.raises(IdempotencyConflict, match="IDEMPOTENCY_KEY_REUSED"):
        service.create_storyboard(
            context(request_id="other-request"),
            episode_id="episode-2",
            shots=drafts(),
            idempotency_key="same-key",
        )


def test_stale_and_invalid_batch_updates_are_atomic_and_audited(tmp_path: Path) -> None:
    from server.xingjing_storyboard.errors import ContractViolation
    from server.xingjing_storyboard.service import ShotEdit

    service = service_at(tmp_path)
    created = service.create_storyboard(
        context(), episode_id="episode-1", shots=drafts(), idempotency_key="create-1"
    ).storyboard
    changed = service.batch_edit(
        context(request_id="request-2"),
        storyboard_id=created.storyboard_id,
        expected_version=1,
        edits=(ShotEdit(shot_id=created.shots[0].shot_id, dialogue="新台词"),),
        idempotency_key="edit-1",
    ).storyboard

    with pytest.raises(VersionConflict, match="VERSION_CONFLICT") as caught:
        service.batch_edit(
            context(request_id="stale-request"),
            storyboard_id=created.storyboard_id,
            expected_version=1,
            edits=(ShotEdit(shot_id=created.shots[1].shot_id, dialogue="过期写入"),),
            idempotency_key="edit-stale",
        )
    assert caught.value.details["current_version"] == 2

    with pytest.raises(ContractViolation, match="SHOT_NOT_FOUND"):
        service.batch_edit(
            context(request_id="invalid-request"),
            storyboard_id=created.storyboard_id,
            expected_version=2,
            edits=(ShotEdit(shot_id="missing-shot", dialogue="无效"),),
            idempotency_key="edit-invalid",
        )

    assert service.get_storyboard(context(), created.storyboard_id) == changed
    audit = service.list_audit(context(), storyboard_id=created.storyboard_id)
    assert [(event.request_id, event.result) for event in audit][-2:] == [
        ("stale-request", "failed"),
        ("invalid-request", "failed"),
    ]
    assert audit[0].action == "storyboard.created"
    assert audit[0].before == {}
    assert audit[0].after["version"] == 1
