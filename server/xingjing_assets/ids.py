from __future__ import annotations

import secrets
import time
import uuid


def uuid7() -> str:
    """生成 RFC 9562 UUIDv7 字符串，不依赖进程内序列状态。"""
    timestamp_ms = time.time_ns() // 1_000_000
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return str(uuid.UUID(int=value))
