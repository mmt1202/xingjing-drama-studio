from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from server.xingjing_storyboard_persistence import Base


def test_schema_compiles_for_postgresql_with_scope_cas_and_upload_constraints() -> None:
    expected_tables = {
        "xingjing_storyboards",
        "xingjing_storyboard_idempotency",
        "xingjing_storyboard_audit",
        "xingjing_storyboard_import_uploads",
    }
    assert set(Base.metadata.tables) == expected_tables

    compiled = {
        name: str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for name, table in Base.metadata.tables.items()
    }
    storyboard_sql = compiled["xingjing_storyboards"]
    idempotency_sql = compiled["xingjing_storyboard_idempotency"]
    upload_sql = compiled["xingjing_storyboard_import_uploads"]

    assert "PRIMARY KEY (tenant_id, workspace_id, project_id, storyboard_id)" in storyboard_sql
    assert "CHECK (version >= 1)" in storyboard_sql
    assert "PRIMARY KEY (tenant_id, workspace_id, project_id, operation_kind, idempotency_key)" in idempotency_sql
    assert "fk_xj_storyboard_upload_consumed_storyboard" in upload_sql
    assert "ck_xj_storyboard_upload_sha256_length" in upload_sql
    assert "ck_xj_storyboard_upload_status" in upload_sql
    assert all("TIMESTAMP WITH TIME ZONE" in sql for sql in compiled.values() if "created_at" in sql)
