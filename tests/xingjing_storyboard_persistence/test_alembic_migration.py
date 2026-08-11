from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_alembic_head_creates_storyboard_tables(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "storyboard-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert {
        "xingjing_storyboards",
        "xingjing_storyboard_idempotency",
        "xingjing_storyboard_audit",
        "xingjing_storyboard_import_uploads",
    } <= set(inspect(engine).get_table_names())
