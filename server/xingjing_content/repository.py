from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

import portalocker

from .models import ScriptDocument, script_from_dict

T = TypeVar("T")


class FileContentRepository:
    """可被 API/数据库适配器替换的持久化实现；每个工作区事务由跨进程文件锁串行化。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    def transact(
        self,
        workspace_id: str,
        idempotency_key: str,
        fingerprint: str,
        operation: Callable[[dict[str, ScriptDocument]], tuple[dict[str, ScriptDocument], T]],
    ) -> T:
        workspace_key = hashlib.sha256(workspace_id.encode()).hexdigest()
        directory = self._root / workspace_key
        directory.mkdir(parents=True, exist_ok=True)
        state_path = directory / "content.json"
        lock_path = directory / "content.lock"
        with portalocker.Lock(lock_path, mode="a", timeout=10):
            state = self._read_state(state_path)
            receipts = cast(dict[str, dict[str, str]], state["idempotency"])
            existing = receipts.get(idempotency_key)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("IDEMPOTENCY_KEY_REUSED")
                scripts = self._scripts_from_state(state)
                return scripts[existing["script_id"]]  # type: ignore[return-value]
            scripts, result = operation(self._scripts_from_state(state))
            script_id = getattr(result, "script_id")
            state["scripts"] = {key: value.to_dict() for key, value in scripts.items()}
            receipts[idempotency_key] = {"fingerprint": fingerprint, "script_id": script_id}
            self._atomic_write(state_path, state)
            return result

    def read_all(self, workspace_id: str) -> dict[str, ScriptDocument]:
        workspace_key = hashlib.sha256(workspace_id.encode()).hexdigest()
        directory = self._root / workspace_key
        state_path = directory / "content.json"
        lock_path = directory / "content.lock"
        directory.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(lock_path, mode="a", timeout=10):
            return self._scripts_from_state(self._read_state(state_path))

    def list_audit(
        self,
        workspace_id: str,
        *,
        project_id: str,
        script_id: str,
        request_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        return ()

    @staticmethod
    def _read_state(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"schema_version": 1, "scripts": {}, "idempotency": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("UNSUPPORTED_CONTENT_SCHEMA")
        return data

    @staticmethod
    def _scripts_from_state(state: dict[str, Any]) -> dict[str, ScriptDocument]:
        raw = state["scripts"]
        if not isinstance(raw, dict):
            raise ValueError("INVALID_CONTENT_STATE")
        return {str(key): script_from_dict(value) for key, value in raw.items() if isinstance(value, dict)}

    @staticmethod
    def _atomic_write(path: Path, state: dict[str, Any]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix="content-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
