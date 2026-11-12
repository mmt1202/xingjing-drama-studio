"""Separate local execution identities from upstream request and job identities."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class NativeImageBase(DeclarativeBase):
    pass


class NativeImageExecutionRow(NativeImageBase):
    __tablename__ = "xingjing_native_image_executions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "project_id", "task_id", name="uq_xj_native_image_task"),
        CheckConstraint(
            "status IN ('queued','preparing','dispatching','unknown','result_ready','succeeded','failed','cancelled')",
            name="ck_xj_native_image_status",
        ),
        CheckConstraint(
            "successful_output_minor >= 0 AND successful_output_minor <= estimated_minor",
            name="ck_xj_native_image_quote",
        ),
        Index("ix_xj_native_image_pending", "status", "updated_at"),
    )
    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    registry_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    credential_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    successful_output_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    upstream_request_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
