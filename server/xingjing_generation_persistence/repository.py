from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from string import hexdigits
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from server.xingjing_generation.contracts import GenerationTask, TaskStatus


class Base(DeclarativeBase):
    """独立 metadata；生产表由主线 Alembic 迁移创建。"""


class TaskRow(Base):
    __tablename__ = "xingjing_generation_tasks"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_scope", name="uq_xj_generation_idempotency"),)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_scope: Mapped[str] = mapped_column(String(257), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderEvidenceRow(Base):
    __tablename__ = "xingjing_generation_provider_evidence"
    __table_args__ = (UniqueConstraint("workspace_id", "evidence_id", name="uq_xj_generation_provider_evidence"),)
    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostEvidenceRow(Base):
    __tablename__ = "xingjing_generation_cost_evidence"
    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRow(Base):
    __tablename__ = "xingjing_generation_audit"
    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationBillingAccountRow(Base):
    __tablename__ = "xingjing_generation_billing_accounts"
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    available_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    held_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spent_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("available_minor >= 0", name="ck_xj_generation_billing_available"),
        CheckConstraint("held_minor >= 0", name="ck_xj_generation_billing_held"),
        CheckConstraint("spent_minor >= 0", name="ck_xj_generation_billing_spent"),
        CheckConstraint("version >= 1", name="ck_xj_generation_billing_account_version"),
    )


class GenerationBillingHoldRow(Base):
    __tablename__ = "xingjing_generation_billing_holds"
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    released_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    terminal_event_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "task_id"],
            ["xingjing_generation_tasks.workspace_id", "xingjing_generation_tasks.project_id", "xingjing_generation_tasks.task_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("estimated_minor > 0", name="ck_xj_generation_hold_estimated"),
        CheckConstraint("actual_minor >= 0", name="ck_xj_generation_hold_actual"),
        CheckConstraint("released_minor >= 0", name="ck_xj_generation_hold_released"),
        CheckConstraint("actual_minor + released_minor <= estimated_minor", name="ck_xj_generation_hold_conservation"),
        CheckConstraint("status IN ('active','settled','released')", name="ck_xj_generation_hold_status"),
    )


class GenerationBillingJournalRow(Base):
    __tablename__ = "xingjing_generation_billing_journals"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    postings: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    reference: Mapped[str] = mapped_column(String(256), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_xj_generation_journal_amount"),
        Index("ix_xj_generation_journal_project_time", "workspace_id", "project_id", "occurred_at"),
    )


class ProviderSubmissionOutboxRow(Base):
    __tablename__ = "xingjing_generation_provider_submission_outbox"
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_job_id: Mapped[str | None] = mapped_column(String(256))
    last_error: Mapped[str | None] = mapped_column(String(2_000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "task_id"],
            ["xingjing_generation_tasks.workspace_id", "xingjing_generation_tasks.project_id", "xingjing_generation_tasks.task_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('pending','accepted','failed')", name="ck_xj_generation_provider_outbox_status"),
        CheckConstraint("attempts >= 0", name="ck_xj_generation_provider_outbox_attempts"),
        Index("ix_xj_generation_provider_outbox_recovery", "status", "updated_at"),
    )


class GenerationProgressRow(Base):
    __tablename__ = "xingjing_generation_progress"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    percent: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "task_id"],
            ["xingjing_generation_tasks.workspace_id", "xingjing_generation_tasks.project_id", "xingjing_generation_tasks.task_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("percent >= 0 AND percent <= 100", name="ck_xj_generation_progress_percent"),
        CheckConstraint("eta_seconds IS NULL OR eta_seconds >= 0", name="ck_xj_generation_progress_eta"),
        Index("ix_xj_generation_progress_task_time", "workspace_id", "project_id", "task_id", "occurred_at"),
    )


class LegacyQueueDispatchRow(Base):
    """Durable bridge from one M06 task to one legacy worker task.

    ``legacy_task_id`` is intentionally separate from ``provider_job_id``:
    the former identifies ArcReel's local worker queue, while the latter is
    issued later by an external model provider.  Collapsing them loses restart
    recovery and makes callback validation unsafe.
    """

    __tablename__ = "xingjing_generation_legacy_dispatches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "task_id"],
            [
                "xingjing_generation_tasks.workspace_id",
                "xingjing_generation_tasks.project_id",
                "xingjing_generation_tasks.task_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "project_id", "task_id", name="uq_xj_generation_dispatch_task"),
        UniqueConstraint("legacy_task_id", name="uq_xj_generation_dispatch_legacy_task"),
        Index("ix_xj_generation_dispatch_reconcile", "dispatch_status", "updated_at"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    legacy_task_id: Mapped[str | None] = mapped_column(String(128))
    dispatch_status: Mapped[str] = mapped_column(String(32), nullable=False)
    legacy_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(String(2_000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GeneratedAssetRow(Base):
    """One immutable, managed media artifact produced for an M06 task."""

    __tablename__ = "xingjing_generated_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "task_id"],
            [
                "xingjing_generation_tasks.workspace_id",
                "xingjing_generation_tasks.project_id",
                "xingjing_generation_tasks.task_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "object_key", name="uq_xj_generated_asset_object"),
        CheckConstraint("size_bytes > 0", name="ck_xj_generated_asset_size_positive"),
        CheckConstraint("length(content_sha256) = 64", name="ck_xj_generated_asset_sha256_length"),
        CheckConstraint("media_type IN ('image', 'video')", name="ck_xj_generated_asset_media_type"),
        Index("ix_xj_generated_asset_task", "workspace_id", "project_id", "task_id", "created_at"),
    )

    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1_024), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GeneratedCandidateSelectionRow(Base):
    """One versioned user selection for the output candidates of one task."""

    __tablename__ = "xingjing_generation_candidate_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "task_id"],
            [
                "xingjing_generation_tasks.workspace_id",
                "xingjing_generation_tasks.project_id",
                "xingjing_generation_tasks.task_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["selected_asset_id"], ["xingjing_generated_assets.asset_id"], ondelete="RESTRICT"),
        CheckConstraint("version >= 1", name="ck_xj_generation_candidate_selection_version"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    selected_asset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VersionConflict(ValueError):
    pass


class TaskScopeNotFound(ValueError):
    """统一缺失响应，避免暴露其他工作区或项目中的任务存在性。"""

    def __init__(self) -> None:
        super().__init__("GENERATION_TASK_NOT_FOUND")


class GenerationBillingError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderCallEvidence:
    evidence_id: str
    task_id: str
    workspace_id: str
    project_id: str
    provider_id: str
    model_id: str
    provider_job_id: str
    attempt: int
    request_summary: dict[str, Any]
    response_summary: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True)
class CostEvidence:
    evidence_id: str
    task_id: str
    workspace_id: str
    project_id: str
    provider_call_evidence_id: str
    currency: str
    estimated_minor: int
    actual_minor: int
    pricing_version: str
    recorded_at: datetime


@dataclass(frozen=True)
class ImmutableAuditRecord:
    audit_id: str
    workspace_id: str
    project_id: str
    task_id: str
    request_id: str
    actor_id: str
    action: str
    result: str
    before: dict[str, Any]
    after: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True)
class LegacyQueueDispatch:
    workspace_id: str
    project_id: str
    task_id: str
    project_name: str
    legacy_task_id: str | None
    dispatch_status: str
    legacy_status: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class GeneratedAsset:
    asset_id: str
    workspace_id: str
    project_id: str
    task_id: str
    media_type: str
    object_key: str
    content_sha256: str
    size_bytes: int
    metadata: dict[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.asset_id, self.workspace_id, self.project_id, self.task_id, self.object_key)):
            raise ValueError("GENERATED_ASSET_IDENTITY_REQUIRED")
        if self.media_type not in {"image", "video"}:
            raise ValueError("INVALID_GENERATED_ASSET_MEDIA_TYPE")
        if self.size_bytes <= 0:
            raise ValueError("INVALID_GENERATED_ASSET_SIZE")
        if (
            len(self.content_sha256) != 64
            or self.content_sha256 != self.content_sha256.lower()
            or any(character not in hexdigits for character in self.content_sha256)
        ):
            raise ValueError("INVALID_GENERATED_ASSET_SHA256")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("GENERATED_ASSET_TIMEZONE_REQUIRED")


@dataclass(frozen=True)
class GeneratedCandidateSelection:
    workspace_id: str
    project_id: str
    task_id: str
    selected_asset_id: str
    version: int
    actor_id: str
    request_id: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class GeneratedCandidateSet:
    """One completed task and its server-confirmed output candidates."""

    task: GenerationTask
    assets: tuple[GeneratedAsset, ...]
    selected_candidate: GeneratedCandidateSelection | None


@dataclass(frozen=True)
class ModelUsageCost:
    currency: str
    estimated_minor: int
    actual_minor: int


@dataclass(frozen=True)
class ModelUsageReport:
    """Project-scoped usage derived from persisted task and billing evidence."""

    provider_id: str | None
    model_id: str | None
    task_count: int
    succeeded_count: int
    processing_count: int
    failed_count: int
    costs: tuple[ModelUsageCost, ...]


@dataclass
class _ModelUsageAccumulator:
    task_count: int = 0
    succeeded_count: int = 0
    processing_count: int = 0
    failed_count: int = 0
    costs: dict[str, list[int]] = field(default_factory=dict)


SessionFactory = async_sessionmaker[AsyncSession]


class SqlAlchemyGenerationTaskRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def grant_credits(
        self,
        *,
        workspace_id: str,
        currency: str,
        amount_minor: int,
        event_id: str,
        reference: str,
        at: datetime,
    ) -> dict[str, object]:
        if amount_minor <= 0 or len(currency) != 3:
            raise GenerationBillingError("GENERATION_BILLING_GRANT_INVALID")
        async with self._session_factory.begin() as session:
            journal_id = _journal_id("grant", f"{workspace_id}:{event_id}")
            replay = await session.get(GenerationBillingJournalRow, journal_id)
            if replay is not None:
                if (
                    replay.action != "grant" or replay.workspace_id != workspace_id
                    or replay.amount_minor != amount_minor or replay.currency != currency.upper()
                    or replay.reference != reference
                ):
                    raise GenerationBillingError("GENERATION_BILLING_IDEMPOTENCY_CONFLICT")
                account = await session.get(GenerationBillingAccountRow, workspace_id)
                if account is None:
                    raise GenerationBillingError("GENERATION_BILLING_ACCOUNT_MISSING")
                return _billing_account_payload(account)
            account = await session.get(GenerationBillingAccountRow, workspace_id, with_for_update=True)
            normalized_currency = currency.upper()
            if account is None:
                account = GenerationBillingAccountRow(
                    workspace_id=workspace_id, currency=normalized_currency, available_minor=0,
                    held_minor=0, spent_minor=0, version=0, updated_at=at,
                )
                session.add(account)
            elif account.currency != normalized_currency:
                raise GenerationBillingError("GENERATION_BILLING_CURRENCY_MISMATCH")
            account.available_minor += amount_minor
            account.version += 1
            account.updated_at = at
            session.add(GenerationBillingJournalRow(
                event_id=journal_id, workspace_id=workspace_id, project_id="system", task_id=None,
                action="grant", currency=normalized_currency, amount_minor=amount_minor,
                postings=_balanced_postings(("workspace.available", amount_minor), ("platform.funding", -amount_minor)),
                reference=reference, occurred_at=at,
            ))
            await session.flush()
            return _billing_account_payload(account)

    async def create_with_billing(
        self,
        task: GenerationTask,
        *,
        estimated_minor: int,
        currency: str,
        pricing_version: str,
        max_active_tasks: int = 20,
    ) -> GenerationTask:
        if estimated_minor <= 0:
            raise GenerationBillingError("GENERATION_ESTIMATED_COST_INVALID")
        try:
            async with self._session_factory.begin() as session:
                existing = await session.scalar(
                    select(TaskRow).where(
                        TaskRow.workspace_id == task.request.workspace_id,
                        TaskRow.project_id == task.request.project_id,
                        TaskRow.idempotency_scope == task.idempotency_scope,
                    )
                )
                if existing is not None:
                    value = GenerationTask.model_validate(existing.snapshot)
                    if value.request_fingerprint != task.request_fingerprint:
                        raise GenerationBillingError("GENERATION_IDEMPOTENCY_CONFLICT")
                    return value
                account = await session.get(
                    GenerationBillingAccountRow, task.request.workspace_id, with_for_update=True
                )
                if account is None:
                    raise GenerationBillingError("GENERATION_BILLING_ACCOUNT_MISSING")
                if account.currency != currency.upper():
                    raise GenerationBillingError("GENERATION_BILLING_CURRENCY_MISMATCH")
                if account.available_minor < estimated_minor:
                    raise GenerationBillingError("GENERATION_INSUFFICIENT_CREDITS")
                active_count = int(await session.scalar(
                    select(func.count()).select_from(TaskRow).where(
                        TaskRow.workspace_id == task.request.workspace_id,
                        TaskRow.status.in_((
                            TaskStatus.QUEUED.value, TaskStatus.RUNNING.value,
                            TaskStatus.RETRYING.value, TaskStatus.CANCELLING.value,
                        )),
                    )
                ) or 0)
                if active_count >= max_active_tasks:
                    raise GenerationBillingError("GENERATION_WORKSPACE_CONCURRENCY_LIMIT")
                session.add(_task_row(task))
                account.available_minor -= estimated_minor
                account.held_minor += estimated_minor
                account.version += 1
                account.updated_at = task.created_at
                session.add(GenerationBillingHoldRow(
                    workspace_id=task.request.workspace_id, project_id=task.request.project_id,
                    task_id=task.task_id, currency=account.currency, estimated_minor=estimated_minor,
                    actual_minor=0, released_minor=0, pricing_version=pricing_version,
                    status="active", terminal_event_id=None, created_at=task.created_at, updated_at=task.created_at,
                ))
                session.add(GenerationBillingJournalRow(
                    event_id=_journal_id("freeze", task.task_id), workspace_id=task.request.workspace_id,
                    project_id=task.request.project_id, task_id=task.task_id, action="freeze",
                    currency=account.currency, amount_minor=estimated_minor,
                    postings=_balanced_postings(("workspace.available", -estimated_minor), ("workspace.held", estimated_minor)),
                    reference=pricing_version, occurred_at=task.created_at,
                ))
                session.add(ProviderSubmissionOutboxRow(
                    workspace_id=task.request.workspace_id, project_id=task.request.project_id,
                    task_id=task.task_id, status="pending", attempts=0, provider_job_id=None,
                    last_error=None, created_at=task.created_at, updated_at=task.created_at,
                ))
                await session.flush()
                return task
        except IntegrityError:
            existing = await self.get_by_idempotency_scope(task.idempotency_scope, project_id=task.request.project_id)
            if existing and existing.request_fingerprint == task.request_fingerprint:
                return existing
            raise

    async def settle_billing(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        actual_minor: int,
        currency: str,
        event_id: str,
        at: datetime,
    ) -> dict[str, object]:
        async with self._session_factory.begin() as session:
            hold = await session.get(
                GenerationBillingHoldRow, (workspace_id, project_id, task_id), with_for_update=True
            )
            account = await session.get(GenerationBillingAccountRow, workspace_id, with_for_update=True)
            if hold is None or account is None:
                raise GenerationBillingError("GENERATION_BILLING_HOLD_MISSING")
            if hold.status != "active":
                if hold.status == "settled" and hold.terminal_event_id == event_id and hold.actual_minor == actual_minor:
                    return _billing_hold_payload(hold)
                raise GenerationBillingError("GENERATION_BILLING_TERMINAL_CONFLICT")
            if currency.upper() != hold.currency or actual_minor < 0 or actual_minor > hold.estimated_minor:
                raise GenerationBillingError("GENERATION_BILLING_SETTLEMENT_INVALID")
            released = hold.estimated_minor - actual_minor
            account.held_minor -= hold.estimated_minor
            account.available_minor += released
            account.spent_minor += actual_minor
            account.version += 1
            account.updated_at = at
            hold.actual_minor = actual_minor
            hold.released_minor = released
            hold.status = "settled"
            hold.terminal_event_id = event_id
            hold.updated_at = at
            session.add(GenerationBillingJournalRow(
                event_id=_journal_id("settle", event_id), workspace_id=workspace_id, project_id=project_id,
                task_id=task_id, action="settle", currency=hold.currency,
                amount_minor=actual_minor,
                postings=_balanced_postings(("workspace.held", -hold.estimated_minor), ("platform.revenue", actual_minor), ("workspace.available", released)),
                reference=event_id, occurred_at=at,
            ))
            return _billing_hold_payload(hold)

    async def release_billing(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        event_id: str,
        at: datetime,
    ) -> dict[str, object]:
        async with self._session_factory.begin() as session:
            hold = await session.get(
                GenerationBillingHoldRow, (workspace_id, project_id, task_id), with_for_update=True
            )
            account = await session.get(GenerationBillingAccountRow, workspace_id, with_for_update=True)
            if hold is None or account is None:
                raise GenerationBillingError("GENERATION_BILLING_HOLD_MISSING")
            if hold.status != "active":
                if hold.status == "released" and hold.terminal_event_id == event_id:
                    return _billing_hold_payload(hold)
                raise GenerationBillingError("GENERATION_BILLING_TERMINAL_CONFLICT")
            account.held_minor -= hold.estimated_minor
            account.available_minor += hold.estimated_minor
            account.version += 1
            account.updated_at = at
            hold.released_minor = hold.estimated_minor
            hold.status = "released"
            hold.terminal_event_id = event_id
            hold.updated_at = at
            session.add(GenerationBillingJournalRow(
                event_id=_journal_id("release", event_id), workspace_id=workspace_id, project_id=project_id,
                task_id=task_id, action="release", currency=hold.currency,
                amount_minor=hold.estimated_minor,
                postings=_balanced_postings(("workspace.held", -hold.estimated_minor), ("workspace.available", hold.estimated_minor)),
                reference=event_id, occurred_at=at,
            ))
            return _billing_hold_payload(hold)

    async def create(self, task: GenerationTask) -> GenerationTask:
        try:
            async with self._session_factory.begin() as s:
                s.add(_task_row(task))
                await s.flush()
                return task
        except IntegrityError:
            existing = await self.get_by_idempotency_scope(task.idempotency_scope, project_id=task.request.project_id)
            if existing and existing.request_fingerprint == task.request_fingerprint:
                return existing
            raise

    async def get_by_idempotency_scope(self, scope: str, *, project_id: str | None = None) -> GenerationTask | None:
        workspace_id = scope.split(":", 1)[0]
        async with self._session_factory() as s:
            q = select(TaskRow).where(TaskRow.workspace_id == workspace_id, TaskRow.idempotency_scope == scope)
            if project_id:
                q = q.where(TaskRow.project_id == project_id)
            row = (await s.execute(q)).scalar_one_or_none()
            return None if row is None else GenerationTask.model_validate(row.snapshot)

    async def get(self, workspace_id: str, project_id: str, task_id: str) -> GenerationTask | None:
        async with self._session_factory() as s:
            row = (
                await s.execute(
                    select(TaskRow).where(
                        TaskRow.workspace_id == workspace_id,
                        TaskRow.project_id == project_id,
                        TaskRow.task_id == task_id,
                    )
                )
            ).scalar_one_or_none()
            return None if row is None else GenerationTask.model_validate(row.snapshot)

    async def list(
        self,
        workspace_id: str,
        project_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        status: str | None = None,
        media_type: str | None = None,
        capability: str | None = None,
    ) -> tuple[tuple[GenerationTask, ...], int]:
        """List only the trusted workspace/project scope in a stable order."""
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("INVALID_GENERATION_TASK_PAGE")
        async with self._session_factory() as s:
            statement = (
                select(TaskRow)
                .where(TaskRow.workspace_id == workspace_id, TaskRow.project_id == project_id)
                .order_by(TaskRow.created_at.desc(), TaskRow.task_id.desc())
            )
            if status is not None:
                statement = statement.where(TaskRow.status == status)
            rows = (await s.execute(statement)).scalars().all()
        filtered = tuple(
            task
            for task in (GenerationTask.model_validate(row.snapshot) for row in rows)
            if (media_type is None or task.request.media_type.value == media_type)
            and (capability is None or task.request.capability == capability)
        )
        return filtered[offset : offset + limit], len(filtered)

    async def list_generated_candidate_sets(
        self,
        workspace_id: str,
        project_id: str,
        *,
        media_type: str | None,
        capability: str | None,
        offset: int,
        limit: int,
    ) -> tuple[tuple[GeneratedCandidateSet, ...], int]:
        """Return only persisted successful candidates, with server-side scope checks.

        Capability lives inside the immutable request snapshot, so it is filtered
        after deserialisation.  The result remains project-scoped and paginated;
        no client supplied candidate or asset is ever accepted here.
        """
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("INVALID_GENERATION_CANDIDATE_PAGE")
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TaskRow)
                    .where(
                        TaskRow.workspace_id == workspace_id,
                        TaskRow.project_id == project_id,
                        TaskRow.status == TaskStatus.SUCCEEDED.value,
                    )
                    .order_by(TaskRow.updated_at.desc(), TaskRow.task_id.desc())
                )
            ).scalars().all()
            tasks = tuple(GenerationTask.model_validate(row.snapshot) for row in rows)
            filtered = tuple(
                task
                for task in tasks
                if (media_type is None or task.request.media_type.value == media_type)
                and (capability is None or task.request.capability == capability)
            )
            page = filtered[offset : offset + limit]
            task_ids = tuple(task.task_id for task in page)
            if not task_ids:
                return (), len(filtered)
            asset_rows = (
                await session.execute(
                    select(GeneratedAssetRow)
                    .where(
                        GeneratedAssetRow.workspace_id == workspace_id,
                        GeneratedAssetRow.project_id == project_id,
                        GeneratedAssetRow.task_id.in_(task_ids),
                    )
                    .order_by(GeneratedAssetRow.created_at, GeneratedAssetRow.asset_id)
                )
            ).scalars().all()
            selection_rows = (
                await session.execute(
                    select(GeneratedCandidateSelectionRow).where(
                        GeneratedCandidateSelectionRow.workspace_id == workspace_id,
                        GeneratedCandidateSelectionRow.project_id == project_id,
                        GeneratedCandidateSelectionRow.task_id.in_(task_ids),
                    )
                )
            ).scalars().all()
        assets_by_task: dict[str, list[GeneratedAsset]] = {task_id: [] for task_id in task_ids}
        for row in asset_rows:
            assets_by_task[row.task_id].append(_generated_asset(row))
        selections = {row.task_id: _candidate_selection(row) for row in selection_rows}
        return (
            tuple(
                GeneratedCandidateSet(
                    task=task,
                    assets=tuple(assets_by_task[task.task_id]),
                    selected_candidate=selections.get(task.task_id),
                )
                for task in page
            ),
            len(filtered),
        )

    async def model_usage_report(self, workspace_id: str, project_id: str) -> tuple[ModelUsageReport, ...]:
        """Aggregate only recorded task states and provider billing evidence."""
        async with self._session_factory() as session:
            task_rows = (
                await session.execute(
                    select(TaskRow)
                    .where(TaskRow.workspace_id == workspace_id, TaskRow.project_id == project_id)
                    .order_by(TaskRow.created_at.desc(), TaskRow.task_id.desc())
                )
            ).scalars().all()
            cost_rows = (
                await session.execute(
                    select(CostEvidenceRow).where(
                        CostEvidenceRow.workspace_id == workspace_id,
                        CostEvidenceRow.project_id == project_id,
                    )
                )
            ).scalars().all()
        tasks = tuple(GenerationTask.model_validate(row.snapshot) for row in task_rows)
        task_keys = {
            task.task_id: (task.resolved_provider_id, task.resolved_model_id)
            for task in tasks
        }
        aggregates: dict[tuple[str | None, str | None], _ModelUsageAccumulator] = {}
        for task in tasks:
            key = (task.resolved_provider_id, task.resolved_model_id)
            item = aggregates.setdefault(key, _ModelUsageAccumulator())
            item.task_count += 1
            if task.status is TaskStatus.SUCCEEDED:
                item.succeeded_count += 1
            elif task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.CANCELLING}:
                item.processing_count += 1
            else:
                item.failed_count += 1
        for row in cost_rows:
            key = task_keys.get(row.task_id)
            if key is None:
                continue
            item = aggregates.setdefault(key, _ModelUsageAccumulator())
            payload = row.payload
            currency = payload.get("currency")
            estimated = payload.get("estimated_minor")
            actual = payload.get("actual_minor")
            if not isinstance(currency, str) or not isinstance(estimated, int) or isinstance(estimated, bool) or not isinstance(actual, int) or isinstance(actual, bool):
                raise ValueError("GENERATION_COST_EVIDENCE_INVALID")
            current = item.costs.setdefault(currency, [0, 0])
            current[0] += estimated
            current[1] += actual
        return tuple(
            ModelUsageReport(
                provider_id=key[0], model_id=key[1], task_count=value.task_count,
                succeeded_count=value.succeeded_count, processing_count=value.processing_count,
                failed_count=value.failed_count,
                costs=tuple(
                    ModelUsageCost(currency=currency, estimated_minor=amounts[0], actual_minor=amounts[1])
                    for currency, amounts in sorted(value.costs.items())
                ),
            )
            for key, value in sorted(aggregates.items(), key=lambda item: ((item[0][0] or ""), (item[0][1] or "")))
        )

    async def save(self, task: GenerationTask, *, expected_version: int) -> GenerationTask:
        async with self._session_factory.begin() as s:
            current = (
                await s.execute(
                    select(TaskRow.version).where(
                        TaskRow.workspace_id == task.request.workspace_id,
                        TaskRow.project_id == task.request.project_id,
                        TaskRow.task_id == task.task_id,
                    )
                )
            ).scalar_one_or_none()
            if current != expected_version:
                raise VersionConflict("任务版本冲突或已终态")
            if task.version != expected_version + 1:
                raise ValueError("任务版本必须连续递增")
            r = await s.execute(
                update(TaskRow)
                .where(
                    TaskRow.workspace_id == task.request.workspace_id,
                    TaskRow.project_id == task.request.project_id,
                    TaskRow.task_id == task.task_id,
                    TaskRow.version == expected_version,
                    TaskRow.status.not_in(
                        [
                            x.value
                            for x in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED)
                        ]
                    ),
                )
                .values(
                    version=task.version,
                    status=task.status.value,
                    snapshot=task.model_dump(mode="json"),
                    updated_at=task.updated_at,
                )
            )
            if not r.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
                raise VersionConflict("任务版本冲突或已终态")
            return task

    async def append_provider_evidence(self, item: ProviderCallEvidence) -> None:
        async with self._session_factory.begin() as s:
            await self._require_task_scope(s, item.workspace_id, item.project_id, item.task_id)
            existing = await s.get(ProviderEvidenceRow, item.evidence_id)
            if existing is None:
                s.add(
                    ProviderEvidenceRow(
                        evidence_id=item.evidence_id,
                        workspace_id=item.workspace_id,
                        project_id=item.project_id,
                        task_id=item.task_id,
                        payload={k: v for k, v in item.__dict__.items() if k != "occurred_at"},
                        occurred_at=item.occurred_at,
                    )
                )

    async def append_cost_evidence(self, item: CostEvidence) -> None:
        async with self._session_factory.begin() as s:
            await self._require_task_scope(s, item.workspace_id, item.project_id, item.task_id)
            existing = await s.get(CostEvidenceRow, item.evidence_id)
            if existing is None:
                s.add(
                    CostEvidenceRow(
                        evidence_id=item.evidence_id,
                        workspace_id=item.workspace_id,
                        project_id=item.project_id,
                        task_id=item.task_id,
                        payload={k: v for k, v in item.__dict__.items() if k != "recorded_at"},
                        occurred_at=item.recorded_at,
                    )
                )

    async def append_audit(self, item: ImmutableAuditRecord) -> None:
        async with self._session_factory.begin() as s:
            await self._require_task_scope(s, item.workspace_id, item.project_id, item.task_id)
            if await s.get(AuditRow, item.audit_id) is None:
                s.add(
                    AuditRow(
                        audit_id=item.audit_id,
                        workspace_id=item.workspace_id,
                        project_id=item.project_id,
                        task_id=item.task_id,
                        request_id=item.request_id,
                        payload={k: v for k, v in item.__dict__.items() if k != "occurred_at"},
                        occurred_at=item.occurred_at,
                    )
                )

    @staticmethod
    async def _require_task_scope(
        session: AsyncSession, workspace_id: str, project_id: str, task_id: str
    ) -> TaskRow:
        row = (
            await session.execute(
                select(TaskRow).where(
                    TaskRow.workspace_id == workspace_id,
                    TaskRow.project_id == project_id,
                    TaskRow.task_id == task_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise TaskScopeNotFound()
        return row

    async def list_provider_evidence(
        self, workspace_id: str, project_id: str, task_id: str
    ) -> tuple[ProviderCallEvidence, ...]:
        async with self._session_factory() as s:
            rows = (
                await s.execute(
                    select(ProviderEvidenceRow).where(
                        ProviderEvidenceRow.workspace_id == workspace_id,
                        ProviderEvidenceRow.project_id == project_id,
                        ProviderEvidenceRow.task_id == task_id,
                    )
                )
            ).scalars()
            return tuple(ProviderCallEvidence(**r.payload, occurred_at=_utc(r.occurred_at)) for r in rows)

    async def list_cost_evidence(self, workspace_id: str, project_id: str, task_id: str) -> tuple[CostEvidence, ...]:
        async with self._session_factory() as s:
            rows = (
                await s.execute(
                    select(CostEvidenceRow).where(
                        CostEvidenceRow.workspace_id == workspace_id,
                        CostEvidenceRow.project_id == project_id,
                        CostEvidenceRow.task_id == task_id,
                    )
                )
            ).scalars()
            return tuple(CostEvidence(**r.payload, recorded_at=_utc(r.occurred_at)) for r in rows)

    async def query_audit(
        self, workspace_id: str, project_id: str, *, request_id: str | None = None
    ) -> tuple[ImmutableAuditRecord, ...]:
        async with self._session_factory() as s:
            q = select(AuditRow).where(AuditRow.workspace_id == workspace_id, AuditRow.project_id == project_id)
            if request_id:
                q = q.where(AuditRow.request_id == request_id)
            rows = (await s.execute(q)).scalars()
            return tuple(ImmutableAuditRecord(**r.payload, occurred_at=_utc(r.occurred_at)) for r in rows)

    async def create_legacy_dispatch(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        project_name: str,
        created_at: datetime,
    ) -> LegacyQueueDispatch:
        """Create the persistent outbox-side bridge before queue submission.

        A crash after this transaction leaves a recoverable ``pending`` record
        for the dispatcher instead of an invisible task that never reaches the
        worker.
        """
        async with self._session_factory.begin() as s:
            await self._require_task_scope(s, workspace_id, project_id, task_id)
            row = await s.get(LegacyQueueDispatchRow, (workspace_id, project_id, task_id))
            if row is None:
                row = LegacyQueueDispatchRow(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    task_id=task_id,
                    project_name=project_name,
                    legacy_task_id=None,
                    dispatch_status="pending",
                    legacy_status=None,
                    last_error=None,
                    created_at=created_at,
                    updated_at=created_at,
                )
                s.add(row)
            elif row.project_name != project_name:
                raise VersionConflict("legacy dispatch project mapping conflict")
            await s.flush()
            return _dispatch(row)

    async def create_generated_asset(self, asset: GeneratedAsset) -> GeneratedAsset:
        """Persist a verified object reference without accepting cross-media output."""
        async with self._session_factory.begin() as s:
            task_row = await s.get(TaskRow, (asset.workspace_id, asset.project_id, asset.task_id))
            if task_row is None:
                raise TaskScopeNotFound()
            task = GenerationTask.model_validate(task_row.snapshot)
            if asset.media_type != task.request.media_type.value:
                raise ValueError("GENERATED_ASSET_MEDIA_TYPE_MISMATCH")
            existing = await s.get(GeneratedAssetRow, asset.asset_id)
            if existing is not None:
                stored = _generated_asset(existing)
                if stored != asset:
                    raise VersionConflict("generated asset ID already belongs to another artifact")
                return stored
            s.add(
                GeneratedAssetRow(
                    asset_id=asset.asset_id,
                    workspace_id=asset.workspace_id,
                    project_id=asset.project_id,
                    task_id=asset.task_id,
                    media_type=asset.media_type,
                    object_key=asset.object_key,
                    content_sha256=asset.content_sha256.lower(),
                    size_bytes=asset.size_bytes,
                    artifact_metadata=asset.metadata,
                    created_at=asset.created_at.astimezone(UTC),
                )
            )
            return asset

    async def complete_with_generated_assets(
        self,
        task: GenerationTask,
        *,
        expected_version: int,
        assets: tuple[GeneratedAsset, ...],
        cost: CostEvidence | None = None,
        billing_event_id: str | None = None,
    ) -> GenerationTask:
        """Atomically publish verified outputs and the matching succeeded task."""
        if task.status is not TaskStatus.SUCCEEDED or not task.output_asset_ids:
            raise ValueError("GENERATION_TASK_SUCCESS_OUTPUTS_REQUIRED")
        if tuple(asset.asset_id for asset in assets) != task.output_asset_ids:
            raise ValueError("GENERATION_CALLBACK_OUTPUT_ASSET_MISMATCH")
        if len({asset.asset_id for asset in assets}) != len(assets):
            raise ValueError("GENERATION_OUTPUT_ASSET_DUPLICATE")
        if cost is None or billing_event_id is None:
            raise GenerationBillingError("GENERATION_SUCCESS_BILLING_REQUIRED")

        async with self._session_factory.begin() as s:
            current = await s.get(TaskRow, (task.request.workspace_id, task.request.project_id, task.task_id))
            if current is None or current.version != expected_version or current.status in {
                item.value for item in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED)
            }:
                raise VersionConflict("任务版本冲突或已终态")
            for asset in assets:
                self._validate_generated_asset_for_task(asset, task)
                existing = await s.get(GeneratedAssetRow, asset.asset_id)
                if existing is None:
                    s.add(
                        GeneratedAssetRow(
                            asset_id=asset.asset_id,
                            workspace_id=asset.workspace_id,
                            project_id=asset.project_id,
                            task_id=asset.task_id,
                            media_type=asset.media_type,
                            object_key=asset.object_key,
                            content_sha256=asset.content_sha256,
                            size_bytes=asset.size_bytes,
                            artifact_metadata=asset.metadata,
                            created_at=asset.created_at.astimezone(UTC),
                        )
                    )
                elif _generated_asset(existing) != asset:
                    raise VersionConflict("generated asset ID already belongs to another artifact")

            updated = await s.execute(
                update(TaskRow)
                .where(
                    TaskRow.workspace_id == task.request.workspace_id,
                    TaskRow.project_id == task.request.project_id,
                    TaskRow.task_id == task.task_id,
                    TaskRow.version == expected_version,
                    TaskRow.status.not_in(
                        [item.value for item in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED)]
                    ),
                )
                .values(
                    version=task.version,
                    status=task.status.value,
                    snapshot=task.model_dump(mode="json"),
                    updated_at=task.updated_at,
                )
            )
            if not updated.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
                raise VersionConflict("任务版本冲突或已终态")
            hold = await s.get(
                GenerationBillingHoldRow,
                (task.request.workspace_id, task.request.project_id, task.task_id),
                with_for_update=True,
            )
            account = await s.get(
                GenerationBillingAccountRow, task.request.workspace_id, with_for_update=True
            )
            if hold is None or account is None or hold.status != "active":
                raise GenerationBillingError("GENERATION_BILLING_HOLD_MISSING")
            if (
                cost.workspace_id != task.request.workspace_id
                or cost.project_id != task.request.project_id
                or cost.task_id != task.task_id
                or cost.currency != hold.currency
                or cost.estimated_minor != hold.estimated_minor
                or cost.pricing_version != hold.pricing_version
                or cost.actual_minor > hold.estimated_minor
            ):
                raise GenerationBillingError("GENERATION_BILLING_SETTLEMENT_INVALID")
            released = hold.estimated_minor - cost.actual_minor
            account.held_minor -= hold.estimated_minor
            account.available_minor += released
            account.spent_minor += cost.actual_minor
            account.version += 1
            account.updated_at = task.updated_at
            hold.actual_minor = cost.actual_minor
            hold.released_minor = released
            hold.status = "settled"
            hold.terminal_event_id = billing_event_id
            hold.updated_at = task.updated_at
            s.add(CostEvidenceRow(
                evidence_id=cost.evidence_id, workspace_id=cost.workspace_id,
                project_id=cost.project_id, task_id=cost.task_id,
                payload={k: v for k, v in cost.__dict__.items() if k != "recorded_at"},
                occurred_at=cost.recorded_at,
            ))
            s.add(GenerationBillingJournalRow(
                event_id=_journal_id("settle", billing_event_id), workspace_id=task.request.workspace_id,
                project_id=task.request.project_id, task_id=task.task_id, action="settle",
                currency=hold.currency, amount_minor=cost.actual_minor,
                postings=_balanced_postings(("workspace.held", -hold.estimated_minor), ("platform.revenue", cost.actual_minor), ("workspace.available", released)),
                reference=billing_event_id, occurred_at=task.updated_at,
            ))
            return task

    async def save_with_billing_release(
        self,
        task: GenerationTask,
        *,
        expected_version: int,
        event_id: str,
        cost: CostEvidence | None = None,
    ) -> GenerationTask:
        if task.status not in {TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED}:
            raise GenerationBillingError("GENERATION_BILLING_RELEASE_STATUS_INVALID")
        async with self._session_factory.begin() as session:
            updated = await session.execute(
                update(TaskRow)
                .where(
                    TaskRow.workspace_id == task.request.workspace_id,
                    TaskRow.project_id == task.request.project_id,
                    TaskRow.task_id == task.task_id,
                    TaskRow.version == expected_version,
                    TaskRow.status.not_in(
                        [item.value for item in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED)]
                    ),
                )
                .values(
                    version=task.version, status=task.status.value,
                    snapshot=task.model_dump(mode="json"), updated_at=task.updated_at,
                )
            )
            if not updated.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
                raise VersionConflict("任务版本冲突或已终态")
            hold = await session.get(
                GenerationBillingHoldRow,
                (task.request.workspace_id, task.request.project_id, task.task_id),
                with_for_update=True,
            )
            account = await session.get(
                GenerationBillingAccountRow, task.request.workspace_id, with_for_update=True
            )
            if hold is None or account is None or hold.status != "active":
                raise GenerationBillingError("GENERATION_BILLING_HOLD_MISSING")
            if cost is not None and (
                cost.workspace_id != task.request.workspace_id
                or cost.project_id != task.request.project_id
                or cost.task_id != task.task_id
                or cost.currency != hold.currency
                or cost.estimated_minor != hold.estimated_minor
                or cost.pricing_version != hold.pricing_version
                or cost.actual_minor > hold.estimated_minor
            ):
                raise GenerationBillingError("GENERATION_BILLING_SETTLEMENT_INVALID")
            actual_minor = cost.actual_minor if cost is not None else 0
            released_minor = hold.estimated_minor - actual_minor
            account.held_minor -= hold.estimated_minor
            account.available_minor += released_minor
            account.spent_minor += actual_minor
            account.version += 1
            account.updated_at = task.updated_at
            hold.actual_minor = actual_minor
            hold.released_minor = released_minor
            hold.status = "settled" if cost is not None else "released"
            hold.terminal_event_id = event_id
            hold.updated_at = task.updated_at
            if cost is not None:
                session.add(CostEvidenceRow(
                    evidence_id=cost.evidence_id, workspace_id=cost.workspace_id,
                    project_id=cost.project_id, task_id=cost.task_id,
                    payload={k: v for k, v in cost.__dict__.items() if k != "recorded_at"},
                    occurred_at=cost.recorded_at,
                ))
            session.add(GenerationBillingJournalRow(
                event_id=_journal_id("settle" if cost is not None else "release", event_id),
                workspace_id=task.request.workspace_id,
                project_id=task.request.project_id, task_id=task.task_id,
                action="settle" if cost is not None else "release",
                currency=hold.currency, amount_minor=actual_minor if cost is not None else hold.estimated_minor,
                postings=_balanced_postings(
                    ("workspace.held", -hold.estimated_minor),
                    ("platform.revenue", actual_minor),
                    ("workspace.available", released_minor),
                ),
                reference=event_id, occurred_at=task.updated_at,
            ))
            return task

    async def billing_for_task(self, workspace_id: str, project_id: str, task_id: str) -> dict[str, object] | None:
        async with self._session_factory() as session:
            hold = await session.get(GenerationBillingHoldRow, (workspace_id, project_id, task_id))
            return None if hold is None else _billing_hold_payload(hold)

    async def record_progress(
        self, *, workspace_id: str, project_id: str, task_id: str,
        event_id: str, percent: int, message: str, eta_seconds: int | None, at: datetime,
    ) -> dict[str, object]:
        if not 0 <= percent <= 100 or not message.strip() or (eta_seconds is not None and eta_seconds < 0):
            raise ValueError("GENERATION_PROGRESS_INVALID")
        async with self._session_factory.begin() as session:
            await self._require_task_scope(session, workspace_id, project_id, task_id)
            existing = await session.get(GenerationProgressRow, event_id)
            if existing is None:
                existing = GenerationProgressRow(
                    event_id=event_id, workspace_id=workspace_id, project_id=project_id,
                    task_id=task_id, percent=percent, message=message.strip(),
                    eta_seconds=eta_seconds, occurred_at=at,
                )
                session.add(existing)
            elif (
                existing.workspace_id != workspace_id or existing.project_id != project_id
                or existing.task_id != task_id or existing.percent != percent
            ):
                raise ValueError("GENERATION_PROGRESS_EVENT_CONFLICT")
            return _progress_payload(existing)

    async def task_progress(self, workspace_id: str, project_id: str, task_id: str) -> dict[str, object]:
        async with self._session_factory() as session:
            task = await self._require_task_scope(session, workspace_id, project_id, task_id)
            latest = await session.scalar(
                select(GenerationProgressRow)
                .where(
                    GenerationProgressRow.workspace_id == workspace_id,
                    GenerationProgressRow.project_id == project_id,
                    GenerationProgressRow.task_id == task_id,
                )
                .order_by(GenerationProgressRow.occurred_at.desc(), GenerationProgressRow.event_id.desc())
                .limit(1)
            )
            queue_position: int | None = None
            if task.status == TaskStatus.QUEUED.value:
                queue_position = 1 + int(await session.scalar(
                    select(func.count()).select_from(TaskRow).where(
                        TaskRow.workspace_id == workspace_id,
                        TaskRow.status == TaskStatus.QUEUED.value,
                        TaskRow.created_at < task.created_at,
                    )
                ) or 0)
            return {
                "queue_position": queue_position,
                "latest": None if latest is None else _progress_payload(latest),
            }

    async def save_provider_submission_accepted(
        self, task: GenerationTask, *, expected_version: int, provider_job_id: str, at: datetime
    ) -> GenerationTask:
        async with self._session_factory.begin() as session:
            row = await session.get(
                ProviderSubmissionOutboxRow,
                (task.request.workspace_id, task.request.project_id, task.task_id),
                with_for_update=True,
            )
            if row is None:
                raise TaskScopeNotFound()
            updated = await session.execute(
                update(TaskRow)
                .where(
                    TaskRow.workspace_id == task.request.workspace_id,
                    TaskRow.project_id == task.request.project_id,
                    TaskRow.task_id == task.task_id,
                    TaskRow.version == expected_version,
                    TaskRow.status == TaskStatus.QUEUED.value,
                )
                .values(
                    version=task.version, status=task.status.value,
                    snapshot=task.model_dump(mode="json"), updated_at=task.updated_at,
                )
            )
            if not updated.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
                raise VersionConflict("任务版本冲突或已离开排队态")
            row.status = "accepted"
            row.attempts += 1
            row.provider_job_id = provider_job_id
            row.last_error = None
            row.updated_at = at
            return task

    async def mark_provider_submission_failed(
        self, workspace_id: str, project_id: str, task_id: str, *, error: str, at: datetime
    ) -> None:
        async with self._session_factory.begin() as session:
            row = await session.get(ProviderSubmissionOutboxRow, (workspace_id, project_id, task_id), with_for_update=True)
            if row is None:
                raise TaskScopeNotFound()
            row.status = "failed"
            row.attempts += 1
            row.last_error = error[:2_000]
            row.updated_at = at

    async def list_pending_provider_submissions(self, *, limit: int = 100) -> tuple[GenerationTask, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("invalid provider submission recovery limit")
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(TaskRow)
                .join(ProviderSubmissionOutboxRow, (
                    (ProviderSubmissionOutboxRow.workspace_id == TaskRow.workspace_id)
                    & (ProviderSubmissionOutboxRow.project_id == TaskRow.project_id)
                    & (ProviderSubmissionOutboxRow.task_id == TaskRow.task_id)
                ))
                .where(ProviderSubmissionOutboxRow.status.in_(("pending", "failed")), TaskRow.status == TaskStatus.QUEUED.value)
                .order_by(ProviderSubmissionOutboxRow.updated_at, TaskRow.task_id)
                .limit(limit)
            )).scalars()
            return tuple(GenerationTask.model_validate(row.snapshot) for row in rows)

    async def list_overdue_tasks(self, *, at: datetime, limit: int = 100) -> tuple[GenerationTask, ...]:
        if at.tzinfo is None or at.utcoffset() is None or not 1 <= limit <= 1_000:
            raise ValueError("invalid overdue task query")
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TaskRow)
                    .where(
                        TaskRow.status.in_(
                            (
                                TaskStatus.QUEUED.value,
                                TaskStatus.RUNNING.value,
                                TaskStatus.RETRYING.value,
                                TaskStatus.CANCELLING.value,
                            )
                        )
                    )
                    .order_by(TaskRow.created_at, TaskRow.task_id)
                    .limit(1_000)
                )
            ).scalars()
            tasks = (GenerationTask.model_validate(row.snapshot) for row in rows)
            return tuple(task for task in tasks if task.timeout_at <= at)[:limit]

    async def list_generated_assets(
        self, workspace_id: str, project_id: str, task_id: str
    ) -> tuple[GeneratedAsset, ...]:
        async with self._session_factory() as s:
            await self._require_task_scope(s, workspace_id, project_id, task_id)
            rows = (
                await s.execute(
                    select(GeneratedAssetRow)
                    .where(
                        GeneratedAssetRow.workspace_id == workspace_id,
                        GeneratedAssetRow.project_id == project_id,
                        GeneratedAssetRow.task_id == task_id,
                    )
                    .order_by(GeneratedAssetRow.created_at, GeneratedAssetRow.asset_id)
                )
            ).scalars()
            return tuple(_generated_asset(row) for row in rows)

    async def get_generated_asset(
        self, workspace_id: str, project_id: str, asset_id: str
    ) -> GeneratedAsset | None:
        async with self._session_factory() as s:
            row = (
                await s.execute(
                    select(GeneratedAssetRow).where(
                        GeneratedAssetRow.workspace_id == workspace_id,
                        GeneratedAssetRow.project_id == project_id,
                        GeneratedAssetRow.asset_id == asset_id,
                    )
                )
            ).scalar_one_or_none()
            return None if row is None else _generated_asset(row)

    async def get_generated_candidate_selection(
        self, workspace_id: str, project_id: str, task_id: str
    ) -> GeneratedCandidateSelection | None:
        async with self._session_factory() as s:
            row = await s.get(GeneratedCandidateSelectionRow, (workspace_id, project_id, task_id))
            return None if row is None else _candidate_selection(row)

    async def select_generated_candidate(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        asset_id: str,
        expected_version: int,
        actor_id: str,
        idempotency_key: str,
        at: datetime,
    ) -> GeneratedCandidateSelection:
        if expected_version < 0:
            raise ValueError("GENERATION_CANDIDATE_SELECTION_VERSION_INVALID")
        async with self._session_factory.begin() as s:
            task = await s.get(TaskRow, (workspace_id, project_id, task_id))
            if task is None:
                raise TaskScopeNotFound()
            if task.status != TaskStatus.SUCCEEDED.value:
                raise ValueError("GENERATION_CANDIDATE_SELECTION_TASK_NOT_COMPLETED")
            asset = await s.get(GeneratedAssetRow, asset_id)
            if asset is None or (
                asset.workspace_id != workspace_id or asset.project_id != project_id or asset.task_id != task_id
            ):
                raise TaskScopeNotFound()
            row = await s.get(GeneratedCandidateSelectionRow, (workspace_id, project_id, task_id))
            if row is None:
                if expected_version != 0:
                    raise VersionConflict("GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT")
                row = GeneratedCandidateSelectionRow(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    task_id=task_id,
                    selected_asset_id=asset_id,
                    version=1,
                    actor_id=actor_id,
                    request_id=idempotency_key,
                    created_at=at,
                    updated_at=at,
                )
                s.add(row)
            else:
                if row.version != expected_version:
                    if row.request_id == idempotency_key and row.selected_asset_id == asset_id:
                        return _candidate_selection(row)
                    raise VersionConflict("GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT")
                row.selected_asset_id = asset_id
                row.version += 1
                row.actor_id = actor_id
                row.request_id = idempotency_key
                row.updated_at = at
            await s.flush()
            return _candidate_selection(row)

    @staticmethod
    def _validate_generated_asset_for_task(asset: GeneratedAsset, task: GenerationTask) -> None:
        if (
            asset.workspace_id != task.request.workspace_id
            or asset.project_id != task.request.project_id
            or asset.task_id != task.task_id
        ):
            raise TaskScopeNotFound()
        if asset.media_type != task.request.media_type.value:
            raise ValueError("GENERATED_ASSET_MEDIA_TYPE_MISMATCH")

    async def mark_legacy_dispatch_enqueued(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        legacy_task_id: str,
        at: datetime,
    ) -> LegacyQueueDispatch:
        async with self._session_factory.begin() as s:
            row = await s.get(LegacyQueueDispatchRow, (workspace_id, project_id, task_id))
            if row is None:
                raise TaskScopeNotFound()
            if row.legacy_task_id is not None and row.legacy_task_id != legacy_task_id:
                raise VersionConflict("legacy dispatch already linked to another queue task")
            row.legacy_task_id = legacy_task_id
            row.dispatch_status = "enqueued"
            row.last_error = None
            row.updated_at = at
            await s.flush()
            return _dispatch(row)

    async def record_legacy_dispatch_failure(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        error: str,
        at: datetime,
    ) -> LegacyQueueDispatch:
        async with self._session_factory.begin() as s:
            row = await s.get(LegacyQueueDispatchRow, (workspace_id, project_id, task_id))
            if row is None:
                raise TaskScopeNotFound()
            row.dispatch_status = "pending"
            row.last_error = error[:2_000]
            row.updated_at = at
            await s.flush()
            return _dispatch(row)

    async def update_legacy_observation(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        legacy_status: str,
        at: datetime,
    ) -> LegacyQueueDispatch:
        async with self._session_factory.begin() as s:
            row = await s.get(LegacyQueueDispatchRow, (workspace_id, project_id, task_id))
            if row is None or row.legacy_task_id is None:
                raise TaskScopeNotFound()
            row.legacy_status = legacy_status
            row.updated_at = at
            await s.flush()
            return _dispatch(row)

    async def list_legacy_dispatches_for_reconciliation(self, *, limit: int = 100) -> tuple[LegacyQueueDispatch, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("invalid reconciliation limit")
        async with self._session_factory() as s:
            rows = (
                await s.execute(
                    select(LegacyQueueDispatchRow)
                    .where(LegacyQueueDispatchRow.dispatch_status.in_(("pending", "enqueued")))
                    .order_by(LegacyQueueDispatchRow.updated_at, LegacyQueueDispatchRow.task_id)
                    .limit(limit)
                )
            ).scalars()
            return tuple(_dispatch(row) for row in rows)


def _task_row(task: GenerationTask) -> TaskRow:
    return TaskRow(
        workspace_id=task.request.workspace_id,
        project_id=task.request.project_id,
        task_id=task.task_id,
        idempotency_scope=task.idempotency_scope,
        version=task.version,
        status=task.status.value,
        snapshot=task.model_dump(mode="json"),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _billing_account_payload(row: GenerationBillingAccountRow) -> dict[str, object]:
    return {
        "workspace_id": row.workspace_id,
        "currency": row.currency,
        "available_minor": row.available_minor,
        "held_minor": row.held_minor,
        "spent_minor": row.spent_minor,
        "version": row.version,
        "updated_at": _utc(row.updated_at).isoformat(),
    }


def _billing_hold_payload(row: GenerationBillingHoldRow) -> dict[str, object]:
    return {
        "workspace_id": row.workspace_id,
        "project_id": row.project_id,
        "task_id": row.task_id,
        "currency": row.currency,
        "estimated_minor": row.estimated_minor,
        "actual_minor": row.actual_minor,
        "released_minor": row.released_minor,
        "pricing_version": row.pricing_version,
        "status": row.status,
        "terminal_event_id": row.terminal_event_id,
        "updated_at": _utc(row.updated_at).isoformat(),
    }


def _progress_payload(row: GenerationProgressRow) -> dict[str, object]:
    return {
        "event_id": row.event_id,
        "percent": row.percent,
        "message": row.message,
        "eta_seconds": row.eta_seconds,
        "occurred_at": _utc(row.occurred_at).isoformat(),
    }


def _journal_id(action: str, external_id: str) -> str:
    digest = hashlib.sha256(f"{action}:{external_id}".encode()).hexdigest()
    return f"{action}:{digest}"


def _balanced_postings(*entries: tuple[str, int]) -> list[dict[str, object]]:
    if sum(amount for _, amount in entries) != 0:
        raise GenerationBillingError("GENERATION_BILLING_JOURNAL_UNBALANCED")
    return [{"account": account, "amount_minor": amount} for account, amount in entries]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _dispatch(row: LegacyQueueDispatchRow) -> LegacyQueueDispatch:
    return LegacyQueueDispatch(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        task_id=row.task_id,
        project_name=row.project_name,
        legacy_task_id=row.legacy_task_id,
        dispatch_status=row.dispatch_status,
        legacy_status=row.legacy_status,
        last_error=row.last_error,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def _generated_asset(row: GeneratedAssetRow) -> GeneratedAsset:
    return GeneratedAsset(
        asset_id=row.asset_id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        task_id=row.task_id,
        media_type=row.media_type,
        object_key=row.object_key,
        content_sha256=row.content_sha256,
        size_bytes=row.size_bytes,
        metadata=dict(row.artifact_metadata),
        created_at=_utc(row.created_at),
    )


def _candidate_selection(row: GeneratedCandidateSelectionRow) -> GeneratedCandidateSelection:
    return GeneratedCandidateSelection(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        task_id=row.task_id,
        selected_asset_id=row.selected_asset_id,
        version=row.version,
        actor_id=row.actor_id,
        request_id=row.request_id,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )
