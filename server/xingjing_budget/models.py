"""SQL budget policies and immutable production-cost attribution."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BudgetBase(DeclarativeBase):
    pass


class BudgetPolicyRow(BudgetBase):
    __tablename__ = "xingjing_budget_policies"
    __table_args__ = (
        CheckConstraint("limit_minor IS NULL OR limit_minor >= 0", name="ck_xj_budget_limit_nonnegative"),
        CheckConstraint("episode_id IS NULL OR episode_id > 0", name="ck_xj_budget_episode_positive"),
        CheckConstraint("version >= 1", name="ck_xj_budget_policy_version"),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    episode_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    limit_minor: Mapped[int | None] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BudgetTaskAttributionRow(BudgetBase):
    __tablename__ = "xingjing_budget_task_attributions"
    __table_args__ = (
        CheckConstraint("kind IN ('episode', 'project_shared', 'unattributed')", name="ck_xj_budget_target_kind"),
        CheckConstraint("module IN ('generation', 'audio', 'editing')", name="ck_xj_budget_target_module"),
        CheckConstraint(
            "(kind = 'episode' AND episode_id IS NOT NULL AND episode_id > 0) OR (kind <> 'episode' AND episode_id IS NULL)",
            name="ck_xj_budget_target_episode",
        ),
        Index(
            "ix_xj_budget_resource_scope",
            "tenant_id",
            "workspace_id",
            "project_id",
            "module",
            "source_kind",
            "source_reference",
        ),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    module: Mapped[str] = mapped_column(String(16), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    episode_id: Mapped[int | None] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
