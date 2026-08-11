from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from server.xingjing_storyboard.errors import IdempotencyConflict, ImportValidationError
from server.xingjing_storyboard.models import AssetKind, AssetReference
from server.xingjing_storyboard.repository import AtomicFileStoryboardRepository
from server.xingjing_storyboard.service import AccessContext, ShotDraft, StoryboardService

NOW = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)


@dataclass(slots=True)
class Ids:
    value: int = 0

    def new(self, kind: str) -> str:
        self.value += 1
        return f"{kind}-{self.value}"


class Assets:
    def validate(self, *, tenant_id, workspace_id, project_id, references):
        return ()


def context(request_id: str = "request-1") -> AccessContext:
    return AccessContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        project_id="project-a",
        actor_id="user-1",
        request_id=request_id,
        permissions=frozenset({"shot.view", "shot.manage"}),
    )


def service_at(path: Path) -> StoryboardService:
    return StoryboardService(AtomicFileStoryboardRepository(path), Assets(), identifiers=Ids(), clock=lambda: NOW)


def draft(number: str, duration_ms: int) -> ShotDraft:
    return ShotDraft(
        shot_number=number,
        duration_ms=duration_ms,
        dialogue=f"dialogue-{number}",
        prompt=f"prompt-{number}",
        shot_size="medium",
        camera_movement="pan",
        model_strategy="quality",
        cost_tier="pro",
        asset_references=(AssetReference("scene-1", "scene-v1", AssetKind.SCENE),),
    )


@pytest.mark.parametrize("format_name", ["json", "csv", "xlsx"])
def test_json_csv_xlsx_export_import_preserves_stable_shot_contract(tmp_path: Path, format_name: str) -> None:
    from server.xingjing_storyboard.exchange import ExportFormat

    service = service_at(tmp_path)
    original = service.create_storyboard(
        context(), episode_id="episode-1", shots=(draft("S01", 2_000), draft("S02", 3_000)), idempotency_key="create-1"
    ).storyboard
    format_ = ExportFormat(format_name)
    exported = service.export_storyboard(
        context("export-request"),
        storyboard_id=original.storyboard_id,
        format=format_,
        idempotency_key=f"export-{format_name}",
    )
    replay = service.export_storyboard(
        context("export-retry"),
        storyboard_id=original.storyboard_id,
        format=format_,
        idempotency_key=f"export-{format_name}",
    )
    imported = service.import_storyboard(
        context("import-request"),
        episode_id=f"imported-{format_name}",
        filename=exported.filename,
        content=exported.content,
        idempotency_key=f"import-{format_name}",
    ).storyboard

    assert exported.content
    assert replay.replayed is True
    assert replay.content == exported.content
    assert exported.sha256 == replay.sha256
    assert tuple(shot.shot_id for shot in imported.shots) == tuple(shot.shot_id for shot in original.shots)
    assert [shot.to_dict() for shot in imported.shots] == [
        {**shot.to_dict(), "quality_issues": [], "replacements": []} for shot in original.shots
    ]


def test_invalid_import_is_atomic_and_idempotency_key_cannot_mask_different_payload(tmp_path: Path) -> None:
    service = service_at(tmp_path)

    with pytest.raises(ImportValidationError, match="INVALID_IMPORT"):
        service.import_storyboard(
            context(),
            episode_id="episode-1",
            filename="broken.csv",
            content=b"shot_id,position\nbad,not-an-int\n",
            idempotency_key="import-1",
        )
    assert service.list_storyboards(context()).total == 0

    first = service.import_storyboard(
        context("import-ok"),
        episode_id="episode-1",
        filename="shots.json",
        content=b'{"schema_version":1,"shots":[{"shot_id":"stable-1","position":1,"shot_number":"S01","duration_ms":1000,"dialogue":"","prompt":"","shot_size":null,"camera_movement":null,"model_strategy":null,"cost_tier":null,"asset_references":[],"selected_media_id":null,"generation_status":"not_started"}]}',
        idempotency_key="import-1",
    )
    assert first.replayed is False
    with pytest.raises(IdempotencyConflict, match="IDEMPOTENCY_KEY_REUSED"):
        service.import_storyboard(
            context("import-other"),
            episode_id="episode-2",
            filename="different.json",
            content=b'{"schema_version":1,"shots":[]}',
            idempotency_key="import-1",
        )
