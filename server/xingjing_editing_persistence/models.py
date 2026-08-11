from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EditingPersistenceBase(DeclarativeBase):
    """M08 持久化表的独立 metadata，供主线迁移显式接入。"""


class TimelineHeadRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_timeline_heads"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timeline_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_id: Mapped[str] = mapped_column(String(128), nullable=False)
    current_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "timeline_id",
            name="pk_xj_editing_timeline_heads",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "final_video_id",
            name="uq_xj_editing_timeline_final_video",
        ),
        CheckConstraint("current_revision >= 1", name="ck_xj_editing_timeline_current_revision"),
    )


class TimelineVersionRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_timeline_versions"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timeline_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(String(128))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "timeline_id",
            "version_id",
            name="pk_xj_editing_timeline_versions",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "timeline_id"),
            (
                "xingjing_editing_timeline_heads.tenant_id",
                "xingjing_editing_timeline_heads.workspace_id",
                "xingjing_editing_timeline_heads.timeline_id",
            ),
            name="fk_xj_editing_timeline_version_head",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "timeline_id",
            "revision",
            name="uq_xj_editing_timeline_revision",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "project_id",
            "timeline_id",
            "version_id",
            name="uq_xj_editing_timeline_project_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "version_id",
            name="uq_xj_editing_timeline_version_scope",
        ),
        CheckConstraint("revision >= 1", name="ck_xj_editing_timeline_version_revision"),
    )


class TimelineIdempotencyRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_timeline_idempotency"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    timeline_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version_id: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "idempotency_key",
            name="pk_xj_editing_timeline_idempotency",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "timeline_id", "version_id"),
            (
                "xingjing_editing_timeline_versions.tenant_id",
                "xingjing_editing_timeline_versions.workspace_id",
                "xingjing_editing_timeline_versions.timeline_id",
                "xingjing_editing_timeline_versions.version_id",
            ),
            name="fk_xj_editing_timeline_idempotency_version",
            ondelete="RESTRICT",
        ),
    )


class RenderTaskRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_render_tasks"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timeline_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timeline_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    output_version_id: Mapped[str | None] = mapped_column(String(128))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "task_id",
            name="pk_xj_editing_render_tasks",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "project_id", "timeline_id", "timeline_version_id"),
            (
                "xingjing_editing_timeline_versions.tenant_id",
                "xingjing_editing_timeline_versions.workspace_id",
                "xingjing_editing_timeline_versions.project_id",
                "xingjing_editing_timeline_versions.timeline_id",
                "xingjing_editing_timeline_versions.version_id",
            ),
            name="fk_xj_editing_render_task_timeline",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "idempotency_key",
            name="uq_xj_editing_render_idempotency",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "project_id",
            "task_id",
            name="uq_xj_editing_render_project_task",
        ),
        CheckConstraint("task_revision >= 1", name="ck_xj_editing_render_task_revision"),
        CheckConstraint("attempt >= 0", name="ck_xj_editing_render_task_attempt"),
        Index(
            "ix_xj_editing_render_deadline",
            "status",
            "deadline_at",
            "task_id",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'cancelling', 'cancelled', 'failed', 'succeeded')",
            name="ck_xj_editing_render_task_status",
        ),
    )


class RenderCallbackReceiptRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_render_callback_receipts"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    task_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "event_id",
            name="pk_xj_editing_render_callback_receipts",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "task_id"),
            (
                "xingjing_editing_render_tasks.tenant_id",
                "xingjing_editing_render_tasks.workspace_id",
                "xingjing_editing_render_tasks.task_id",
            ),
            name="fk_xj_editing_render_callback_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint("task_revision >= 1", name="ck_xj_editing_render_callback_revision"),
    )


class FinalVideoVersionRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_final_video_versions"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timeline_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timeline_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    render_task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "final_video_id",
            "version_id",
            name="pk_xj_editing_final_video_versions",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "project_id", "timeline_id", "timeline_version_id"),
            (
                "xingjing_editing_timeline_versions.tenant_id",
                "xingjing_editing_timeline_versions.workspace_id",
                "xingjing_editing_timeline_versions.project_id",
                "xingjing_editing_timeline_versions.timeline_id",
                "xingjing_editing_timeline_versions.version_id",
            ),
            name="fk_xj_editing_final_video_timeline",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "project_id", "render_task_id"),
            (
                "xingjing_editing_render_tasks.tenant_id",
                "xingjing_editing_render_tasks.workspace_id",
                "xingjing_editing_render_tasks.project_id",
                "xingjing_editing_render_tasks.task_id",
            ),
            name="fk_xj_editing_final_video_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "version_id",
            name="uq_xj_editing_final_video_version_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "final_video_id",
            "deduplication_key",
            name="uq_xj_editing_final_video_deduplication",
        ),
    )


class RenderOutboxRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_render_outbox"

    outbox_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_xj_editing_render_outbox_pending",
            "published_at",
            "created_at",
            "outbox_key",
        ),
    )


class EditingRenderBillingHoldRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_render_billing_holds"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    released_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pricing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    terminal_event_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id", "workspace_id", "project_id", "task_id", name="pk_xj_editing_render_billing_hold"
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "project_id", "task_id"),
            (
                "xingjing_editing_render_tasks.tenant_id",
                "xingjing_editing_render_tasks.workspace_id",
                "xingjing_editing_render_tasks.project_id",
                "xingjing_editing_render_tasks.task_id",
            ),
            name="fk_xj_editing_render_billing_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint("estimated_minor > 0", name="ck_xj_editing_render_billing_estimated"),
        CheckConstraint("actual_minor >= 0 AND released_minor >= 0", name="ck_xj_editing_render_billing_amounts"),
        CheckConstraint(
            "actual_minor + released_minor <= estimated_minor",
            name="ck_xj_editing_render_billing_conservation",
        ),
        CheckConstraint(
            "status IN ('active','settled','released')",
            name="ck_xj_editing_render_billing_status",
        ),
    )


class EditingRenderBillingJournalRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_render_billing_journals"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    postings: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    reference: Mapped[str] = mapped_column(String(256), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("action IN ('freeze','settle','release','refreeze')", name="ck_xj_editing_billing_action"),
        CheckConstraint("amount_minor >= 0", name="ck_xj_editing_billing_journal_amount"),
        Index(
            "ix_xj_editing_billing_project_time",
            "tenant_id",
            "workspace_id",
            "project_id",
            "occurred_at",
            "event_id",
        ),
    )


class FinalVideoSelectionRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_final_video_selections"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_video_id: Mapped[str] = mapped_column(String(128), nullable=False)
    selected_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "project_id",
            "final_video_id",
            name="pk_xj_editing_final_video_selection",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "workspace_id", "final_video_id", "selected_version_id"),
            (
                "xingjing_editing_final_video_versions.tenant_id",
                "xingjing_editing_final_video_versions.workspace_id",
                "xingjing_editing_final_video_versions.final_video_id",
                "xingjing_editing_final_video_versions.version_id",
            ),
            name="fk_xj_editing_final_video_selection_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 1", name="ck_xj_editing_final_video_selection_revision"),
    )


class FinalVideoSelectionReceiptRow(EditingPersistenceBase):
    __tablename__ = "xingjing_editing_final_video_selection_receipts"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "idempotency_key",
            name="pk_xj_editing_final_video_selection_receipt",
        ),
    )


class EditingAuditRow(EditingPersistenceBase):
    """Append-only audit evidence for timeline and render actions.

    The application never updates or deletes these rows.  The database identity
    is scoped by tenant and workspace so a replay from one workspace cannot
    overwrite another workspace's audit evidence.
    """

    __tablename__ = "xingjing_editing_audit_events"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(128))
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(128), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    before_sha256: Mapped[str | None] = mapped_column(String(64))
    after_sha256: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "workspace_id",
            "event_id",
            name="pk_xj_editing_audit_events",
        ),
        Index(
            "ix_xj_editing_audit_scope_time",
            "tenant_id",
            "workspace_id",
            "occurred_at",
            "event_id",
        ),
        Index(
            "ix_xj_editing_audit_scope_request",
            "tenant_id",
            "workspace_id",
            "request_id",
        ),
        Index(
            "ix_xj_editing_audit_project_time",
            "tenant_id",
            "workspace_id",
            "project_id",
            "occurred_at",
            "event_id",
        ),
    )
