"""Persist budget limits, cost attribution and native image execution."""

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from server.xingjing_budget.models import BudgetBase, BudgetPolicyRow
from server.xingjing_generation_runtime.native_image.models import NativeImageBase
from server.xingjing_identity_context.budget_permissions import backfill_builtin_budget_permissions
from server.xingjing_model_control.runtime import ModelRouteReleaseRow

revision = "f4c9cc34000e"
down_revision = "e3b8bb23000d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    BudgetBase.metadata.create_all(connection, checkfirst=True)
    NativeImageBase.metadata.create_all(connection, checkfirst=True)
    ModelRouteReleaseRow.__table__.create(connection, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(connection).get_columns("xingjing_model_pricing")}
    if "successful_output_minor" not in columns:
        op.add_column("xingjing_model_pricing", sa.Column("successful_output_minor", sa.Integer(), nullable=True))
    projects = sa.table(
        "xingjing_projects",
        sa.column("tenant_id"),
        sa.column("workspace_id"),
        sa.column("id"),
        sa.column("configuration", sa.JSON()),
    )
    policy = BudgetPolicyRow.__table__
    now = datetime.now(UTC)
    for row in connection.execute(sa.select(projects)).mappings():
        configuration = row["configuration"]
        limit = configuration.get("budgetMinor") if isinstance(configuration, dict) else None
        if limit is None:
            continue
        if type(limit) is not int or limit < 0:
            raise RuntimeError("Existing project budget is invalid; no budget policy can be inferred")
        identity = {
            "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"],
            "project_id": row["id"],
            "scope_key": "project",
        }
        if (
            connection.execute(
                sa.select(policy.c.project_id).where(*(policy.c[name] == value for name, value in identity.items()))
            ).first()
            is None
        ):
            connection.execute(
                policy.insert().values(
                    **identity,
                    episode_id=None,
                    title=None,
                    currency="CNY",
                    limit_minor=limit,
                    active=True,
                    confirmed=True,
                    version=1,
                    updated_by="project-budget-migration",
                    updated_at=now,
                )
            )
    backfill_builtin_budget_permissions(connection)


def downgrade() -> None:
    raise RuntimeError("Budget policies, cost attribution and execution evidence are retained")
