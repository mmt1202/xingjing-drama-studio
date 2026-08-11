from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from .models import AccountingReceipt, AccountingRequest, AuditEvent, CommandRecord, CommercialOrder


class CommercialUnitOfWork(Protocol):
    def get_order(self, owner_workspace_id: str, order_id: str) -> CommercialOrder | None: ...

    def put_order(self, order: CommercialOrder, expected_version: int | None) -> None: ...

    def get_command(self, workspace_id: str, scope: str, idempotency_key: str) -> CommandRecord | None: ...

    def put_command(self, record: CommandRecord) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def list_audit(self, workspace_id: str, object_id: str) -> tuple[AuditEvent, ...]: ...


class CommercialRepository(Protocol):
    """Durable persistence boundary for the commercial aggregate.

    Production adapters must use a real database transaction, scope every resource
    lookup by ``owner_workspace_id``, enforce ``expected_version`` with compare-and-swap,
    and commit the aggregate, idempotency record, and audit event atomically.
    """

    def atomic(self) -> AbstractContextManager[CommercialUnitOfWork]: ...


class AccountingPort(Protocol):
    """Exclusive production boundary for settlement-side financial effects."""

    def freeze_settlement(self, request: AccountingRequest) -> AccountingReceipt: ...

    def resume_settlement(self, request: AccountingRequest) -> AccountingReceipt: ...

    def pay_settlement(self, request: AccountingRequest) -> AccountingReceipt: ...


class DeliveryArtifactPort(Protocol):
    """Authoritative catalog boundary for immutable commercial deliverables."""

    def verify_selected_artifact(
        self, *, workspace_id: str, artifact_version_id: str, artifact_digest: str
    ) -> bool: ...
