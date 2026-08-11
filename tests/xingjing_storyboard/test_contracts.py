from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest


def test_storyboard_contract_round_trips_and_verifies_frozen_snapshot() -> None:
    from server.xingjing_storyboard.models import (
        AssetKind,
        AssetReference,
        FrozenStoryboard,
        GenerationStatus,
        QualityIssue,
        QualitySeverity,
        Shot,
        ShotReplacement,
        Storyboard,
        StoryboardStatus,
        StoryboardVersion,
        frozen_storyboard_digest,
        storyboard_version_digest,
    )

    created_at = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    shot = Shot(
        shot_id="shot-01",
        position=1,
        shot_number="S01",
        shot_size="medium",
        camera_movement="dolly_in",
        dialogue="你好",
        duration_ms=2_000,
        prompt="cinematic medium shot",
        model_strategy="balanced",
        cost_tier="standard",
        asset_references=(AssetReference("asset-1", "asset-v3", AssetKind.CHARACTER),),
        selected_media_id="media-v2",
        generation_status=GenerationStatus.SUCCEEDED,
        quality_issues=(
            QualityIssue(
                issue_id="issue-1",
                shot_id="shot-01",
                code="DIALOGUE_TOO_LONG",
                severity=QualitySeverity.WARNING,
                field="dialogue",
                details={"characters": 2, "capacity": 1},
            ),
        ),
        replacements=(
            ShotReplacement(
                replacement_id="replacement-1",
                previous_media_id="media-v1",
                new_media_id="media-v2",
                reason="quality_fix",
                actor_id="user-1",
                replaced_at=created_at,
            ),
        ),
    )
    version = StoryboardVersion(
        number=1,
        action="storyboard.created",
        actor_id="user-1",
        occurred_at=created_at,
        shots=(shot,),
        digest=storyboard_version_digest((shot,)),
    )
    frozen_at = datetime(2026, 7, 15, 8, 5, tzinfo=UTC)
    frozen = FrozenStoryboard(
        snapshot_id="snapshot-1",
        storyboard_id="storyboard-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        project_id="project-1",
        episode_id="episode-1",
        source_version=1,
        shots=(shot,),
        frozen_at=frozen_at,
        digest="",
    )
    frozen = FrozenStoryboard(
        snapshot_id=frozen.snapshot_id,
        storyboard_id=frozen.storyboard_id,
        tenant_id=frozen.tenant_id,
        workspace_id=frozen.workspace_id,
        project_id=frozen.project_id,
        episode_id=frozen.episode_id,
        source_version=frozen.source_version,
        shots=frozen.shots,
        frozen_at=frozen.frozen_at,
        digest=frozen_storyboard_digest(frozen),
    )
    storyboard = Storyboard(
        storyboard_id="storyboard-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        project_id="project-1",
        episode_id="episode-1",
        version=1,
        status=StoryboardStatus.FROZEN,
        shots=(shot,),
        versions=(version,),
        created_at=created_at,
        updated_at=frozen_at,
        frozen_snapshot=frozen,
    )

    serialized = storyboard.to_dict()
    assert Storyboard.from_dict(serialized) == storyboard
    assert json.loads(json.dumps(serialized, ensure_ascii=False))["shots"][0]["shot_id"] == "shot-01"
    assert frozen.verify_digest()


def test_storyboard_contract_rejects_duplicate_shot_ids_and_positions() -> None:
    from server.xingjing_storyboard.errors import ContractViolation
    from server.xingjing_storyboard.models import GenerationStatus, Shot, Storyboard, StoryboardStatus

    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    shot = Shot(
        shot_id="shot-01",
        position=1,
        shot_number="S01",
        shot_size=None,
        camera_movement=None,
        dialogue="",
        duration_ms=1_000,
        prompt="",
        model_strategy=None,
        cost_tier=None,
        asset_references=(),
        selected_media_id=None,
        generation_status=GenerationStatus.NOT_STARTED,
    )

    with pytest.raises(ContractViolation, match="DUPLICATE_SHOT_ID"):
        Storyboard(
            storyboard_id="storyboard-1",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            project_id="project-1",
            episode_id="episode-1",
            version=1,
            status=StoryboardStatus.DRAFT,
            shots=(shot, shot),
            versions=(),
            created_at=now,
            updated_at=now,
        )
