from __future__ import annotations

from pathlib import Path
from typing import cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, Table

from server.xingjing_storyboard_persistence import PromptTemplateRow, SmartStoryboardRequestRow


def test_m05_migration_registers_postgresql_compatible_storyboard_tables() -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    revision = ScriptDirectory.from_config(config).get_revision("e5c7a9d2f104")

    assert revision is not None
    assert revision.down_revision == "d4b9f6e2a018"
    tables = (
        cast(Table, PromptTemplateRow.__table__),
        cast(Table, SmartStoryboardRequestRow.__table__),
    )
    for table in tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert table.name in ddl
