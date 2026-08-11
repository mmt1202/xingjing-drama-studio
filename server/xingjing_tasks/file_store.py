from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def _empty_state() -> dict[str, Any]:
    return {"tasks": {}, "idempotency": {}, "events": [], "event_ids": [], "dead_letters": [], "audit": []}


class AtomicFileTaskStore:
    """Durable single-process adapter using fsync + atomic replace.

    Multi-process deployments should bind TaskStore to PostgreSQL and implement the
    transaction with row locks/unique constraints. RabbitMQ/Redis remain transports,
    never the task source of truth.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    def transact(self, operation: Callable[[dict[str, Any]], T]) -> T:
        with self._lock:
            state = self._read()
            result = operation(state)
            self._write(state)
            return result

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        with self.path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else _empty_state()

    def _write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(state, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
