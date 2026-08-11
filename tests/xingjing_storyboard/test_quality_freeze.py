from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from server.xingjing_storyboard.errors import (
    FrozenStoryboardViolation,
    InvalidAssetReference,
    QualityGateFailed,
)
from server.xingjing_storyboard.models import AssetKind, AssetReference, GenerationStatus
from server.xingjing_storyboard.ports import AssetReferenceFailure
from server.xingjing_storyboard.repository import AtomicFileStoryboardRepository
from server.xingjing_storyboard.service import AccessContext, ShotDraft, StoryboardService

NOW = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


@dataclass(slots=True)
class Ids:
    sequence: int = 0

    def new(self, kind: str) -> str:
        self.sequence += 1
        return f"{kind}-{self.sequence}"


@dataclass(slots=True)
class Assets:
    failures: tuple[AssetReferenceFailure, ...] = ()
    seen: tuple[str, str, str] | None = None

    def validate(self, *, tenant_id, workspace_id, project_id, references):
        self.seen = (tenant_id, workspace_id, project_id)
        return self.failures


def context(request_id: str = "request-1") -> AccessContext:
    return AccessContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        project_id="project-a",
        actor_id="user-1",
        request_id=request_id,
        permissions=frozenset({"shot.view", "shot.manage"}),
    )


def policy():
    from server.xingjing_storyboard.quality import QualityPolicy

    return QualityPolicy(
        minimum_duration_ms=1_000,
        maximum_duration_ms=5_000,
        max_adjacent_duration_delta_ms=2_000,
        max_dialogue_characters_per_second=2,
        require_scene_reference=True,
        require_character_for_dialogue=True,
        compliance_terms=frozenset({"forbidden-term"}),
    )


def service_at(path: Path, assets: Assets | None = None) -> StoryboardService:
    return StoryboardService(
        AtomicFileStoryboardRepository(path),
        assets or Assets(),
        identifiers=Ids(),
        clock=lambda: NOW,
        quality_policy=policy(),
    )


def valid_draft() -> ShotDraft:
    return ShotDraft(
        shot_number="S01",
        duration_ms=2_000,
        dialogue="你好",
        prompt="clean prompt",
        asset_references=(
            AssetReference("scene-1", "scene-v1", AssetKind.SCENE),
            AssetReference("character-1", "character-v1", AssetKind.CHARACTER),
        ),
    )


def test_asset_validation_receives_tenant_scope_and_rejects_cross_scope_reference(tmp_path: Path) -> None:
    assets = Assets(failures=(AssetReferenceFailure("scene-other", "v1", "CROSS_WORKSPACE_ASSET"),))
    service = service_at(tmp_path, assets)

    with pytest.raises(InvalidAssetReference, match="INVALID_ASSET_REFERENCE") as caught:
        service.create_storyboard(
            context(),
            episode_id="episode-1",
            shots=(valid_draft(),),
            idempotency_key="create-1",
        )

    assert assets.seen == ("tenant-a", "workspace-a", "project-a")
    failures = cast(list[dict[str, str]], caught.value.details["failures"])
    assert failures[0]["code"] == "CROSS_WORKSPACE_ASSET"
    assert service.list_storyboards(context()).total == 0


def test_quality_check_versions_deterministic_issues_and_freeze_preserves_failed_state(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    created = service.create_storyboard(
        context(),
        episode_id="episode-1",
        shots=(
            ShotDraft(
                shot_number="S01",
                duration_ms=6_000,
                dialogue="forbidden-term and a very long dialogue",
                prompt="risk",
            ),
        ),
        idempotency_key="create-1",
    ).storyboard
    checked = service.run_quality_check(
        context("quality-request"),
        storyboard_id=created.storyboard_id,
        expected_version=1,
        idempotency_key="quality-1",
    ).storyboard

    assert checked.version == 2
    assert {issue.code for issue in checked.shots[0].quality_issues} == {
        "COMPLIANCE_RISK",
        "DIALOGUE_TOO_LONG",
        "DURATION_OUT_OF_BOUNDS",
        "MISSING_CHARACTER",
        "MISSING_SCENE",
    }
    with pytest.raises(QualityGateFailed, match="QUALITY_GATE_FAILED"):
        service.freeze_storyboard(
            context("freeze-request"),
            storyboard_id=created.storyboard_id,
            expected_version=2,
            idempotency_key="freeze-1",
        )
    assert service.get_storyboard(context(), created.storyboard_id) == checked


def test_replacement_keeps_shot_id_and_frozen_snapshot_is_immutable_downstream_contract(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    created = service.create_storyboard(
        context(), episode_id="episode-1", shots=(valid_draft(),), idempotency_key="create-1"
    ).storyboard
    replaced = service.replace_shot_media(
        context("replace-request"),
        storyboard_id=created.storyboard_id,
        expected_version=1,
        shot_id=created.shots[0].shot_id,
        new_media_id="media-v2",
        reason="candidate_approved",
        idempotency_key="replace-1",
    ).storyboard
    frozen = service.freeze_storyboard(
        context("freeze-request"),
        storyboard_id=created.storyboard_id,
        expected_version=2,
        idempotency_key="freeze-1",
    ).storyboard

    assert replaced.shots[0].shot_id == created.shots[0].shot_id
    assert replaced.shots[0].selected_media_id == "media-v2"
    assert replaced.shots[0].generation_status is GenerationStatus.SUCCEEDED
    assert frozen.frozen_snapshot is not None
    assert frozen.frozen_snapshot.verify_digest()
    assert frozen.frozen_snapshot.source_version == frozen.version
    assert frozen.frozen_snapshot.shots[0].shot_id == created.shots[0].shot_id
    with pytest.raises(FrozenStoryboardViolation, match="STORYBOARD_FROZEN"):
        service.replace_shot_media(
            context("late-replace"),
            storyboard_id=created.storyboard_id,
            expected_version=frozen.version,
            shot_id=created.shots[0].shot_id,
            new_media_id="media-v3",
            reason="too_late",
            idempotency_key="replace-late",
        )
