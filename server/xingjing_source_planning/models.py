"""Immutable source/proposal data and once-only confirmations."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SourcePlanningBase(DeclarativeBase):
    pass


class SourceSnapshotRow(SourcePlanningBase):
    __tablename__ = "xingjing_source_snapshots"
    __table_args__ = (Index("ix_xj_source_snapshot_scope", "tenant_id", "workspace_id", "project_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    option: Mapped[dict] = mapped_column(JSON, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chapters: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EpisodePlanRow(SourcePlanningBase):
    __tablename__ = "xingjing_episode_source_plans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "project_id", "actor_id", "request_key", name="uq_xj_source_plan_request"),
        Index("ix_xj_source_plan_scope", "tenant_id", "workspace_id", "project_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    episode_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal: Mapped[dict] = mapped_column(JSON, nullable=False)
    base_edits: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlanConfirmationRow(SourcePlanningBase):
    __tablename__ = "xingjing_episode_plan_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "project_id", "actor_id", "request_key", name="uq_xj_plan_confirmation_request"
        ),
        UniqueConstraint("plan_id", name="uq_xj_plan_confirmation_once"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
