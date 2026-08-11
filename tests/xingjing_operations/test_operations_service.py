from datetime import UTC, datetime

import pytest

from server.xingjing_operations import (
    Actor,
    InMemoryOperationsRepository,
    OperationsService,
    ProbeReading,
    VersionConflict,
)

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
