"""complete M12 review delivery

Revision ID: k2e5c9b3d716
Revises: j1d4f8a2c605
Create Date: 2026-07-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k2e5c9b3d716"
down_revision: str | Sequence[str] | None = "j1d4f8a2c605"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M12 review persistence requires PostgreSQL")
    op.add_column("xingjing_review_sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("xingjing_review_sessions", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE xingjing_review_sessions SET last_seen_at = created_at WHERE last_seen_at IS NULL")
    op.alter_column("xingjing_review_sessions", "last_seen_at", nullable=False)
    op.add_column("xingjing_review_comments", sa.Column("parent_comment_id", sa.String(128), nullable=True))
    op.add_column("xingjing_review_links", sa.Column("watermark_text", sa.String(255), nullable=True))
    op.create_foreign_key(
        "fk_xj_review_comment_parent", "xingjing_review_comments", "xingjing_review_comments",
        ["tenant_id", "workspace_id", "parent_comment_id"], ["tenant_id", "workspace_id", "id"], ondelete="RESTRICT",
    )
    op.create_table(
        "xingjing_review_screenshots",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("review_link_id", sa.String(128), nullable=False),
        sa.Column("final_video_version_id", sa.String(128), nullable=False),
        sa.Column("uploader_session_id", sa.String(128), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column("timecode_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "review_link_id"],
            ["xingjing_review_links.tenant_id", "xingjing_review_links.workspace_id", "xingjing_review_links.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "workspace_id", "object_key", name="uq_xj_review_screenshot_object_key"),
        sa.CheckConstraint("size_bytes > 0", name="ck_xj_review_screenshot_size"),
        sa.CheckConstraint("timecode_ms >= 0", name="ck_xj_review_screenshot_timecode"),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("M12 review persistence requires PostgreSQL")
    op.drop_table("xingjing_review_screenshots")
    op.drop_constraint("fk_xj_review_comment_parent", "xingjing_review_comments", type_="foreignkey")
    op.drop_column("xingjing_review_comments", "parent_comment_id")
    op.drop_column("xingjing_review_links", "watermark_text")
    op.drop_column("xingjing_review_sessions", "revoked_at")
    op.drop_column("xingjing_review_sessions", "last_seen_at")
