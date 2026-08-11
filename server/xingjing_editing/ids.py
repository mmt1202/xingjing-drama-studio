from __future__ import annotations

import secrets
import time
import uuid


class Uuid7IdGenerator:
    def new_id(self, kind: str) -> str:
        del kind
        timestamp_ms = time.time_ns() // 1_000_000
        value = (timestamp_ms & ((1 << 48) - 1)) << 80
        value |= 0x7 << 76
        value |= secrets.randbits(12) << 64
        value |= 0b10 << 62
        value |= secrets.randbits(62)
        return str(uuid.UUID(int=value))
