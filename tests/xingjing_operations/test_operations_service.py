from datetime import UTC, datetime

import pytest

from server.xingjing_admin_governance.runtime import CommandRow
from server.xingjing_operations import (
    Actor,
    InMemoryOperationsRepository,
    OperationsService,
    ProbeReading,
    VersionConflict,
)
from server.xingjing_operations.runtime import _filter_records, _probe_dependency

NOW = datetime(2026, 7, 15, tzinfo=UTC)
ACTOR = Actor("tenant-a", "ops-1", "req-1")


class StaticProbe:
    def __init__(self, readings):
        self.readings = readings

    def collect(self, tenant_id, observed_at):
        return self.readings


class BrokenProbe:
    def collect(self, tenant_id, observed_at):
        raise OSError("monitor unavailable")


def test_snapshot_preserves_real_probe_state_and_marks_failed_sources_unknown():
    service = OperationsService(
        InMemoryOperationsRepository(),
        service_probe=StaticProbe([ProbeReading("api", "healthy", {"latency_ms": 12})]),
        queue_probe=StaticProbe([ProbeReading("generation", "degraded", {"depth": 41})]),
        storage_probe=BrokenProbe(),
        clock=lambda: NOW,
    )

    snapshot = service.capture_snapshot(ACTOR)

    assert [(item.name, item.status) for item in snapshot.services] == [("api", "healthy")]
    assert [(item.name, item.status) for item in snapshot.queues] == [("generation", "degraded")]
    assert snapshot.storage_source.status == "unavailable"
    assert snapshot.storage == ()


def test_incident_lifecycle_uses_idempotency_and_optimistic_versioning():
    service = OperationsService(InMemoryOperationsRepository(), clock=lambda: NOW)

    first = service.open_incident(ACTOR, "idem-1", "queue", "q-1", "critical", "backlog")
    replay = service.open_incident(ACTOR, "idem-1", "queue", "q-1", "critical", "backlog")
    assert replay == first

    acknowledged = service.acknowledge_incident(ACTOR, "idem-2", first.id, first.version, "ops-1")
    assert acknowledged.status == "acknowledged"
    with pytest.raises(VersionConflict):
        service.resolve_incident(ACTOR, "idem-3", first.id, first.version, "recovered")

    resolved = service.resolve_incident(ACTOR, "idem-3", first.id, acknowledged.version, "recovered")
    assert resolved.status == "resolved"
    assert service.audit.query(request_id="req-1")[-1].result == "success"


def test_shared_admin_idempotency_is_scoped_to_tenant_and_workspace():
    columns = CommandRow.__table__.columns
    assert "tenant_id" in columns
    assert "workspace_id" in columns
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in CommandRow.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id", "workspace_id", "actor_id", "idempotency_key") in unique_columns


def test_admin_listing_combines_search_and_multiple_status_filters():
    records = [
        {"id": "queue-1", "name": "视频生成队列", "status": "backlog"},
        {"id": "queue-2", "name": "图片生成队列", "status": "healthy"},
        {"id": "service-1", "name": "视频服务", "status": "healthy"},
    ]

    filtered = _filter_records(records, search="视频", status="healthy,backlog")

    assert [item["id"] for item in filtered] == ["queue-1", "service-1"]


def test_storage_dependency_probe_reports_real_path_state(tmp_path):
    observed = datetime(2026, 8, 11, tzinfo=UTC)

    healthy = _probe_dependency(
        "object-storage", "对象存储", "storage", str(tmp_path), observed
    )
    missing = _probe_dependency(
        "object-storage", "对象存储", "storage", str(tmp_path / "missing"), observed
    )

    assert healthy["status"] == "healthy"
    assert healthy["details"]["writable"] is True
    assert missing["status"] == "degraded"
