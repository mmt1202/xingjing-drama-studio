from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from .context import current_context
from .redaction import redact

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@dataclass(frozen=True, slots=True)
class StructuredLogEvent:
    timestamp: str
    level: LogLevel
    service: str
    event: str
    message: str
    schema_version: str = "1.0"
    environment: str | None = None
    tenant_id: str | None = None
    actor_id: str | None = None
    request_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    business_object_type: str | None = None
    business_object_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        level: LogLevel,
        service: str,
        event: str,
        message: str,
        environment: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> StructuredLogEvent:
        if not service.strip() or not event.strip():
            raise ValueError("service and event must be non-empty")
        context = current_context()
        return cls(
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            level=level,
            service=service,
            event=event,
            message=redact(message),
            environment=environment,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            request_id=context.request_id,
            task_id=context.task_id,
            trace_id=context.trace_id,
            span_id=context.span_id,
            business_object_type=context.business_object_type,
            business_object_id=context.business_object_id,
            attributes=redact(attributes or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return redact(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class StructuredLogSink(Protocol):
    """日志采集器、OTel Collector 或标准输出适配器应实现的输出端口。"""

    def write(self, event: StructuredLogEvent) -> None: ...


class StructuredLogger:
    def __init__(self, sink: StructuredLogSink, *, service: str, environment: str | None = None) -> None:
        self._sink = sink
        self._service = service
        self._environment = environment

    def emit(
        self,
        *,
        level: LogLevel,
        event: str,
        message: str,
        attributes: dict[str, Any] | None = None,
    ) -> StructuredLogEvent:
        record = StructuredLogEvent.create(
            level=level,
            service=self._service,
            event=event,
            message=message,
            environment=self._environment,
            attributes=attributes,
        )
        self._sink.write(record)
        return record
