from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_alembic_head_creates_marketplace_tables(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "marketplace-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "xingjing_marketplace_templates",
        "xingjing_marketplace_template_versions",
        "xingjing_marketplace_items",
        "xingjing_marketplace_forks",
        "xingjing_marketplace_idempotency",
        "xingjing_marketplace_audit_events",
    } <= tables
