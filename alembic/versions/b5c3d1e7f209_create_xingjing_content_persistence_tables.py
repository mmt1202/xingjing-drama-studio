"""create Xingjing M03 content persistence tables

Revision ID: b5c3d1e7f209
Revises: a4b7c2d8e910
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5c3d1e7f209"
down_revision: str | Sequence[str] | None = "a4b7c2d8e910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "xingjing_content_scripts",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("script_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("source_media_type", sa.String(length=255), nullable=False),
        sa.Column("source_content", sa.String(), nullable=False),
        sa.Column("locked_version_number", sa.Integer(), nullable=True),
        sa.Column("director_profile", sa.JSON(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_xj_content_script_revision"),
        sa.PrimaryKeyConstraint("workspace_id", "script_id"),
    )
    op.create_index(
        "ix_xj_content_script_scope_project",
        "xingjing_content_scripts",
        ["workspace_id", "project_id", "title", "script_id"],
        unique=False,
    )
    op.create_table(
        "xingjing_content_script_versions",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("script_id", sa.String(length=128), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("source_content", sa.String(), nullable=False),
        sa.Column("change_summary", sa.String(length=2048), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.CheckConstraint("number >= 1", name="ck_xj_content_version_number"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "script_id"],
            ["xingjing_content_scripts.workspace_id", "xingjing_content_scripts.script_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "script_id", "number"),
    )
    op.create_table(
        "xingjing_content_script_scenes",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("script_id", sa.String(length=128), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("scene_id", sa.String(length=128), nullable=False),
        sa.Column("heading", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "script_id", "version_number"],
            [
                "xingjing_content_script_versions.workspace_id",
                "xingjing_content_script_versions.script_id",
                "xingjing_content_script_versions.number",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "script_id", "version_number", "sequence"),
    )
    op.create_table(
        "xingjing_content_script_paragraphs",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("script_id", sa.String(length=128), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("scene_sequence", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("paragraph_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("source_text", sa.String(), nullable=False),
        sa.Column("speaker", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "script_id", "version_number", "scene_sequence"],
            [
                "xingjing_content_script_scenes.workspace_id",
                "xingjing_content_script_scenes.script_id",
                "xingjing_content_script_scenes.version_number",
                "xingjing_content_script_scenes.sequence",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "script_id", "version_number", "scene_sequence", "sequence"),
    )
    op.create_table(
        "xingjing_content_source_mappings",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("script_id", sa.String(length=128), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.CheckConstraint("source_start >= 0", name="ck_xj_content_mapping_start"),
        sa.CheckConstraint("source_end >= source_start", name="ck_xj_content_mapping_range"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "script_id", "version_number"],
            [
                "xingjing_content_script_versions.workspace_id",
                "xingjing_content_script_versions.script_id",
                "xingjing_content_script_versions.number",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "script_id", "version_number", "target_id"),
    )
    op.create_table(
        "xingjing_content_idempotency",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("script_id", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "idempotency_key"),
    )
    op.create_table(
        "xingjing_content_audit_events",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=512), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("script_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("before_payload", sa.JSON(), nullable=False),
        sa.Column("after_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "event_id"),
    )
    op.create_index(
        "ix_xj_content_audit_scope_object_time",
        "xingjing_content_audit_events",
        ["workspace_id", "script_id", "occurred_at", "event_id"],
        unique=False,
    )
    op.create_index(
        "ix_xj_content_audit_scope_request",
        "xingjing_content_audit_events",
        ["workspace_id", "request_id"],
        unique=False,
    )
    op.create_index(
        "ix_xj_content_audit_scope_actor",
        "xingjing_content_audit_events",
        ["workspace_id", "actor_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("xingjing_content_audit_events")
    op.drop_table("xingjing_content_idempotency")
    op.drop_table("xingjing_content_source_mappings")
    op.drop_table("xingjing_content_script_paragraphs")
    op.drop_table("xingjing_content_script_scenes")
    op.drop_table("xingjing_content_script_versions")
    op.drop_table("xingjing_content_scripts")
