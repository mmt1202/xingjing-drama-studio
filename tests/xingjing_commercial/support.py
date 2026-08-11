from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock

from server.xingjing_commercial import (
    AccountingReceipt,
    AccountingRequest,
    AuditEvent,
    CommercialOrder,
    VersionConflict,
)
from server.xingjing_commercial.models import CommandRecord


class FakeAccountingPort:
    def __init__(self) -> None:
        self._rejections: set[str] = set()
        self._effective: dict[tuple[str, str], tuple[AccountingRequest, AccountingReceipt]] = {}
        self.calls: list[AccountingRequest] = []

    def reject_next(self, action: str) -> None:
        self._rejections.add(action)

    def effective_operations(self, action: str) -> tuple[AccountingRequest, ...]:
        return tuple(request for (kind, _), (request, _) in self._effective.items() if kind == action)

    def freeze_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._execute("freeze", request)

    def resume_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._execute("resume", request)

    def pay_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._execute("pay", request)

    def _execute(self, action: str, request: AccountingRequest) -> AccountingReceipt:
        if request.action != action:
            raise AssertionError("accounting action does not match the invoked port operation")
        self.calls.append(request)
        if action in self._rejections:
            self._rejections.remove(action)
            return AccountingReceipt(request.operation_id, "rejected", False, datetime.now(UTC), "REJECTED")
        key = (action, request.operation_id)
        if existing := self._effective.get(key):
            return existing[1]
        receipt = AccountingReceipt(
            request.operation_id,
            f"receipt-{len(self._effective) + 1}",
            True,
            datetime.now(UTC),
        )
        self._effective[key] = (request, receipt)
        return receipt


class FakeDeliveryArtifactVerifier:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid
        self.calls: list[tuple[str, str, str]] = []

    def verify_selected_artifact(
        self, *, workspace_id: str, artifact_version_id: str, artifact_digest: str
    ) -> bool:
        self.calls.append((workspace_id, artifact_version_id, artifact_digest))
        return self.valid


class _MemoryUnitOfWork:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    def get_order(self, owner_workspace_id: str, order_id: str) -> CommercialOrder | None:
        return self.state["orders"].get((owner_workspace_id, order_id))  # type: ignore[union-attr, no-any-return]

    def put_order(self, order: CommercialOrder, expected_version: int | None) -> None:
        key = (order.owner_workspace_id, order.id)
        existing = self.state["orders"].get(key)  # type: ignore[union-attr]
        if expected_version is None:
            if existing is not None:
                raise VersionConflict()
        elif existing is None or existing.version != expected_version:
            raise VersionConflict()
        self.state["orders"][key] = order  # type: ignore[index]

    def get_command(self, workspace_id: str, scope: str, idempotency_key: str) -> CommandRecord | None:
        key = (workspace_id, scope, idempotency_key)
        return self.state["commands"].get(key)  # type: ignore[union-attr, no-any-return]

    def put_command(self, record: CommandRecord) -> None:
        key = (record.workspace_id, record.scope, record.idempotency_key)
        self.state["commands"][key] = record  # type: ignore[index]

    def append_audit(self, event: AuditEvent) -> None:
        self.state["audits"].append(event)  # type: ignore[union-attr]

    def list_audit(self, workspace_id: str, object_id: str) -> tuple[AuditEvent, ...]:
        return tuple(
            event
            for event in self.state["audits"]  # type: ignore[union-attr]
            if event.workspace_id == workspace_id and event.object_id == object_id
        )


class _Atomic(AbstractContextManager[_MemoryUnitOfWork]):
    def __init__(self, repository: InMemoryCommercialRepository) -> None:
        self.repository = repository

    def __enter__(self) -> _MemoryUnitOfWork:
        self.repository._lock.acquire()
        self.snapshot = deepcopy(self.repository._state)
        return _MemoryUnitOfWork(self.repository._state)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        commit_failure = exc_type is None and self.repository._fail_next_commit
        if exc_type is not None or commit_failure:
            self.repository._state = self.snapshot
        if commit_failure:
            self.repository._fail_next_commit = False
        self.repository._lock.release()
        if commit_failure:
            raise RuntimeError("simulated commit failure")


class InMemoryCommercialRepository:
    """Test-only transactional adapter; production must implement CommercialRepository durably."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._state: dict[str, object] = {"orders": {}, "commands": {}, "audits": []}
        self._fail_next_commit = False

    def atomic(self) -> _Atomic:
        return _Atomic(self)

    def fail_next_commit(self) -> None:
        self._fail_next_commit = True

    def get_order(self, owner_workspace_id: str, order_id: str) -> CommercialOrder | None:
        with self._lock:
            return deepcopy(self._state["orders"].get((owner_workspace_id, order_id)))  # type: ignore[union-attr, no-any-return]
