from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import portalocker

from .errors import ContractViolation, IdempotencyConflict, StoryboardNotFound, VersionConflict
from .models import Storyboard
from .ports import AuditEvent, CommitOutcome, StoryboardScope


class AtomicFileStoryboardRepository:
    """跨进程锁 + 原子替换的耐久适配器；事务同时覆盖版本、幂等收据和审计。"""

    def __init__(self, root: Path, *, lock_timeout_seconds: float = 10) -> None:
        self._root = root
        self._lock_timeout_seconds = lock_timeout_seconds

    def replay(self, scope: StoryboardScope, *, idempotency_key: str, fingerprint: str) -> Storyboard | None:
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            state = self._read_state(state_path)
            receipt = self._receipt(state, idempotency_key)
            if receipt is None:
                return None
            self._assert_fingerprint(receipt, fingerprint)
            return Storyboard.from_dict(self._mapping(receipt.get("result")))

    def commit(
        self,
        scope: StoryboardScope,
        storyboard: Storyboard,
        *,
        expected_version: int | None,
        idempotency_key: str,
        fingerprint: str,
        audit: AuditEvent,
    ) -> CommitOutcome:
        self._assert_scope(scope, storyboard)
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            state = self._read_state(state_path)
            existing_receipt = self._receipt(state, idempotency_key)
            if existing_receipt is not None:
                self._assert_fingerprint(existing_receipt, fingerprint)
                replayed = Storyboard.from_dict(self._mapping(existing_receipt.get("result")))
                return CommitOutcome(replayed, True)

            storyboards = self._storyboards(state)
            current_raw = storyboards.get(storyboard.storyboard_id)
            current = Storyboard.from_dict(self._mapping(current_raw)) if current_raw is not None else None
            if expected_version is None:
                if current is not None:
                    raise VersionConflict("VERSION_CONFLICT", details={"current_version": current.version})
            elif current is None:
                raise StoryboardNotFound("STORYBOARD_NOT_FOUND")
            elif current.version != expected_version:
                raise VersionConflict("VERSION_CONFLICT", details={"current_version": current.version})

            serialized = storyboard.to_dict()
            storyboards[storyboard.storyboard_id] = serialized
            receipts = self._receipts(state)
            receipts[idempotency_key] = {"fingerprint": fingerprint, "result": serialized}
            audits = self._audits(state)
            audits.append(audit.to_dict())
            self._atomic_write(state_path, state)
            return CommitOutcome(storyboard, False)

    def replay_export(
        self, scope: StoryboardScope, *, idempotency_key: str, fingerprint: str
    ) -> dict[str, object] | None:
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            state = self._read_state(state_path)
            receipt = self._receipt(state, self._export_key(idempotency_key))
            if receipt is None:
                return None
            self._assert_fingerprint(receipt, fingerprint)
            return dict(self._mapping(receipt.get("result")))

    def commit_export(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        fingerprint: str,
        result: dict[str, object],
        audit: AuditEvent,
    ) -> tuple[dict[str, object], bool]:
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            state = self._read_state(state_path)
            receipt_key = self._export_key(idempotency_key)
            existing_receipt = self._receipt(state, receipt_key)
            if existing_receipt is not None:
                self._assert_fingerprint(existing_receipt, fingerprint)
                return dict(self._mapping(existing_receipt.get("result"))), True
            self._receipts(state)[receipt_key] = {"fingerprint": fingerprint, "result": result}
            self._audits(state).append(audit.to_dict())
            self._atomic_write(state_path, state)
            return dict(result), False

    def get(self, scope: StoryboardScope, storyboard_id: str) -> Storyboard | None:
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            raw = self._storyboards(self._read_state(state_path)).get(storyboard_id)
            if raw is None:
                return None
            storyboard = Storyboard.from_dict(self._mapping(raw))
            return storyboard if storyboard.project_id == scope.project_id else None

    def list(self, scope: StoryboardScope) -> tuple[Storyboard, ...]:
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            storyboards = (
                Storyboard.from_dict(self._mapping(item))
                for item in self._storyboards(self._read_state(state_path)).values()
            )
            return tuple(item for item in storyboards if item.project_id == scope.project_id)

    def append_audit(self, event: AuditEvent) -> None:
        scope = StoryboardScope(event.tenant_id, event.workspace_id, event.project_id)
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            state = self._read_state(state_path)
            self._audits(state).append(event.to_dict())
            self._atomic_write(state_path, state)

    def list_audit(self, scope: StoryboardScope, *, storyboard_id: str | None = None) -> tuple[AuditEvent, ...]:
        state_path, lock_path = self._paths(scope)
        with portalocker.Lock(lock_path, mode="a", timeout=self._lock_timeout_seconds):
            events = tuple(
                AuditEvent.from_dict(self._mapping(item)) for item in self._audits(self._read_state(state_path))
            )
        return tuple(
            event
            for event in events
            if event.project_id == scope.project_id and (storyboard_id is None or event.object_id == storyboard_id)
        )

    def _paths(self, scope: StoryboardScope) -> tuple[Path, Path]:
        scope_key = hashlib.sha256(f"{scope.tenant_id}\x1f{scope.workspace_id}".encode()).hexdigest()
        directory = self._root / scope_key
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "storyboards.json", directory / "storyboards.lock"

    @staticmethod
    def _assert_scope(scope: StoryboardScope, storyboard: Storyboard) -> None:
        if (storyboard.tenant_id, storyboard.workspace_id, storyboard.project_id) != (
            scope.tenant_id,
            scope.workspace_id,
            scope.project_id,
        ):
            raise ContractViolation("STORYBOARD_SCOPE_MISMATCH")

    @staticmethod
    def _empty_state() -> dict[str, object]:
        return {"schema_version": 1, "storyboards": {}, "idempotency": {}, "audit": []}

    @classmethod
    def _read_state(cls, path: Path) -> dict[str, object]:
        if not path.exists():
            return cls._empty_state()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ContractViolation("INVALID_PERSISTED_STATE") from error
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ContractViolation("UNSUPPORTED_STORYBOARD_STORE_SCHEMA")
        return cast(dict[str, object], raw)

    @staticmethod
    def _storyboards(state: dict[str, object]) -> dict[str, object]:
        raw = state.get("storyboards")
        if not isinstance(raw, dict):
            raise ContractViolation("INVALID_PERSISTED_STATE")
        return cast(dict[str, object], raw)

    @staticmethod
    def _receipts(state: dict[str, object]) -> dict[str, object]:
        raw = state.get("idempotency")
        if not isinstance(raw, dict):
            raise ContractViolation("INVALID_PERSISTED_STATE")
        return cast(dict[str, object], raw)

    @staticmethod
    def _audits(state: dict[str, object]) -> list[object]:
        raw = state.get("audit")
        if not isinstance(raw, list):
            raise ContractViolation("INVALID_PERSISTED_STATE")
        return raw

    @classmethod
    def _receipt(cls, state: dict[str, object], key: str) -> Mapping[str, object] | None:
        raw = cls._receipts(state).get(key)
        return None if raw is None else cls._mapping(raw)

    @staticmethod
    def _assert_fingerprint(receipt: Mapping[str, object], fingerprint: str) -> None:
        if receipt.get("fingerprint") != fingerprint:
            raise IdempotencyConflict("IDEMPOTENCY_KEY_REUSED")

    @staticmethod
    def _export_key(idempotency_key: str) -> str:
        return f"export\x1f{idempotency_key}"

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ContractViolation("INVALID_PERSISTED_STATE")
        return cast(Mapping[str, object], value)

    @staticmethod
    def _atomic_write(path: Path, state: dict[str, object]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix="storyboards-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
