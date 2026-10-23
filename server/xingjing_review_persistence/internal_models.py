"""Internal staff review rounds, separate from customer review links."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class InternalReviewBase(DeclarativeBase):
    pass


class InternalReviewRoundRow(InternalReviewBase):
    __tablename__ = "xingjing_internal_review_rounds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "project_id", "number", name="uq_xj_internal_round_number"),
        Index("ix_xj_internal_round_project", "tenant_id", "workspace_id", "project_id", "created_at", "id"),
        CheckConstraint("version >= 1 AND number >= 1", name="ck_xj_internal_round_version"),
        CheckConstraint("state IN ('collecting','approved','changes_requested')", name="ck_xj_internal_round_state"),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InternalReviewCommentRow(InternalReviewBase):
    __tablename__ = "xingjing_internal_review_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "round_id"),
            (
                "xingjing_internal_review_rounds.tenant_id",
                "xingjing_internal_review_rounds.workspace_id",
                "xingjing_internal_review_rounds.id",
            ),
            ondelete="RESTRICT",
            name="fk_xj_internal_comment_round",
        ),
        Index("ix_xj_internal_comment_round", "tenant_id", "workspace_id", "round_id", "timecode_ms", "created_at"),
        CheckConstraint("version >= 1 AND timecode_ms >= 0", name="ck_xj_internal_comment_version"),
        CheckConstraint("status IN ('open','in_progress','resolved','reopened')", name="ck_xj_internal_comment_status"),
        CheckConstraint("severity IN ('note','minor','major','critical')", name="ck_xj_internal_comment_severity"),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    round_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    author_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timecode_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_id: Mapped[str | None] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_comment_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InternalReviewCommandRow(InternalReviewBase):
    __tablename__ = "xingjing_internal_review_commands"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
