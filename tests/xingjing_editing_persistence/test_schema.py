from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from server.xingjing_editing_persistence import EditingPersistenceBase


def test_schema_compiles_for_postgresql_with_named_portable_constraints() -> None:
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in EditingPersistenceBase.metadata.sorted_tables
    )
    constraint_names = {
        constraint.name
        for table in EditingPersistenceBase.metadata.tables.values()
        for constraint in table.constraints
        if constraint.name is not None
    }

    assert "JSON" in ddl
    assert "xingjing_editing_render_tasks" in ddl
    assert "xingjing_editing_final_video_versions" in ddl
    assert {
        "uq_xj_editing_render_idempotency",
        "uq_xj_editing_final_video_deduplication",
        "pk_xj_editing_render_callback_receipts",
        "ck_xj_editing_render_task_revision",
    }.issubset(constraint_names)
