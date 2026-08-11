from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.xingjing_tasks import AtomicFileTaskStore, RecordingNotificationSink, TaskService


def test_event_sequence_supports_resume_and_duplicate_or_late_event(tmp_path):
    def now() -> datetime:
        return datetime(2026, 7, 15, tzinfo=UTC)

    service = TaskService(AtomicFileTaskStore(tmp_path / "state.json"), clock=now)
    task = service.submit(tenant_id="t", task_type="video.generate", payload={}, idempotency_key="k")
    claimed = service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    assert claimed is not None
    service.report_progress(task.id, worker_id="w", lease_token=claimed.lease_token, progress=40)
    cursor = service.read_events(tenant_id="t")[0].sequence
    service.succeed(task.id, worker_id="w", lease_token=claimed.lease_token, result={"asset_id": "a1"}, event_id="cb-1")

    duplicate = service.ingest_result(
        task.id, tenant_id="t", event_id="cb-1", outcome="succeeded", result={"asset_id": "a1"}
    )
    late = service.ingest_result(task.id, tenant_id="t", event_id="cb-2", outcome="failed", reason="late failure")

    assert duplicate.disposition == "duplicate"
    assert late.disposition == "late"
    resumed = service.read_events(tenant_id="t", after_sequence=cursor)
    assert [event.sequence for event in resumed] == sorted(event.sequence for event in resumed)
    assert any(event.event_type == "task.succeeded" for event in resumed)
    assert service.list_dead_letters(tenant_id="t")[-1].reason == "late_event"


def test_notification_port_receives_terminal_notification_once(tmp_path):
    sink = RecordingNotificationSink()
    service = TaskService(
        AtomicFileTaskStore(tmp_path / "state.json"),
        clock=lambda: datetime(2026, 7, 15, tzinfo=UTC),
        notifications=sink,
    )
    task = service.submit(tenant_id="t", task_type="export", payload={}, idempotency_key="k")
    claimed = service.claim(worker_id="w", lease_for=timedelta(minutes=1))
    assert claimed is not None
    service.succeed(task.id, worker_id="w", lease_token=claimed.lease_token, result={}, event_id="done-1")
    service.ingest_result(task.id, tenant_id="t", event_id="done-1", outcome="succeeded", result={})

    assert len(sink.deliveries) == 1
    assert sink.deliveries[0].deduplication_key == f"task:{task.id}:succeeded"


def test_external_result_is_recorded_as_a_resumable_event(tmp_path):
    service = TaskService(AtomicFileTaskStore(tmp_path / "state.json"), clock=lambda: datetime(2026, 7, 15, tzinfo=UTC))
    task = service.submit(tenant_id="t", task_type="review", payload={}, idempotency_key="k")

    applied = service.ingest_result(task.id, tenant_id="t", event_id="provider-42", outcome="succeeded", result={})

    assert applied.disposition == "applied"
    assert service.read_events(tenant_id="t")[-1].id == "provider-42"
