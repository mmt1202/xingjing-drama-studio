"""Cover plans own mutable drafts; selections freeze publishable combinations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CoverBase(DeclarativeBase):
    pass


class CoverPlanRow(CoverBase):
    __tablename__ = "xingjing_cover_plans"
    __table_args__ = (
        Index("ix_xj_cover_plan_scope", "tenant_id", "workspace_id", "project_id", "created_at", "id"),
        CheckConstraint("version >= 1", name="ck_xj_cover_plan_version"),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    final_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False)
    title_safe_area: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    candidates: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    generation_task_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    selected_variant_id: Mapped[str | None] = mapped_column(String(128))
    recommended_variant_id: Mapped[str | None] = mapped_column(String(128))
    applied_selection_id: Mapped[str | None] = mapped_column(String(128))
    pending_command: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoverSelectionRow(CoverBase):
    """An immutable selection, independent of later plan or variant edits."""

    __tablename__ = "xingjing_cover_selections"
    __table_args__ = (
        Index(
            "ix_xj_cover_selection_source",
            "tenant_id",
            "workspace_id",
            "project_id",
            "final_version_id",
            "target",
            "created_at",
            "id",
        ),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    final_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoverCommandRow(CoverBase):
    __tablename__ = "xingjing_cover_commands"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoverAuditRow(CoverBase):
    __tablename__ = "xingjing_cover_audit"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
