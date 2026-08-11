from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from server.xingjing_content import (
    ContentConflict,
    ContentService,
    DirectorProfile,
    ImportScriptCommand,
    ReviseScriptCommand,
    SetDirectorProfileCommand,
)
from server.xingjing_content_persistence import (
    AuditContext,
    ContentAuditRow,
    ContentPersistenceBase,
    ScriptVersionRow,
    SqlAlchemyContentRepository,
)

SOURCE = """# 第一章 雨夜
场景：旧宅客厅｜夜｜内
林夏：你终于来了。
旁白：窗外的雨越来越大。

场景：旧宅走廊｜夜｜内
周明：别回头。
"""
NOW = datetime(2026, 7, 16, tzinfo=UTC)


@pytest.fixture
def repository() -> SqlAlchemyContentRepository:
    engine = create_engine("sqlite:///:memory:")
    ContentPersistenceBase.metadata.create_all(engine)
    return SqlAlchemyContentRepository(
        sessionmaker(engine, expire_on_commit=False),
        audit_context=AuditContext(request_id="request-1", actor_id="actor-1", occurred_at=NOW),
    )


def command(
    *, workspace_id: str = "workspace-a", project_id: str = "project-a", key: str = "import-1"
) -> ImportScriptCommand:
    return ImportScriptCommand(workspace_id, project_id, "雨夜", "rain.md", "text/markdown", SOURCE, key)


def test_import_round_trips_source_and_normalized_mappings_with_audit(repository: SqlAlchemyContentRepository) -> None:
    service = ContentService(repository)

    created = service.import_script(command())
    reloaded = service.get_script(workspace_id="workspace-a", script_id=created.script_id)

    assert reloaded == created
    assert reloaded.source_document.content == SOURCE
    assert reloaded.current_version.source_mappings[0].source_start == SOURCE.index("林夏")
    with repository._session_factory() as session:
        version = session.scalar(select(ScriptVersionRow))
        audits = session.scalars(select(ContentAuditRow)).all()
    assert version is not None
    assert version.source_content == SOURCE
    assert len(audits) == 1
    assert audits[0].workspace_id == "workspace-a"
    assert audits[0].project_id == "project-a"
    assert audits[0].request_id == "request-1"


def test_project_and_workspace_scopes_do_not_leak_scripts(repository: SqlAlchemyContentRepository) -> None:
    service = ContentService(repository)
    first = service.import_script(command())
    service.import_script(command(project_id="project-b", key="import-2"))
    service.import_script(command(workspace_id="workspace-b", key="import-3"))

    page = service.list_scripts(workspace_id="workspace-a", project_id="project-a")

    assert page.items == (first,)
    with pytest.raises(KeyError, match="SCRIPT_NOT_FOUND"):
        service.get_script(workspace_id="workspace-b", script_id=first.script_id)


def test_revision_uses_compare_and_swap_and_appends_immutable_version(repository: SqlAlchemyContentRepository) -> None:
    service = ContentService(repository)
    created = service.import_script(command())
    winner = service.revise_script(
        ReviseScriptCommand(
            "workspace-a",
            created.script_id,
            1,
            SOURCE.replace("别回头", "快离开"),
            "revise-1",
            "润色第二场对白",
        )
    )

    with pytest.raises(ContentConflict, match="VERSION_CONFLICT"):
        service.revise_script(ReviseScriptCommand("workspace-a", created.script_id, 1, SOURCE, "stale-1", "过期保存"))

    assert [version.number for version in winner.versions] == [1, 2]
    assert (
        service.get_script(workspace_id="workspace-a", script_id=created.script_id).versions[0].source_content == SOURCE
    )
    with repository._session_factory() as session:
        versions = session.scalars(select(ScriptVersionRow).order_by(ScriptVersionRow.number)).all()
    assert [row.number for row in versions] == [1, 2]


def test_idempotency_replays_original_document_but_rejects_changed_request(
    repository: SqlAlchemyContentRepository,
) -> None:
    service = ContentService(repository)
    first = service.import_script(command(key="same-request"))

    assert service.import_script(command(key="same-request")) == first
    with pytest.raises(ContentConflict, match="IDEMPOTENCY_KEY_REUSED"):
        service.import_script(
            ImportScriptCommand(
                "workspace-a", "project-a", "雨夜", "rain.md", "text/markdown", SOURCE + "尾声", "same-request"
            )
        )


def test_director_profile_and_frozen_version_survive_database_reload(repository: SqlAlchemyContentRepository) -> None:
    service = ContentService(repository)
    created = service.import_script(command())
    directed = service.set_director_profile(
        SetDirectorProfileCommand(
            "workspace-a",
            created.script_id,
            1,
            "director-1",
            DirectorProfile(audience="悬疑短剧用户", pacing="快节奏", production_constraints=("单集 90 秒",)),
        )
    )
    frozen = service.freeze_script(
        workspace_id="workspace-a",
        script_id=created.script_id,
        expected_revision=2,
        idempotency_key="freeze-1",
    )

    reloaded = service.get_script(workspace_id="workspace-a", script_id=created.script_id)

    assert directed.director_profile.pacing == "快节奏"
    assert frozen.locked_version_number == 1
    assert reloaded == frozen


def test_schema_compiles_for_postgresql_without_sqlite_only_features() -> None:
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in ContentPersistenceBase.metadata.sorted_tables
    )

    assert "CREATE TABLE xingjing_content_scripts" in ddl
    assert "CREATE TABLE xingjing_content_script_versions" in ddl
    assert "CREATE TABLE xingjing_content_source_mappings" in ddl
    assert "AUTOINCREMENT" not in ddl
