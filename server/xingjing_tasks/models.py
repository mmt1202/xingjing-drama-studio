from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMED_OUT})


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    tenant_id: str
    task_type: str
    payload: dict[str, Any]
    idempotency_key: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None = None
    attempts: int = 0
    max_attempts: int = 3
    next_attempt_at: datetime | None = None
    worker_id: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    progress: int = 0
    result: dict[str, Any] | None = None
    failure_reason: str | None = None
    cancel_requested: bool = False
    compensated_at: datetime | None = None
    version: int = 1


@dataclass(frozen=True, slots=True)
class DomainEvent:
    id: str
    sequence: int
    event_type: str
    schema_version: int
    tenant_id: str
    subject_id: str
    occurred_at: datetime
    causation_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeadLetter:
    id: str
    task_id: str
    tenant_id: str
    reason: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: str
    tenant_id: str
    task_id: str
    action: str
    actor_id: str
    request_id: str | None
    occurred_at: datetime
    before_status: TaskStatus | None
    after_status: TaskStatus | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Notification:
    tenant_id: str
    recipient_id: str
    kind: str
    subject_id: str
    deduplication_key: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventDisposition:
    disposition: str
    task: Task


class TaskError(RuntimeError):
    pass


class TaskNotFound(TaskError):
    pass


class InvalidTransition(TaskError):
    pass


class StaleLease(TaskError):
    pass
