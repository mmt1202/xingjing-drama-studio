from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

M06_TABLES = {
    "xingjing_generation_tasks",
    "xingjing_generation_provider_evidence",
    "xingjing_generation_cost_evidence",
    "xingjing_generation_audit",
}
M03_REVISION = "b5c3d1e7f209"


def _config(database: Path, monkeypatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    return config


def test_m06_upgrade_from_current_head_creates_generation_schema_with_constraints(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "m06-migration.db"
    config = _config(database, monkeypatch)

    command.upgrade(config, "a4b7c2d8e910")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    inspector = inspect(engine)
    assert M06_TABLES <= set(inspector.get_table_names())
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("xingjing_generation_tasks")} == {
        "uq_xj_generation_idempotency"
    }
    assert {
        constraint["name"] for constraint in inspector.get_unique_constraints("xingjing_generation_provider_evidence")
    } == {"uq_xj_generation_provider_evidence"}
    assert {column["name"] for column in inspector.get_columns("xingjing_generation_tasks")} >= {
        "workspace_id",
        "project_id",
        "task_id",
        "idempotency_scope",
        "version",
        "status",
        "snapshot",
        "created_at",
        "updated_at",
    }


def test_m06_downgrade_removes_only_generation_tables(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "m06-downgrade.db"
    config = _config(database, monkeypatch)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert M06_TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(config, M03_REVISION)

    tables = set(inspect(engine).get_table_names())
    assert not M06_TABLES & tables
    assert "xingjing_content_scripts" in tables
    assert "xingjing_editing_audit_events" in tables
