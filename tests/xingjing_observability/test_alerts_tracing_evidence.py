import json

from server.xingjing_observability.alerts import (
    AlertEngine,
    AlertRule,
    Comparison,
    IncidentStatus,
    Suppression,
)
from server.xingjing_observability.evidence import EvidenceSource, InMemoryEvidenceSink, RuntimeEvidence
from server.xingjing_observability.tracing import TraceContext, extract_trace_context, inject_trace_context


def test_trace_context_round_trip_and_child_preserves_trace_id():
    parent = TraceContext.from_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    headers = inject_trace_context({}, parent)
    extracted = extract_trace_context(headers)
    child = extracted.child(span_id="b7ad6b7169203331")

    assert headers["traceparent"] == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == parent.span_id
    assert child.to_traceparent() == "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01"


def test_alert_lifecycle_and_suppression_are_deterministic():
    engine = AlertEngine()
    rule = AlertRule(
        rule_id="queue-depth-high",
        metric="xingjing_queue_depth",
        comparison=Comparison.GREATER_THAN,
        threshold=100,
        severity="critical",
        owner="platform-ops",
    )

    firing = engine.evaluate(rule, value=120, environment="prod", observed_at="2026-07-15T10:00:00Z")
    assert firing is not None
    acknowledged = engine.acknowledge(firing.incident_id, actor_id="ops-1", observed_at="2026-07-15T10:01:00Z")
    resolved = engine.evaluate(rule, value=20, environment="prod", observed_at="2026-07-15T10:02:00Z")

    assert resolved is not None
    assert firing.status is IncidentStatus.FIRING
    assert acknowledged.status is IncidentStatus.ACKNOWLEDGED
    assert resolved.status is IncidentStatus.RESOLVED
    assert resolved.incident_id == firing.incident_id

    engine.add_suppression(
        Suppression(
            suppression_id="maintenance-1",
            rule_id=rule.rule_id,
            environment="prod",
            starts_at="2026-07-15T11:00:00Z",
            ends_at="2026-07-15T12:00:00Z",
            reason="planned maintenance",
            actor_id="ops-2",
        )
    )
    suppressed = engine.evaluate(rule, value=130, environment="prod", observed_at="2026-07-15T11:30:00Z")
    assert suppressed is not None
    assert suppressed.status is IncidentStatus.SUPPRESSED
    assert suppressed.suppression_id == "maintenance-1"


def test_runtime_evidence_requires_source_and_redacts_before_serialization():
    sink = InMemoryEvidenceSink()
    evidence = RuntimeEvidence.create(
        evidence_id="EV-FOUND-009-RUN-001",
        subject="python readiness",
        source=EvidenceSource.PROBE,
        source_ref="readiness/postgres",
        outcome="unhealthy",
        observed_at="2026-07-15T10:00:00Z",
        facts={"latency_ms": 5000, "authorization": "Bearer secret"},
    )
    sink.write(evidence)

    payload = json.loads(evidence.to_json())
    assert payload["source"] == "probe"
    assert payload["source_ref"] == "readiness/postgres"
    assert payload["outcome"] == "unhealthy"
    assert payload["facts"]["authorization"] == "[REDACTED]"
    assert sink.items == [evidence]
