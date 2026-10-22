"""Keep each rendered version's export configuration independently."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8e2a4c6d901f"
down_revision: str | Sequence[str] | None = "7d174f942017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "xingjing_compliance_export_configurations"
BASE_KEY = ["tenant_id", "workspace_id", "project_id", "target"]
VERSION_KEY = [*BASE_KEY, "snapshot_version"]


def _replace_key(columns: list[str]) -> None:
    connection = op.get_bind()
    primary = sa.inspect(connection).get_pk_constraint(TABLE)
    if primary["constrained_columns"] == columns:
        return
    name = primary["name"] or f"pk_{TABLE}"
    with op.batch_alter_table(TABLE, naming_convention={"pk": "pk_%(table_name)s"}) as batch:
        batch.drop_constraint(name, type_="primary")
        batch.create_primary_key(f"pk_{TABLE}", columns)


def upgrade() -> None:
    _replace_key(VERSION_KEY)


def downgrade() -> None:
    # Do not discard another episode's approval to make the older key unique.
    table = sa.table(TABLE, *(sa.column(column) for column in BASE_KEY))
    duplicates = (
        op.get_bind()
        .execute(sa.select(sa.func.count()).select_from(table).group_by(*table.c).having(sa.func.count() > 1).limit(1))
        .first()
    )
    if duplicates:
        raise RuntimeError("Multiple version export configurations exist; resolve them before downgrading")
    _replace_key(BASE_KEY)
