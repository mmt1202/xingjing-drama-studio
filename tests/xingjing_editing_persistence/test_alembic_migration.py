from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_alembic_head_creates_editing_tables(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "editing-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "xingjing_editing_timeline_heads",
        "xingjing_editing_timeline_versions",
        "xingjing_editing_timeline_idempotency",
        "xingjing_editing_render_tasks",
        "xingjing_editing_render_callback_receipts",
        "xingjing_editing_final_video_versions",
        "xingjing_editing_render_outbox",
    } <= tables


def test_editing_audit_table_is_added_after_the_original_editing_revision(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "editing-audit-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))

    command.upgrade(config, "d3e8f5a1b640")
    before = set(inspect(create_engine(f"sqlite:///{database.as_posix()}")).get_table_names())
    assert "xingjing_editing_audit_events" not in before

    command.upgrade(config, "head")
    after = set(inspect(create_engine(f"sqlite:///{database.as_posix()}")).get_table_names())
    assert "xingjing_editing_audit_events" in after
