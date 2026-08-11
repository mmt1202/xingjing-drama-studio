from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True, slots=True)
class ObservabilityContext:
    request_id: str | None = None
    tenant_id: str | None = None
    actor_id: str | None = None
    task_id: str | None = None
    business_object_type: str | None = None
    business_object_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    def as_headers(self) -> dict[str, str]:
        names = {
            "request_id": "x-request-id",
            "tenant_id": "x-tenant-id",
            "actor_id": "x-actor-id",
            "task_id": "x-task-id",
            "business_object_type": "x-business-object-type",
            "business_object_id": "x-business-object-id",
        }
        return {header: value for field, header in names.items() if (value := getattr(self, field)) is not None}


_CURRENT_CONTEXT: ContextVar[ObservabilityContext] = ContextVar(
    "xingjing_observability_context", default=ObservabilityContext()
)


@dataclass(frozen=True, slots=True)
class ContextBinding:
    _token: Token[ObservabilityContext]

    def reset(self) -> None:
        _CURRENT_CONTEXT.reset(self._token)


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    value: ObservabilityContext

    def as_headers(self) -> dict[str, str]:
        return self.value.as_headers()

    @contextmanager
    def activate(self) -> Iterator[ObservabilityContext]:
        token = bind_context(self.value)
        try:
            yield self.value
        finally:
            token.reset()


def current_context() -> ObservabilityContext:
    return _CURRENT_CONTEXT.get()


def bind_context(context: ObservabilityContext) -> ContextBinding:
    return ContextBinding(_CURRENT_CONTEXT.set(context))


@contextmanager
def context_scope(**changes: str | None) -> Iterator[ObservabilityContext]:
    unknown = changes.keys() - asdict(ObservabilityContext()).keys()
    if unknown:
        raise TypeError(f"unknown observability context fields: {sorted(unknown)}")
    value = replace(current_context(), **changes)
    token = bind_context(value)
    try:
        yield value
    finally:
        token.reset()


def capture_context() -> ContextSnapshot:
    return ContextSnapshot(current_context())
