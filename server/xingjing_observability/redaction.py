from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(^|[_-])(authorization|cookie|password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)s?($|[_-])",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*=\s*([^\s&;,]+)")
_URI_CREDENTIAL = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/\s]+):(?P<secret>[^@/\s]+)@", re.I)


def _redact_text(value: str) -> str:
    value = _BEARER.sub(f"Bearer {REDACTED}", value)
    value = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", value)
    return _URI_CREDENTIAL.sub(lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@", value)


def redact(value: Any, *, key: str | None = None) -> Any:
    """返回可安全记录的深拷贝；未知对象以字符串表示并继续文本脱敏。"""
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact(item) for item in value]
    return _redact_text(str(value))
