from __future__ import annotations

import secrets
import time
import uuid


def uuid7() -> str:
    """Return an RFC 9562 UUIDv7 without process-local sequence state."""
    timestamp_ms = time.time_ns() // 1_000_000
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return str(uuid.UUID(int=value))
