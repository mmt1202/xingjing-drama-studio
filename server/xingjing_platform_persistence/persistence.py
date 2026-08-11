"""星镜 S03/S04 的 SQLAlchemy 持久化端口与生产实现。"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """独立 metadata；由主线迁移显式接入，禁止运行时隐式建表。"""


class ScopedMixin:
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)


class ProjectRow(ScopedMixin, Base):
    __tablename__ = "xingjing_projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "idempotency_key", name="uq_xj_project_scope_idempotency"),
        Index("ix_xj_project_scope_cursor", "tenant_id", "workspace_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    project_type: Mapped[str] = mapped_column(String(64), nullable=False, default="drama")
    target_platform: Mapped[str] = mapped_column(String(64), nullable=False, default="web")
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    production_status: Mapped[str] = mapped_column(String(64), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InboxRow(ScopedMixin, Base):
    __tablename__ = "xingjing_inbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "consumer", "message_id", name="uq_xj_inbox_message"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    consumer: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class OutboxRow(ScopedMixin, Base):
    __tablename__ = "xingjing_outbox"
    __table_args__ = (Index("ix_xj_outbox_pending", "published_at", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditRow(ScopedMixin, Base):
    __tablename__ = "xingjing_audit_log"
    __table_args__ = (Index("ix_xj_audit_scope_time", "tenant_id", "workspace_id", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class TaskRow(ScopedMixin, Base):
    __tablename__ = "xingjing_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "idempotency_key", name="uq_xj_task_scope_idempotency"),
        Index("ix_xj_task_claim", "tenant_id", "workspace_id", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    provider_job_id: Mapped[str | None] = mapped_column(String(255))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class TaskEventRow(ScopedMixin, Base):
    __tablename__ = "xingjing_task_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "event_key", name="uq_xj_task_event_key"),
        Index("ix_xj_task_event_stream", "tenant_id", "workspace_id", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, default=_id)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class TaskDeadLetterRow(ScopedMixin, Base):
    __tablename__ = "xingjing_task_dead_letters"
    __table_args__ = (Index("ix_xj_dead_letter_scope_status", "tenant_id", "workspace_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationRow(ScopedMixin, Base):
    __tablename__ = "xingjing_notifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "recipient_id", "dedupe_key", name="uq_xj_notification_dedupe"),
        Index("ix_xj_notification_inbox", "tenant_id", "workspace_id", "recipient_id", "read_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    recipient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class NotificationPreferenceRow(ScopedMixin, Base):
    __tablename__ = "xingjing_notification_preferences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "recipient_id", "channel", name="uq_xj_notification_preference"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    recipient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    destination: Mapped[str | None] = mapped_column(String(320))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class NotificationDeliveryRow(ScopedMixin, Base):
    __tablename__ = "xingjing_notification_deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "notification_id", "channel", name="uq_xj_notification_delivery"),
        Index("ix_xj_notification_delivery_claim", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    notification_id: Mapped[str] = mapped_column(String(36), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    destination: Mapped[str] = mapped_column(String(320), nullable=False)
    template_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_receipt: Mapped[dict[str, object] | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.workspace_id:
            raise ValueError("tenant_id 与 workspace_id 不能为空")


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    version: int
    created_at: datetime
    deleted_at: datetime | None
    project_type: str = "drama"
    target_platform: str = "web"
    owner_id: str = "system"
    production_status: str = "draft"


@dataclass(frozen=True)
class Task:
    id: str
    task_type: str
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None


@dataclass(frozen=True)
class OutboxEvent:
    id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, object]


@dataclass(frozen=True)
class AuditRecord:
    id: str
    action: str
    resource_type: str
    resource_id: str


@dataclass(frozen=True)
class CursorPage[T]:
    items: tuple[T, ...]
    next_cursor: str | None


class OptimisticConflict(RuntimeError):
    """预期版本已被其他事务推进。"""


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _project(row: ProjectRow) -> Project:
    created_at = _as_utc(row.created_at)
    assert created_at is not None
    return Project(
        row.id,
        row.name,
        row.version,
        created_at,
        _as_utc(row.deleted_at),
        row.project_type,
        row.target_platform,
        row.owner_id,
        row.production_status,
    )


def _task(row: TaskRow) -> Task:
    return Task(
        row.id,
        row.task_type,
        row.status,
        row.attempt_count,
        row.max_attempts,
        _as_utc(row.next_attempt_at),
        row.last_error_code,
        row.last_error_message,
    )


class _ScopedRepository:
    def __init__(self, session: AsyncSession, scope: Scope):
        self._session = session
        self._scope = scope

    def _scope_clause(self, row_type: type[ScopedMixin]):
        return and_(row_type.tenant_id == self._scope.tenant_id, row_type.workspace_id == self._scope.workspace_id)


class ProjectRepository(_ScopedRepository):
    async def create(
        self,
        idempotency_key: str,
        name: str,
        *,
        project_type: str = "drama",
        target_platform: str = "web",
        owner_id: str = "system",
    ) -> Project:
        existing = await self._session.scalar(
            select(ProjectRow).where(self._scope_clause(ProjectRow), ProjectRow.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return _project(existing)
        row = ProjectRow(
            tenant_id=self._scope.tenant_id,
            workspace_id=self._scope.workspace_id,
            idempotency_key=idempotency_key,
            name=name,
            project_type=project_type,
            target_platform=target_platform,
            owner_id=owner_id,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            existing = await self._session.scalar(
                select(ProjectRow).where(self._scope_clause(ProjectRow), ProjectRow.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise
            return _project(existing)
        return _project(row)

    async def get(self, project_id: str, *, include_deleted: bool = False) -> Project | None:
        clauses = [self._scope_clause(ProjectRow), ProjectRow.id == project_id]
        if not include_deleted:
            clauses.append(ProjectRow.deleted_at.is_(None))
        row = await self._session.scalar(select(ProjectRow).where(*clauses))
        return None if row is None else _project(row)

    async def rename(self, project_id: str, name: str, *, expected_version: int) -> Project:
        return await self._versioned_update(project_id, expected_version, name=name, updated_at=_now())

    async def soft_delete(self, project_id: str, *, expected_version: int) -> Project:
        return await self._versioned_update(project_id, expected_version, deleted_at=_now(), updated_at=_now())

    async def restore(self, project_id: str, *, expected_version: int) -> Project:
        return await self._versioned_update(project_id, expected_version, deleted_at=None, updated_at=_now())

    async def set_production_status(self, project_id: str, production_status: str, *, expected_version: int) -> Project:
        return await self._versioned_update(
            project_id,
            expected_version,
            production_status=production_status,
            updated_at=_now(),
        )

    async def _versioned_update(self, project_id: str, expected_version: int, **values: object) -> Project:
        statement = (
            update(ProjectRow)
            .where(self._scope_clause(ProjectRow), ProjectRow.id == project_id, ProjectRow.version == expected_version)
            .values(**values, version=ProjectRow.version + 1)
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
            raise OptimisticConflict(f"project {project_id} version {expected_version} 已过期")
        row = await self._session.scalar(
            select(ProjectRow).where(self._scope_clause(ProjectRow), ProjectRow.id == project_id)
        )
        assert row is not None
        return _project(row)

    async def list_page(self, *, limit: int, cursor: str | None = None) -> CursorPage[Project]:
        if limit < 1 or limit > 200:
            raise ValueError("limit 必须在 1..200")
        clauses = [self._scope_clause(ProjectRow), ProjectRow.deleted_at.is_(None)]
        if cursor:
            created_at, project_id = _decode_cursor(cursor)
            clauses.append(
                or_(
                    ProjectRow.created_at > created_at,
                    and_(ProjectRow.created_at == created_at, ProjectRow.id > project_id),
                )
            )
        rows = (
            await self._session.scalars(
                select(ProjectRow).where(*clauses).order_by(ProjectRow.created_at, ProjectRow.id).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = _encode_cursor(visible[-1].created_at, visible[-1].id) if has_more else None
        return CursorPage(tuple(_project(row) for row in visible), next_cursor)


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        timestamp, row_id = json.loads(raw)
        return datetime.fromisoformat(timestamp), str(row_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("无效游标") from exc


class InboxRepository(_ScopedRepository):
    async def claim(self, consumer: str, message_id: str) -> bool:
        existing = await self._session.scalar(
            select(InboxRow.id).where(
                self._scope_clause(InboxRow), InboxRow.consumer == consumer, InboxRow.message_id == message_id
            )
        )
        if existing is not None:
            return False
        row = InboxRow(
            tenant_id=self._scope.tenant_id,
            workspace_id=self._scope.workspace_id,
            consumer=consumer,
            message_id=message_id,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            return False
        return True


class OutboxRepository(_ScopedRepository):
    async def add(
        self, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict[str, object]
    ) -> OutboxEvent:
        row = OutboxRow(
            tenant_id=self._scope.tenant_id,
            workspace_id=self._scope.workspace_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
        )
        self._session.add(row)
        await self._session.flush()
        return OutboxEvent(row.id, row.event_type, row.aggregate_type, row.aggregate_id, row.payload)

    async def list_pending(self, *, limit: int) -> tuple[OutboxEvent, ...]:
        rows = (
            await self._session.scalars(
                select(OutboxRow)
                .where(self._scope_clause(OutboxRow), OutboxRow.published_at.is_(None))
                .order_by(OutboxRow.created_at, OutboxRow.id)
                .limit(limit)
            )
        ).all()
        return tuple(
            OutboxEvent(row.id, row.event_type, row.aggregate_type, row.aggregate_id, row.payload) for row in rows
        )


class AuditRepository(_ScopedRepository):
    async def append(
        self,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, object] | None = None,
    ) -> AuditRecord:
        row = AuditRow(
            tenant_id=self._scope.tenant_id,
            workspace_id=self._scope.workspace_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
        self._session.add(row)
        await self._session.flush()
        return AuditRecord(row.id, row.action, row.resource_type, row.resource_id)

    async def list_recent(self, *, limit: int) -> tuple[AuditRecord, ...]:
        rows = (
            await self._session.scalars(
                select(AuditRow)
                .where(self._scope_clause(AuditRow))
                .order_by(AuditRow.created_at.desc(), AuditRow.id.desc())
                .limit(limit)
            )
        ).all()
        return tuple(AuditRecord(row.id, row.action, row.resource_type, row.resource_id) for row in rows)


def build_claim_tasks_statement(scope: Scope, now: datetime, limit: int):
    return (
        select(TaskRow)
        .where(
            TaskRow.tenant_id == scope.tenant_id,
            TaskRow.workspace_id == scope.workspace_id,
            TaskRow.attempt_count < TaskRow.max_attempts,
            or_(
                and_(
                    TaskRow.status.in_(("queued", "retrying")),
                    or_(TaskRow.next_attempt_at.is_(None), TaskRow.next_attempt_at <= now),
                ),
                and_(TaskRow.status == "running", TaskRow.lease_expires_at <= now),
            ),
        )
        .order_by(TaskRow.created_at, TaskRow.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


class TaskRepository(_ScopedRepository):
    async def create(
        self, idempotency_key: str, task_type: str, payload: dict[str, object], *, max_attempts: int
    ) -> Task:
        existing = await self._session.scalar(
            select(TaskRow).where(self._scope_clause(TaskRow), TaskRow.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return _task(existing)
        row = TaskRow(
            tenant_id=self._scope.tenant_id,
            workspace_id=self._scope.workspace_id,
            idempotency_key=idempotency_key,
            task_type=task_type,
            payload=payload,
            max_attempts=max_attempts,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            existing = await self._session.scalar(
                select(TaskRow).where(self._scope_clause(TaskRow), TaskRow.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise
            return _task(existing)
        return _task(row)

    async def get(self, task_id: str) -> Task | None:
        row = await self._session.scalar(select(TaskRow).where(self._scope_clause(TaskRow), TaskRow.id == task_id))
        return None if row is None else _task(row)

    async def claim(self, worker_id: str, *, now: datetime, lease_for: timedelta, limit: int) -> tuple[Task, ...]:
        rows = (await self._session.scalars(build_claim_tasks_statement(self._scope, now, limit))).all()
        for row in rows:
            row.status = "running"
            row.lease_owner = worker_id
            row.lease_expires_at = now + lease_for
            row.attempt_count += 1
            row.updated_at = now
        await self._session.flush()
        return tuple(_task(row) for row in rows)

    async def record_failure(
        self, task_id: str, error_code: str, error_message: str, *, retry_at: datetime | None
    ) -> Task:
        row = await self._session.scalar(
            select(TaskRow).where(self._scope_clause(TaskRow), TaskRow.id == task_id).with_for_update()
        )
        if row is None:
            raise LookupError(task_id)
        row.last_error_code = error_code
        row.last_error_message = error_message
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_attempt_at = retry_at
        row.status = "retrying" if retry_at is not None and row.attempt_count < row.max_attempts else "failed"
        row.updated_at = _now()
        await self._session.flush()
        return _task(row)


SessionFactory = async_sessionmaker[AsyncSession]


class PersistenceUnitOfWork:
    """同一 AsyncSession 中提交业务变更、审计、inbox 与 outbox。"""

    def __init__(self, session_factory: SessionFactory | Callable[[], AsyncSession], scope: Scope):
        self._session_factory = session_factory
        self._scope = scope
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> PersistenceUnitOfWork:
        self._session = self._session_factory()
        await self._session.begin()
        self.projects = ProjectRepository(self._session, self._scope)
        self.tasks = TaskRepository(self._session, self._scope)
        self.inbox = InboxRepository(self._session, self._scope)
        self.outbox = OutboxRepository(self._session, self._scope)
        self.audit = AuditRepository(self._session, self._scope)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        self._require_session()
        await self._session.commit()  # pyright: ignore[reportOptionalMemberAccess]

    async def rollback(self) -> None:
        self._require_session()
        await self._session.rollback()  # pyright: ignore[reportOptionalMemberAccess]

    def _require_session(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork 尚未进入上下文")

    projects: ProjectRepository
    tasks: TaskRepository
    inbox: InboxRepository
    outbox: OutboxRepository
    audit: AuditRepository
