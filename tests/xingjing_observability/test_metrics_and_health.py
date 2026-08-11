import pytest

from server.xingjing_observability.health import HealthChecker, HealthResult, HealthStatus
from server.xingjing_observability.metrics import (
    CardinalityLimitError,
    InMemoryMetricSink,
    LabelContractError,
    MetricRegistry,
    standard_metric_definitions,
)


def test_standard_metrics_cover_required_domains_and_reject_identity_labels():
    definitions = standard_metric_definitions()
    names = {definition.name for definition in definitions}
    assert names == {
        "xingjing_api_requests_total",
        "xingjing_api_request_duration_seconds",
        "xingjing_queue_depth",
        "xingjing_model_calls_total",
        "xingjing_media_operations_total",
        "xingjing_database_operations_total",
        "xingjing_object_storage_operations_total",
        "xingjing_billing_operations_total",
    }
    assert all("tenant_id" not in definition.allowed_labels for definition in definitions)


def test_metric_registry_enforces_label_schema_and_value_cardinality():
    sink = InMemoryMetricSink()
    registry = MetricRegistry(sink, standard_metric_definitions(), max_values_per_label=2)

    registry.record("xingjing_api_requests_total", 1, {"method": "GET", "route": "/projects", "status_class": "2xx"})
    registry.record("xingjing_api_requests_total", 1, {"method": "POST", "route": "/projects", "status_class": "2xx"})
    with pytest.raises(CardinalityLimitError):
        registry.record(
            "xingjing_api_requests_total", 1, {"method": "DELETE", "route": "/projects", "status_class": "2xx"}
        )
    with pytest.raises(LabelContractError):
        registry.record(
            "xingjing_api_requests_total",
            1,
            {"method": "GET", "route": "/projects", "status_class": "2xx", "tenant_id": "tenant-1"},
        )

    assert len(sink.observations) == 2


async def test_health_checker_distinguishes_liveness_and_readiness_with_real_probe_results():
    async def postgres_probe() -> HealthResult:
        return HealthResult.healthy("postgres", observed_at="2026-07-15T10:00:00Z", latency_ms=4.2)

    async def mq_probe() -> HealthResult:
        return HealthResult.unhealthy("rabbitmq", observed_at="2026-07-15T10:00:01Z", detail="connection refused")

    checker = HealthChecker(service="python-ai", version="abc123")
    checker.register("postgres", postgres_probe, critical=True)
    checker.register("rabbitmq", mq_probe, critical=True)

    live = checker.liveness(observed_at="2026-07-15T10:00:02Z")
    ready = await checker.readiness(observed_at="2026-07-15T10:00:03Z")

    assert live.status is HealthStatus.HEALTHY
    assert live.checks == ()
    assert ready.status is HealthStatus.UNHEALTHY
    assert [check.component for check in ready.checks] == ["postgres", "rabbitmq"]
    assert ready.checks[1].detail == "connection refused"
    assert ready.to_dict()["status"] == "unhealthy"
