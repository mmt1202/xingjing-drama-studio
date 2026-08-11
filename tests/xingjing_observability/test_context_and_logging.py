import json

from server.xingjing_observability.context import (
    ObservabilityContext,
    bind_context,
    capture_context,
    context_scope,
    current_context,
)
from server.xingjing_observability.logging import StructuredLogEvent, StructuredLogger
from server.xingjing_observability.redaction import REDACTED, redact


def test_context_scope_restores_parent_and_snapshot_is_transportable():
    parent = ObservabilityContext(request_id="req-1", tenant_id="tenant-1", actor_id="user-1")
    token = bind_context(parent)
    try:
        with context_scope(task_id="task-9", business_object_type="project", business_object_id="project-7"):
            child = current_context()
            snapshot = capture_context()
            assert child.task_id == "task-9"
            assert snapshot.as_headers() == {
                "x-request-id": "req-1",
                "x-tenant-id": "tenant-1",
                "x-actor-id": "user-1",
                "x-task-id": "task-9",
                "x-business-object-type": "project",
                "x-business-object-id": "project-7",
            }
        assert current_context() == parent
    finally:
        token.reset()


def test_structured_log_contract_uses_context_and_is_json_serializable():
    with context_scope(request_id="req-2", tenant_id="tenant-2", task_id="task-2"):
        event = StructuredLogEvent.create(
            level="INFO",
            service="ai-worker",
            event="generation.started",
            message="generation accepted",
            attributes={"provider": "example", "attempt": 1},
        )

    payload = event.to_dict()
    assert payload["schema_version"] == "1.0"
    assert payload["service"] == "ai-worker"
    assert payload["request_id"] == "req-2"
    assert payload["tenant_id"] == "tenant-2"
    assert payload["task_id"] == "task-2"
    assert payload["event"] == "generation.started"
    assert payload["attributes"] == {"provider": "example", "attempt": 1}
    assert json.loads(event.to_json()) == payload


def test_redaction_recurses_and_scrubs_tokens_embedded_in_text():
    source = {
        "authorization": "Bearer very-secret-token",
        "nested": {"api_key": "sk-live-secret", "safe": "visible"},
        "items": ["password=hunter2", {"refresh_token": "refresh-secret"}],
    }

    result = redact(source)

    assert result == {
        "authorization": REDACTED,
        "nested": {"api_key": REDACTED, "safe": "visible"},
        "items": [f"password={REDACTED}", {"refresh_token": REDACTED}],
    }
    assert "very-secret-token" not in json.dumps(result)
    assert "hunter2" not in json.dumps(result)


def test_structured_logger_delivers_redacted_event_through_backend_port():
    class Sink:
        def __init__(self):
            self.events = []

        def write(self, event):
            self.events.append(event)

    sink = Sink()
    logger = StructuredLogger(sink, service="gateway", environment="test")
    event = logger.emit(
        level="ERROR",
        event="request.failed",
        message="upstream rejected request",
        attributes={"authorization": "Bearer secret", "status_code": 502},
    )

    assert sink.events == [event]
    assert event.attributes == {"authorization": REDACTED, "status_code": 502}
    assert event.to_dict()["attributes"] == {"authorization": REDACTED, "status_code": 502}
