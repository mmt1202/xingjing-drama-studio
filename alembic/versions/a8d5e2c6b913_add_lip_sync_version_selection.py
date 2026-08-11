"""add_lip_sync_version_selection

Revision ID: a8d5e2c6b913
Revises: b7d4e9a1c621
Create Date: 2026-07-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8d5e2c6b913"
down_revision: str | Sequence[str] | None = "b7d4e9a1c621"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M07 audio persistence requires PostgreSQL; SQLite is not a production substitute")
    table = "xingjing_lip_sync_versions"
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns(table)}
    upgrading_legacy_output = "output_size_bytes" not in existing_columns
    additions = (
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("selected_by", sa.String(length=128), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_task_id", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    for column in additions:
        if column.name not in existing_columns:
            op.add_column(table, column)

    foreign_keys = {item["name"] for item in inspector.get_foreign_keys(table)}
    checks = {item["name"] for item in inspector.get_check_constraints(table)}
    indexes = {item["name"] for item in inspector.get_indexes(table)}
    if "fk_xj_lip_sync_source_task_scope" not in foreign_keys:
        op.create_foreign_key(
            "fk_xj_lip_sync_source_task_scope", table, "xingjing_audio_generation_tasks",
            ["source_task_id", "tenant_id", "workspace_id", "project_id"],
            ["id", "tenant_id", "workspace_id", "project_id"], ondelete="RESTRICT",
        )
    if "ck_xj_lip_sync_version" not in checks:
        op.create_check_constraint("ck_xj_lip_sync_version", table, "version >= 1")
    if upgrading_legacy_output:
        op.drop_constraint("ck_xj_lip_sync_output_reference", table, type_="check")
        op.create_check_constraint(
            "ck_xj_lip_sync_output_reference", table,
            "(output_video_key IS NULL AND output_sha256 IS NULL AND output_size_bytes IS NULL AND mime_type IS NULL) "
            "OR (output_video_key IS NOT NULL AND output_sha256 IS NOT NULL AND output_size_bytes IS NOT NULL AND mime_type IS NOT NULL)",
        )
    if "ck_xj_lip_sync_output_size" not in checks:
        op.create_check_constraint("ck_xj_lip_sync_output_size", table, "output_size_bytes IS NULL OR output_size_bytes > 0")
    if "ix_xj_lip_sync_scope_input" not in indexes:
        op.create_index("ix_xj_lip_sync_scope_input", table, ["tenant_id", "workspace_id", "project_id", "input_video_key", "updated_at"])
    if "uq_xj_lip_sync_selected_input" not in indexes:
        op.create_index(
            "uq_xj_lip_sync_selected_input", table,
            ["tenant_id", "workspace_id", "project_id", "input_video_key"], unique=True,
            postgresql_where=sa.text("is_selected IS TRUE"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M07 audio persistence requires PostgreSQL; SQLite is not a production substitute")
    op.drop_index("uq_xj_lip_sync_selected_input", table_name="xingjing_lip_sync_versions")
    op.drop_index("ix_xj_lip_sync_scope_input", table_name="xingjing_lip_sync_versions")
    op.drop_constraint("ck_xj_lip_sync_output_size", "xingjing_lip_sync_versions", type_="check")
    op.drop_constraint("ck_xj_lip_sync_output_reference", "xingjing_lip_sync_versions", type_="check")
    op.create_check_constraint(
        "ck_xj_lip_sync_output_reference", "xingjing_lip_sync_versions",
        "(output_video_key IS NULL AND output_sha256 IS NULL) OR (output_video_key IS NOT NULL AND output_sha256 IS NOT NULL)",
    )
    op.drop_constraint("ck_xj_lip_sync_version", "xingjing_lip_sync_versions", type_="check")
    op.drop_constraint("fk_xj_lip_sync_source_task_scope", "xingjing_lip_sync_versions", type_="foreignkey")
    for name in ("updated_at", "source_task_id", "selected_at", "selected_by", "is_selected", "version", "mime_type", "output_size_bytes"):
        op.drop_column("xingjing_lip_sync_versions", name)
