from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_tasks import (
    AtomicFileTaskStore,
    InvalidTransition,
    StaleLease,
    TaskService,
    TaskStatus,
)


@pytest.fixture
def clock():
    current = datetime(2026, 7, 15, tzinfo=UTC)

    def now() -> datetime:
        return current

    def advance(delta: timedelta) -> None:
        nonlocal current
        current += delta

    return now, advance


@pytest.fixture
def service(tmp_path, clock):
    now, _ = clock
    return TaskService(AtomicFileTaskStore(tmp_path / "tasks.json"), clock=now)


def test_same_tenant_and_idempotency_key_returns_one_task(service):
    first = service.submit(
        tenant_id="tenant-a", task_type="image.generate", payload={"prompt": "moon"}, idempotency_key="req-1"
    )
    second = service.submit(
        tenant_id="tenant-a", task_type="image.generate", payload={"prompt": "ignored"}, idempotency_key="req-1"
    )

    assert second == first
    assert len(service.list_tasks(tenant_id="tenant-a")) == 1


def test_task_survives_service_restart(tmp_path, clock):
    now, _ = clock
    path = tmp_path / "tasks.json"
    created = TaskService(AtomicFileTaskStore(path), clock=now).submit(
        tenant_id="tenant-a", task_type="video.generate", payload={}, idempotency_key="req-2"
    )

    restored = TaskService(AtomicFileTaskStore(path), clock=now).get(created.id, tenant_id="tenant-a")

    assert restored.id == created.id
    assert restored.status is TaskStatus.QUEUED


def test_claim_heartbeat_and_expired_lease_recovery(service, clock):
    _, advance = clock
    task = service.submit(tenant_id="t", task_type="export", payload={}, idempotency_key="k")
    claimed = service.claim(worker_id="worker-1", lease_for=timedelta(seconds=30))
    assert claimed is not None and claimed.status is TaskStatus.RUNNING

    renewed = service.heartbeat(
        task.id, worker_id="worker-1", lease_token=claimed.lease_token, extend_by=timedelta(seconds=30)
    )
    advance(timedelta(seconds=61))
    recovered = service.recover_expired_leases()

    assert renewed.lease_expires_at is not None
    assert recovered[0].status is TaskStatus.QUEUED
    with pytest.raises(StaleLease):
        service.succeed(task.id, worker_id="worker-1", lease_token=claimed.lease_token, result={})


def test_retry_uses_exponential_backoff_then_dead_letters(service, clock):
    _, advance = clock
    task = service.submit(tenant_id="t", task_type="image.generate", payload={}, idempotency_key="k", max_attempts=2)
    first = service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    assert first is not None
    retrying = service.fail(
        task.id, worker_id="w", lease_token=first.lease_token, reason="provider_busy", retryable=True
    )
    assert retrying.status is TaskStatus.RETRYING
    assert retrying.next_attempt_at == datetime(2026, 7, 15, 0, 0, 1, tzinfo=UTC)

    advance(timedelta(seconds=1))
    second = service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    assert second is not None
    failed = service.fail(
        task.id, worker_id="w", lease_token=second.lease_token, reason="provider_busy", retryable=True
    )

    assert failed.status is TaskStatus.FAILED
    assert service.list_dead_letters(tenant_id="t")[0].task_id == task.id


def test_cancel_timeout_and_compensation_are_audited(service, clock):
    _, advance = clock
    queued = service.submit(tenant_id="t", task_type="export", payload={}, idempotency_key="cancel")
    cancelled = service.request_cancel(queued.id, tenant_id="t", actor_id="user-1")
    assert cancelled.status is TaskStatus.CANCELLED

    running = service.submit(
        tenant_id="t", task_type="video.generate", payload={}, idempotency_key="timeout", timeout=timedelta(seconds=5)
    )
    service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    advance(timedelta(seconds=6))
    assert service.expire_timeouts()[0].status is TaskStatus.TIMED_OUT
    compensated = service.compensate(running.id, tenant_id="t", actor_id="ops", reason="release_reserved_credit")

    assert compensated.compensated_at is not None
    assert [entry.action for entry in service.list_audit(tenant_id="t")][-1] == "task.compensated"


def test_terminal_state_rejects_invalid_transition(service):
    task = service.submit(tenant_id="t", task_type="export", payload={}, idempotency_key="k")
    service.request_cancel(task.id, tenant_id="t", actor_id="u")
    with pytest.raises(InvalidTransition):
        service.request_cancel(task.id, tenant_id="t", actor_id="u")


def test_running_cancel_waits_for_worker_confirmation(service):
    task = service.submit(tenant_id="t", task_type="video.generate", payload={}, idempotency_key="k")
    claimed = service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    assert claimed is not None

    requested = service.request_cancel(task.id, tenant_id="t", actor_id="u")
    confirmed = service.confirm_cancel(task.id, worker_id="w", lease_token=claimed.lease_token)

    assert requested.status is TaskStatus.RUNNING and requested.cancel_requested
    assert confirmed.status is TaskStatus.CANCELLED


def test_batch_items_fail_independently(service):
    tasks = service.submit_batch(
        tenant_id="t",
        task_type="image.generate",
        items=[{"prompt": "a"}, {"prompt": "b"}],
        idempotency_key="batch-1",
    )
    first = service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    assert first is not None
    service.succeed(first.id, worker_id="w", lease_token=first.lease_token, result={})
    second = service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    assert second is not None
    service.fail(second.id, worker_id="w", lease_token=second.lease_token, reason="bad_input", retryable=False)

    states = [service.get(task.id, tenant_id="t").status for task in tasks]
    assert states == [TaskStatus.SUCCEEDED, TaskStatus.FAILED]
