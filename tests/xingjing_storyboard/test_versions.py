from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from server.xingjing_storyboard.repository import AtomicFileStoryboardRepository
from server.xingjing_storyboard.service import AccessContext, ShotDraft, ShotEdit, StoryboardService


@dataclass(slots=True)
class Ids:
    value: int = 0

    def new(self, kind: str) -> str:
        self.value += 1
        return f"{kind}-{self.value}"


class Assets:
    def validate(self, *, tenant_id, workspace_id, project_id, references):
        return ()


def context() -> AccessContext:
    return AccessContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        project_id="project-a",
        actor_id="user-1",
        request_id="request-1",
        permissions=frozenset({"shot.view", "shot.manage"}),
    )


def test_version_comparison_returns_field_level_changes_for_stable_shot_id(tmp_path: Path) -> None:
    service = StoryboardService(
        AtomicFileStoryboardRepository(tmp_path),
        Assets(),
        identifiers=Ids(),
        clock=lambda: datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )
    created = service.create_storyboard(
        context(),
        episode_id="episode-1",
        shots=(ShotDraft(shot_number="S01", duration_ms=1_000, dialogue="旧台词"),),
        idempotency_key="create-1",
    ).storyboard
    service.batch_edit(
        context(),
        storyboard_id=created.storyboard_id,
        expected_version=1,
        edits=(ShotEdit(shot_id=created.shots[0].shot_id, dialogue="新台词", prompt="new prompt"),),
        idempotency_key="edit-1",
    )

    comparison = service.compare_versions(
        context(),
        storyboard_id=created.storyboard_id,
        baseline_version=1,
        candidate_version=2,
    )

    assert comparison.baseline_version == 1
    assert comparison.candidate_version == 2
    assert comparison.changed_shot_ids == (created.shots[0].shot_id,)
    assert comparison.fields_by_shot[created.shots[0].shot_id] == ("dialogue", "prompt")
