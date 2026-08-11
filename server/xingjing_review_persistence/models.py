"""M12 客户审片的 PostgreSQL metadata。

这些表由主线 Alembic 迁移创建；本模块绝不在运行时执行 metadata.create_all()。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ReviewPersistenceBase(DeclarativeBase):
    """审片持久化的独立 metadata，供主线迁移显式汇集。"""


class ReviewLinkRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_links"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_xj_review_link_token_digest"),
        Index("ix_xj_review_link_scope_project", "tenant_id", "workspace_id", "project_id", "created_at", "id"),
        CheckConstraint("version >= 1", name="ck_xj_review_link_version"),
        CheckConstraint("state IN ('active', 'revoked')", name="ck_xj_review_link_state"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    access_secret_digest: Mapped[str | None] = mapped_column(String(128))
    comment_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approve_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    download_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    watermark_text: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewSessionRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "review_link_id"),
            ("xingjing_review_links.tenant_id", "xingjing_review_links.workspace_id", "xingjing_review_links.id"),
            ondelete="RESTRICT",
        ),
        Index("ix_xj_review_session_link_created", "tenant_id", "workspace_id", "review_link_id", "created_at", "id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_link_id: Mapped[str] = mapped_column(String(128), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewCommentRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "review_link_id"),
            ("xingjing_review_links.tenant_id", "xingjing_review_links.workspace_id", "xingjing_review_links.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "parent_comment_id"),
            ("xingjing_review_comments.tenant_id", "xingjing_review_comments.workspace_id", "xingjing_review_comments.id"),
            name="fk_xj_review_comment_parent", ondelete="RESTRICT",
        ),
        Index("ix_xj_review_comment_scope_link_time", "tenant_id", "workspace_id", "review_link_id", "timecode_ms", "created_at", "id"),
        Index("ix_xj_review_comment_scope_version", "tenant_id", "workspace_id", "final_video_version_id", "created_at", "id"),
        CheckConstraint("timecode_ms >= 0", name="ck_xj_review_comment_timecode"),
        CheckConstraint("version >= 1", name="ck_xj_review_comment_version"),
        CheckConstraint("status IN ('open', 'in_progress', 'resolved', 'reopened')", name="ck_xj_review_comment_status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_link_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    author_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timecode_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    screenshot_asset_id: Mapped[str | None] = mapped_column(String(128))
    parent_comment_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewScreenshotRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_screenshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "review_link_id"),
            ("xingjing_review_links.tenant_id", "xingjing_review_links.workspace_id", "xingjing_review_links.id"),
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "object_key", name="uq_xj_review_screenshot_object_key"),
        CheckConstraint("size_bytes > 0", name="ck_xj_review_screenshot_size"),
        CheckConstraint("timecode_ms >= 0", name="ck_xj_review_screenshot_timecode"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_link_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    uploader_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timecode_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewApprovalRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "review_link_id"),
            ("xingjing_review_links.tenant_id", "xingjing_review_links.workspace_id", "xingjing_review_links.id"),
            ondelete="RESTRICT",
        ),
        Index("ix_xj_review_approval_scope_link_created", "tenant_id", "workspace_id", "review_link_id", "created_at", "id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_link_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewDeliveryRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "review_link_id"),
            ("xingjing_review_links.tenant_id", "xingjing_review_links.workspace_id", "xingjing_review_links.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_xj_review_delivery_version"),
        CheckConstraint("status IN ('pending', 'approved', 'changes_requested', 'confirmed')", name="ck_xj_review_delivery_status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_link_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    final_video_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(128))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewIdempotencyRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_idempotency"
    __table_args__ = (Index("ix_xj_review_idempotency_scope_object", "tenant_id", "workspace_id", "result_id"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    result_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    result_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAuditRow(ReviewPersistenceBase):
    __tablename__ = "xingjing_review_audit_events"
    __table_args__ = (
        Index("ix_xj_review_audit_scope_object_time", "tenant_id", "workspace_id", "object_type", "object_id", "occurred_at", "event_id"),
        Index("ix_xj_review_audit_scope_request", "tenant_id", "workspace_id", "request_id"),
        Index("ix_xj_review_audit_scope_actor", "tenant_id", "workspace_id", "actor_id", "occurred_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
