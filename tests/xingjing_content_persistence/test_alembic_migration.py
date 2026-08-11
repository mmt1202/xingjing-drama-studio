from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

M03_TABLES = {
    "xingjing_content_scripts",
    "xingjing_content_script_versions",
    "xingjing_content_script_scenes",
    "xingjing_content_script_paragraphs",
    "xingjing_content_source_mappings",
    "xingjing_content_idempotency",
    "xingjing_content_audit_events",
}
M03_REVISION = "b5c3d1e7f209"


def _config(database: Path, monkeypatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    return config


def test_m03_upgrade_from_current_head_creates_content_schema_with_constraints(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "m03-migration.db"
    config = _config(database, monkeypatch)

    command.upgrade(config, "a4b7c2d8e910")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    inspector = inspect(engine)
    assert M03_TABLES <= set(inspector.get_table_names())
    assert {
        foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("xingjing_content_script_versions")
    } == {"xingjing_content_scripts"}
    assert {check["name"] for check in inspector.get_check_constraints("xingjing_content_source_mappings")} == {
        "ck_xj_content_mapping_range",
        "ck_xj_content_mapping_start",
    }
    assert {index["name"] for index in inspector.get_indexes("xingjing_content_audit_events")} == {
        "ix_xj_content_audit_scope_actor",
        "ix_xj_content_audit_scope_object_time",
        "ix_xj_content_audit_scope_request",
    }


def test_m03_downgrade_removes_only_content_tables(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "m03-downgrade.db"
    config = _config(database, monkeypatch)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert M03_TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(config, "a4b7c2d8e910")

    tables = set(inspect(engine).get_table_names())
    assert not M03_TABLES & tables
    assert "xingjing_editing_audit_events" in tables
