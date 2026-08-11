from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from .models import (
    TERMINAL_STATUSES,
    AuditEntry,
    DeadLetter,
    DomainEvent,
    EventDisposition,
    InvalidTransition,
    Notification,
    StaleLease,
    Task,
    TaskNotFound,
    TaskStatus,
)
from .ports import NotificationSink, NullNotificationSink, TaskStore


def _dump(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, TaskStatus):
        return value.value
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return value


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _task(data: dict[str, Any]) -> Task:
    values = dict(data)
    values["status"] = TaskStatus(values["status"])
    for key in ("created_at", "updated_at", "deadline_at", "next_attempt_at", "lease_expires_at", "compensated_at"):
        values[key] = _dt(values.get(key))
    return Task(**values)


class TaskService:
    def __init__(
        self,
        store: TaskStore,
        *,
        clock: Callable[[], datetime] | None = None,
        notifications: NotificationSink | None = None,
        base_retry_delay: timedelta = timedelta(seconds=1),
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._notifications = notifications or NullNotificationSink()
        self._base_retry_delay = base_retry_delay

    def submit(
        self,
        *,
        tenant_id: str,
        task_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
        timeout: timedelta | None = None,
        actor_id: str = "system",
        request_id: str | None = None,
    ) -> Task:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        now = self._now()

        def operation(state: dict[str, Any]) -> Task:
            index_key = f"{tenant_id}\x1f{idempotency_key}"
            existing = state["idempotency"].get(index_key)
            if existing:
                return _task(state["tasks"][existing])
            task = Task(
                id=str(uuid4()),
                tenant_id=tenant_id,
                task_type=task_type,
                payload=payload,
                idempotency_key=idempotency_key,
                status=TaskStatus.QUEUED,
                created_at=now,
                updated_at=now,
                deadline_at=now + timeout if timeout else None,
                max_attempts=max_attempts,
            )
            state["tasks"][task.id] = _dump(asdict(task))
            state["idempotency"][index_key] = task.id
            self._append_event(state, task, "task.queued", now, payload={"task_type": task_type})
            self._append_audit(state, task, "task.submitted", actor_id, request_id, None, task.status, now)
            return task

        return self._store.transact(operation)

    def submit_batch(
        self,
        *,
        tenant_id: str,
        task_type: str,
        items: list[dict[str, Any]],
        idempotency_key: str,
        max_attempts: int = 3,
        timeout: timedelta | None = None,
        actor_id: str = "system",
        request_id: str | None = None,
    ) -> list[Task]:
        """Create independently retryable items with deterministic child keys.

        A retry after a partial client/network failure completes missing children and
        returns existing ones without rolling back successful tasks.
        """
        return [
            self.submit(
                tenant_id=tenant_id,
                task_type=task_type,
                payload=item,
                idempotency_key=f"{idempotency_key}:{index}",
                max_attempts=max_attempts,
                timeout=timeout,
                actor_id=actor_id,
                request_id=request_id,
            )
            for index, item in enumerate(items)
        ]

    def get(self, task_id: str, *, tenant_id: str) -> Task:
        task = self._get_from(self._store.snapshot(), task_id)
        if task.tenant_id != tenant_id:
            raise TaskNotFound(task_id)
        return task

    def list_tasks(self, *, tenant_id: str) -> list[Task]:
        return sorted(
            (_task(item) for item in self._store.snapshot()["tasks"].values() if item["tenant_id"] == tenant_id),
            key=lambda item: (item.created_at, item.id),
        )

    def claim(self, *, worker_id: str, lease_for: timedelta) -> Task | None:
        now = self._now()

        def operation(state: dict[str, Any]) -> Task | None:
            candidates = sorted(
                (_task(item) for item in state["tasks"].values()), key=lambda item: (item.created_at, item.id)
            )
            for task in candidates:
                eligible = task.status is TaskStatus.QUEUED or (
                    task.status is TaskStatus.RETRYING
                    and task.next_attempt_at is not None
                    and task.next_attempt_at <= now
                )
                if not eligible or (task.deadline_at is not None and task.deadline_at <= now):
                    continue
                claimed = replace(
                    task,
                    status=TaskStatus.RUNNING,
                    attempts=task.attempts + 1,
                    worker_id=worker_id,
                    lease_token=str(uuid4()),
                    lease_expires_at=now + lease_for,
                    next_attempt_at=None,
                    updated_at=now,
                    version=task.version + 1,
                )
                self._save(state, claimed)
                self._append_event(state, claimed, "task.running", now, payload={"attempt": claimed.attempts})
                return claimed
            return None

        return self._store.transact(operation)

    def heartbeat(self, task_id: str, *, worker_id: str, lease_token: str | None, extend_by: timedelta) -> Task:
        now = self._now()
        return self._mutate_leased(
            task_id, worker_id, lease_token, now, lambda task: replace(task, lease_expires_at=now + extend_by)
        )

    def report_progress(self, task_id: str, *, worker_id: str, lease_token: str | None, progress: int) -> Task:
        if progress < 0 or progress > 100:
            raise ValueError("progress must be between 0 and 100")
        now = self._now()

        def change(task: Task) -> Task:
            if progress < task.progress:
                raise InvalidTransition("progress cannot decrease")
            return replace(task, progress=progress)

        updated = self._mutate_leased(task_id, worker_id, lease_token, now, change, event_type="task.progress")
        return updated

    def succeed(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str | None,
        result: dict[str, Any],
        event_id: str | None = None,
    ) -> Task:
        now = self._now()
        task = self._mutate_leased(
            task_id,
            worker_id,
            lease_token,
            now,
            lambda item: replace(
                item,
                status=TaskStatus.SUCCEEDED,
                result=result,
                progress=100,
                worker_id=None,
                lease_token=None,
                lease_expires_at=None,
            ),
            event_type="task.succeeded",
            event_id=event_id,
        )
        self._notify_terminal(task)
        return task

    def fail(self, task_id: str, *, worker_id: str, lease_token: str | None, reason: str, retryable: bool) -> Task:
        now = self._now()

        def operation(state: dict[str, Any]) -> Task:
            task = self._validate_lease(self._get_from(state, task_id), worker_id, lease_token, now)
            will_retry = retryable and task.attempts < task.max_attempts
            updated = replace(
                task,
                status=TaskStatus.RETRYING if will_retry else TaskStatus.FAILED,
                failure_reason=reason,
                next_attempt_at=now + self._base_retry_delay * (2 ** (task.attempts - 1)) if will_retry else None,
                worker_id=None,
                lease_token=None,
                lease_expires_at=None,
                updated_at=now,
                version=task.version + 1,
            )
            self._save(state, updated)
            self._append_event(
                state, updated, "task.retrying" if will_retry else "task.failed", now, payload={"reason": reason}
            )
            if not will_retry:
                self._append_dead_letter(state, updated, reason, {"retryable": retryable}, now)
            return updated

        task = self._store.transact(operation)
        if task.status is TaskStatus.FAILED:
            self._notify_terminal(task)
        return task

    def request_cancel(self, task_id: str, *, tenant_id: str, actor_id: str, request_id: str | None = None) -> Task:
        now = self._now()

        def operation(state: dict[str, Any]) -> Task:
            task = self._get_from(state, task_id)
            if task.tenant_id != tenant_id:
                raise TaskNotFound(task_id)
            if task.status in TERMINAL_STATUSES:
                raise InvalidTransition(f"cannot cancel {task.status}")
            status = TaskStatus.CANCELLED if task.status in {TaskStatus.QUEUED, TaskStatus.RETRYING} else task.status
            updated = replace(
                task,
                status=status,
                cancel_requested=True,
                updated_at=now,
                version=task.version + 1,
                worker_id=None if status is TaskStatus.CANCELLED else task.worker_id,
                lease_token=None if status is TaskStatus.CANCELLED else task.lease_token,
                lease_expires_at=None if status is TaskStatus.CANCELLED else task.lease_expires_at,
            )
            self._save(state, updated)
            self._append_event(
                state, updated, "task.cancelled" if status is TaskStatus.CANCELLED else "task.cancel_requested", now
            )
            self._append_audit(state, updated, "task.cancel_requested", actor_id, request_id, task.status, status, now)
            return updated

        task = self._store.transact(operation)
        if task.status is TaskStatus.CANCELLED:
            self._notify_terminal(task)
        return task

    def confirm_cancel(self, task_id: str, *, worker_id: str, lease_token: str | None) -> Task:
        now = self._now()

        def change(task: Task) -> Task:
            if not task.cancel_requested:
                raise InvalidTransition("cancellation was not requested")
            return replace(
                task,
                status=TaskStatus.CANCELLED,
                worker_id=None,
                lease_token=None,
                lease_expires_at=None,
            )

        task = self._mutate_leased(
            task_id,
            worker_id,
            lease_token,
            now,
            change,
            event_type="task.cancelled",
        )
        self._notify_terminal(task)
        return task

    def recover_expired_leases(self) -> list[Task]:
        now = self._now()

        def operation(state: dict[str, Any]) -> list[Task]:
            recovered: list[Task] = []
            for raw in list(state["tasks"].values()):
                task = _task(raw)
                if (
                    task.status is not TaskStatus.RUNNING
                    or task.lease_expires_at is None
                    or task.lease_expires_at > now
                ):
                    continue
                updated = replace(
                    task,
                    status=TaskStatus.QUEUED,
                    worker_id=None,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=now,
                    version=task.version + 1,
                )
                self._save(state, updated)
                self._append_event(state, updated, "task.lease_expired", now)
                recovered.append(updated)
            return recovered

        return self._store.transact(operation)

    def expire_timeouts(self) -> list[Task]:
        now = self._now()

        def operation(state: dict[str, Any]) -> list[Task]:
            expired: list[Task] = []
            for raw in list(state["tasks"].values()):
                task = _task(raw)
                if task.status in TERMINAL_STATUSES or task.deadline_at is None or task.deadline_at > now:
                    continue
                updated = replace(
                    task,
                    status=TaskStatus.TIMED_OUT,
                    failure_reason="deadline_exceeded",
                    worker_id=None,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=now,
                    version=task.version + 1,
                )
                self._save(state, updated)
                self._append_event(state, updated, "task.timed_out", now)
                self._append_dead_letter(state, updated, "timeout", {}, now)
                expired.append(updated)
            return expired

        tasks = self._store.transact(operation)
        for task in tasks:
            self._notify_terminal(task)
        return tasks

    def compensate(
        self, task_id: str, *, tenant_id: str, actor_id: str, reason: str, request_id: str | None = None
    ) -> Task:
        now = self._now()

        def operation(state: dict[str, Any]) -> Task:
            task = self._get_from(state, task_id)
            if task.tenant_id != tenant_id:
                raise TaskNotFound(task_id)
            if task.status not in TERMINAL_STATUSES:
                raise InvalidTransition("only terminal tasks can be compensated")
            if task.compensated_at is not None:
                return task
            updated = replace(task, compensated_at=now, updated_at=now, version=task.version + 1)
            self._save(state, updated)
            self._append_event(state, updated, "task.compensated", now, payload={"reason": reason})
            self._append_audit(
                state,
                updated,
                "task.compensated",
                actor_id,
                request_id,
                task.status,
                task.status,
                now,
                detail={"reason": reason},
            )
            return updated

        return self._store.transact(operation)

    def ingest_result(
        self,
        task_id: str,
        *,
        tenant_id: str,
        event_id: str,
        outcome: Literal["succeeded", "failed"],
        result: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> EventDisposition:
        now = self._now()

        def operation(state: dict[str, Any]) -> EventDisposition:
            task = self._get_from(state, task_id)
            if task.tenant_id != tenant_id:
                raise TaskNotFound(task_id)
            if event_id in state["event_ids"]:
                return EventDisposition("duplicate", task)
            if task.status in TERMINAL_STATUSES:
                state["event_ids"].append(event_id)
                self._append_dead_letter(state, task, "late_event", {"event_id": event_id, "outcome": outcome}, now)
                return EventDisposition("late", task)
            status = TaskStatus.SUCCEEDED if outcome == "succeeded" else TaskStatus.FAILED
            updated = replace(
                task,
                status=status,
                result=result,
                failure_reason=reason,
                progress=100 if status is TaskStatus.SUCCEEDED else task.progress,
                worker_id=None,
                lease_token=None,
                lease_expires_at=None,
                updated_at=now,
                version=task.version + 1,
            )
            self._save(state, updated)
            self._append_event(
                state,
                updated,
                f"task.{status.value}",
                now,
                event_id=event_id,
                payload={"reason": reason} if reason else {},
            )
            if status is TaskStatus.FAILED:
                self._append_dead_letter(state, updated, reason or "provider_failure", {}, now)
            return EventDisposition("applied", updated)

        disposition = self._store.transact(operation)
        if disposition.disposition == "applied":
            self._notify_terminal(disposition.task)
        return disposition

    def read_events(self, *, tenant_id: str, after_sequence: int = 0, limit: int = 1000) -> list[DomainEvent]:
        events = []
        for raw in self._store.snapshot()["events"]:
            if raw["tenant_id"] == tenant_id and raw["sequence"] > after_sequence:
                values = dict(raw)
                values["occurred_at"] = _dt(values["occurred_at"])
                events.append(DomainEvent(**values))
        return events[:limit]

    def list_dead_letters(self, *, tenant_id: str) -> list[DeadLetter]:
        result = []
        for raw in self._store.snapshot()["dead_letters"]:
            if raw["tenant_id"] == tenant_id:
                values = dict(raw)
                values["created_at"] = _dt(values["created_at"])
                result.append(DeadLetter(**values))
        return result

    def list_audit(self, *, tenant_id: str) -> list[AuditEntry]:
        result = []
        for raw in self._store.snapshot()["audit"]:
            if raw["tenant_id"] == tenant_id:
                values = dict(raw)
                values["occurred_at"] = _dt(values["occurred_at"])
                values["before_status"] = TaskStatus(values["before_status"]) if values["before_status"] else None
                values["after_status"] = TaskStatus(values["after_status"]) if values["after_status"] else None
                result.append(AuditEntry(**values))
        return result

    def _mutate_leased(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str | None,
        now: datetime,
        change: Callable[[Task], Task],
        *,
        event_type: str | None = None,
        event_id: str | None = None,
    ) -> Task:
        def operation(state: dict[str, Any]) -> Task:
            task = self._validate_lease(self._get_from(state, task_id), worker_id, lease_token, now)
            updated = replace(change(task), updated_at=now, version=task.version + 1)
            self._save(state, updated)
            if event_type:
                self._append_event(
                    state,
                    updated,
                    event_type,
                    now,
                    event_id=event_id,
                    payload={"progress": updated.progress} if event_type == "task.progress" else {},
                )
            return updated

        return self._store.transact(operation)

    @staticmethod
    def _validate_lease(task: Task, worker_id: str, lease_token: str | None, now: datetime) -> Task:
        if task.status is not TaskStatus.RUNNING or task.worker_id != worker_id or task.lease_token != lease_token:
            raise StaleLease(task.id)
        if task.lease_expires_at is None or task.lease_expires_at <= now:
            raise StaleLease(task.id)
        return task

    @staticmethod
    def _get_from(state: dict[str, Any], task_id: str) -> Task:
        raw = state["tasks"].get(task_id)
        if raw is None:
            raise TaskNotFound(task_id)
        return _task(raw)

    @staticmethod
    def _save(state: dict[str, Any], task: Task) -> None:
        state["tasks"][task.id] = _dump(asdict(task))

    @staticmethod
    def _append_event(
        state: dict[str, Any],
        task: Task,
        event_type: str,
        now: datetime,
        *,
        event_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        identifier = event_id or str(uuid4())
        if identifier in state["event_ids"]:
            return
        state["event_ids"].append(identifier)
        event = DomainEvent(
            identifier, len(state["events"]) + 1, event_type, 1, task.tenant_id, task.id, now, None, payload or {}
        )
        state["events"].append(_dump(asdict(event)))

    @staticmethod
    def _append_dead_letter(
        state: dict[str, Any], task: Task, reason: str, payload: dict[str, Any], now: datetime
    ) -> None:
        entry = DeadLetter(str(uuid4()), task.id, task.tenant_id, reason, payload, now)
        state["dead_letters"].append(_dump(asdict(entry)))

    @staticmethod
    def _append_audit(
        state: dict[str, Any],
        task: Task,
        action: str,
        actor_id: str,
        request_id: str | None,
        before: TaskStatus | None,
        after: TaskStatus | None,
        now: datetime,
        detail: dict[str, Any] | None = None,
    ) -> None:
        entry = AuditEntry(
            str(uuid4()), task.tenant_id, task.id, action, actor_id, request_id, now, before, after, detail or {}
        )
        state["audit"].append(_dump(asdict(entry)))

    def _notify_terminal(self, task: Task) -> None:
        self._notifications.deliver(
            Notification(
                task.tenant_id,
                "task-owner",
                f"task.{task.status.value}",
                task.id,
                f"task:{task.id}:{task.status.value}",
                {"task_type": task.task_type},
            )
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value
