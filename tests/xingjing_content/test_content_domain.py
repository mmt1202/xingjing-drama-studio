import json

import pytest

from server.xingjing_content import (
    ContentConflict,
    ContentService,
    DirectorProfile,
    FileContentRepository,
    ImportScriptCommand,
    ReviseScriptCommand,
    SetDirectorProfileCommand,
)

SOURCE = """# 第一章 雨夜
场景：旧宅客厅｜夜｜内
林夏：你终于来了。
旁白：窗外的雨越来越大。

场景：旧宅走廊｜夜｜内
周明：别回头。
"""


def test_import_parse_preserves_structure_and_source_mapping(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))

    script = service.import_script(
        ImportScriptCommand(
            workspace_id="ws-1",
            project_id="project-1",
            title="雨夜",
            filename="rain.md",
            media_type="text/markdown",
            content=SOURCE,
            idempotency_key="import-1",
        )
    )

    assert script.revision == 1
    assert script.current_version.number == 1
    assert [scene.heading.location for scene in script.current_version.scenes] == ["旧宅客厅", "旧宅走廊"]
    first_paragraph = script.current_version.scenes[0].paragraphs[0]
    assert first_paragraph.kind == "dialogue"
    assert first_paragraph.speaker == "林夏"
    mapping = script.current_version.source_mappings[0]
    assert SOURCE[mapping.source_start : mapping.source_end] == first_paragraph.source_text
    assert script.current_version.validation_errors == ()


def test_import_reports_serializable_validation_errors_without_discarding_source(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))

    script = service.import_script(
        ImportScriptCommand(
            workspace_id="ws-1",
            project_id="project-1",
            title="空剧本",
            filename="empty.txt",
            media_type="text/plain",
            content="只有一行没有场景的文字",
            idempotency_key="import-invalid",
        )
    )

    error = script.current_version.validation_errors[0]
    assert error.code == "SCENE_HEADING_REQUIRED"
    assert error.path == "scenes"
    payload = script.to_dict()
    assert payload["source_document"]["content"] == "只有一行没有场景的文字"
    assert (
        json.loads(json.dumps(payload, ensure_ascii=False))["versions"][0]["validation_errors"][0]["code"] == error.code
    )


def test_duplicate_idempotency_key_returns_original_result_after_repository_reload(tmp_path):
    command = ImportScriptCommand(
        workspace_id="ws-1",
        project_id="project-1",
        title="雨夜",
        filename="rain.md",
        media_type="text/markdown",
        content=SOURCE,
        idempotency_key="same-request",
    )
    first = ContentService(FileContentRepository(tmp_path)).import_script(command)
    second = ContentService(FileContentRepository(tmp_path)).import_script(command)

    assert second.script_id == first.script_id
    assert second.revision == first.revision
    assert len(second.versions) == 1


def test_idempotency_key_cannot_be_reused_with_different_payload(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))
    base = ImportScriptCommand(
        workspace_id="ws-1",
        project_id="project-1",
        title="雨夜",
        filename="rain.md",
        media_type="text/markdown",
        content=SOURCE,
        idempotency_key="same-request",
    )
    service.import_script(base)

    with pytest.raises(ContentConflict, match="IDEMPOTENCY_KEY_REUSED"):
        service.import_script(base.with_content(SOURCE + "\n尾声"))


def test_revision_uses_optimistic_version_and_preserves_history(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))
    imported = service.import_script(
        ImportScriptCommand(
            workspace_id="ws-1",
            project_id="project-1",
            title="雨夜",
            filename="rain.md",
            media_type="text/markdown",
            content=SOURCE,
            idempotency_key="import-1",
        )
    )
    revised = service.revise_script(
        ReviseScriptCommand(
            workspace_id="ws-1",
            script_id=imported.script_id,
            expected_revision=1,
            content=SOURCE.replace("别回头", "快离开"),
            idempotency_key="revise-1",
            change_summary="润色第二场对白",
        )
    )

    assert revised.revision == 2
    assert [version.number for version in revised.versions] == [1, 2]
    assert revised.versions[0].source_content == SOURCE
    with pytest.raises(ContentConflict, match="VERSION_CONFLICT"):
        service.revise_script(
            ReviseScriptCommand(
                workspace_id="ws-1",
                script_id=imported.script_id,
                expected_revision=1,
                content=SOURCE,
                idempotency_key="stale-revise",
                change_summary="过期保存",
            )
        )


def test_freeze_requires_valid_version_and_emits_downstream_snapshot(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))
    imported = service.import_script(
        ImportScriptCommand(
            workspace_id="ws-1",
            project_id="project-1",
            title="雨夜",
            filename="rain.md",
            media_type="text/markdown",
            content=SOURCE,
            idempotency_key="import-1",
        )
    )

    frozen = service.freeze_script(
        workspace_id="ws-1",
        script_id=imported.script_id,
        expected_revision=1,
        idempotency_key="freeze-1",
    )
    snapshot = frozen.downstream_snapshot()

    assert frozen.locked_version_number == 1
    assert snapshot["script_id"] == imported.script_id
    assert snapshot["version_number"] == 1
    assert snapshot["characters"] == ["林夏", "周明"]
    assert snapshot["locations"] == ["旧宅客厅", "旧宅走廊"]


def test_invalid_script_cannot_be_frozen(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))
    imported = service.import_script(
        ImportScriptCommand("ws-1", "project-1", "坏剧本", "bad.txt", "text/plain", "无场景", "import-bad")
    )

    with pytest.raises(ValueError, match="SCRIPT_VALIDATION_FAILED"):
        service.freeze_script(
            workspace_id="ws-1",
            script_id=imported.script_id,
            expected_revision=1,
            idempotency_key="freeze-bad",
        )


def test_director_profile_update_is_versioned_and_included_in_frozen_snapshot(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))
    imported = service.import_script(
        ImportScriptCommand("ws-1", "project-1", "雨夜", "rain.md", "text/markdown", SOURCE, "import-1")
    )
    directed = service.set_director_profile(
        SetDirectorProfileCommand(
            workspace_id="ws-1",
            script_id=imported.script_id,
            expected_revision=1,
            idempotency_key="director-1",
            profile=DirectorProfile(
                audience="悬疑短剧用户",
                pacing="快节奏",
                visual_style="冷色电影感",
                camera_language="手持近景",
                production_constraints=("单集不超过 90 秒",),
            ),
        )
    )
    frozen = service.freeze_script(
        workspace_id="ws-1",
        script_id=directed.script_id,
        expected_revision=2,
        idempotency_key="freeze-1",
    )

    assert directed.revision == 2
    assert frozen.downstream_snapshot()["director_profile"]["pacing"] == "快节奏"


def test_repository_queries_are_tenant_scoped_stably_sorted_and_paginated(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))
    for workspace, title, key in [("ws-1", "乙", "i-2"), ("ws-2", "秘密", "i-x"), ("ws-1", "甲", "i-1")]:
        service.import_script(
            ImportScriptCommand(workspace, "project-1", title, f"{title}.txt", "text/plain", SOURCE, key)
        )

    page = service.list_scripts(workspace_id="ws-1", project_id="project-1", offset=0, limit=1)
    second_page = service.list_scripts(workspace_id="ws-1", project_id="project-1", offset=1, limit=1)
    repeated = service.list_scripts(workspace_id="ws-1", project_id="project-1", offset=0, limit=1)

    assert page.total == 2
    assert page.items == repeated.items
    assert {item.title for item in (*page.items, *second_page.items)} == {"甲", "乙"}
    with pytest.raises(KeyError, match="SCRIPT_NOT_FOUND"):
        service.get_script(workspace_id="ws-2", script_id=page.items[0].script_id)


def test_persisted_contract_round_trips_without_type_or_tuple_drift(tmp_path):
    service = ContentService(FileContentRepository(tmp_path))
    imported = service.import_script(
        ImportScriptCommand("ws-1", "project-1", "雨夜", "rain.md", "text/markdown", SOURCE, "import-1")
    )

    reloaded = ContentService(FileContentRepository(tmp_path)).get_script(
        workspace_id="ws-1", script_id=imported.script_id
    )

    assert reloaded == imported
    assert json.loads(json.dumps(reloaded.to_dict(), ensure_ascii=False))["script_id"] == imported.script_id
