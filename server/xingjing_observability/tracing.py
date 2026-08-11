from __future__ import annotations

import re
import secrets
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

_TRACEPARENT = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    trace_flags: str = "01"
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", self.trace_id) or self.trace_id == "0" * 32:
            raise ValueError("invalid W3C trace id")
        if not re.fullmatch(r"[0-9a-f]{16}", self.span_id) or self.span_id == "0" * 16:
            raise ValueError("invalid W3C span id")
        if not re.fullmatch(r"[0-9a-f]{2}", self.trace_flags):
            raise ValueError("invalid W3C trace flags")

    @classmethod
    def from_traceparent(cls, value: str) -> TraceContext:
        match = _TRACEPARENT.fullmatch(value.strip().lower())
        if match is None or match.group("version") == "ff":
            raise ValueError("invalid traceparent header")
        return cls(match.group("trace_id"), match.group("span_id"), match.group("flags"))

    @classmethod
    def new(cls) -> TraceContext:
        return cls(secrets.token_hex(16), secrets.token_hex(8))

    def child(self, *, span_id: str | None = None) -> TraceContext:
        return TraceContext(self.trace_id, span_id or secrets.token_hex(8), self.trace_flags, self.span_id)

    def to_traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"


def extract_trace_context(headers: Mapping[str, str]) -> TraceContext:
    normalized = {key.lower(): value for key, value in headers.items()}
    try:
        value = normalized["traceparent"]
    except KeyError as exc:
        raise ValueError("traceparent header is required") from exc
    return TraceContext.from_traceparent(value)


def inject_trace_context(headers: MutableMapping[str, str], context: TraceContext) -> MutableMapping[str, str]:
    headers["traceparent"] = context.to_traceparent()
    return headers
