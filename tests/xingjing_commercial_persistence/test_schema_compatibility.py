from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from server.xingjing_commercial_persistence import (
    CommercialOrderRow,
    CommercialPersistenceBase,
)


def test_schema_and_order_cas_compile_for_sqlite_and_postgresql() -> None:
    statement = (
        update(CommercialOrderRow)
        .where(
            CommercialOrderRow.owner_workspace_id == "workspace-owner",
            CommercialOrderRow.id == "order-1",
            CommercialOrderRow.version == 7,
        )
        .values(status="accepted", version=8, aggregate={"version": 8})
    )

    for dialect in (sqlite.dialect(), postgresql.dialect()):
        table_ddl = "\n".join(
            str(CreateTable(table).compile(dialect=dialect))
            for table in CommercialPersistenceBase.metadata.sorted_tables
        )
        index_ddl = "\n".join(
            str(CreateIndex(index).compile(dialect=dialect))
            for table in CommercialPersistenceBase.metadata.sorted_tables
            for index in table.indexes
        )
        cas_sql = str(statement.compile(dialect=dialect))

        assert "xingjing_commercial_orders" in table_ddl
        assert "xingjing_commercial_commands" in table_ddl
        assert "xingjing_commercial_audit" in table_ddl
        assert "owner_workspace_id" in cas_sql
        assert "version" in cas_sql
        assert "ix_xj_commercial_audit_workspace_object" in index_ddl
