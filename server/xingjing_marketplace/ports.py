from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeVar

from .domain import (
    AuditEvent,
    ForkProjectSnapshot,
    ForkRecord,
    IdempotencyRecord,
    IdempotencyScope,
    MarketItem,
    MarketSearchResult,
    MarketSearchSpec,
    Template,
)

T = TypeVar("T")


class MarketplaceTransaction(Protocol):
    """Writes participating in one production database transaction.

    Implementations must persist aggregates, idempotency receipts and audit events
    atomically. A raised exception must leave no partial state.
    """

    def find_template(self, workspace_id: str, template_id: str) -> Template | None: ...

    def find_template_by_review(self, workspace_id: str, review_id: str) -> Template | None: ...

    def find_market_item_by_source(self, source_id: str) -> MarketItem | None: ...

    def find_market_item(self, item_id: str) -> MarketItem | None: ...

    def find_fork(self, fork_id: str) -> ForkRecord | None: ...

    def find_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None: ...

    def save_template(self, template: Template, *, expected_revision: int | None) -> None: ...

    def save_market_item(self, item: MarketItem, *, expected_revision: int | None) -> None: ...

    def save_fork(self, record: ForkRecord) -> None: ...

    def save_fork_project(self, project: ForkProjectSnapshot) -> None: ...

    def settle_market_purchase(
        self, *, buyer_workspace_id: str, tenant_id: str, item: MarketItem,
        target_project_id: str, fork_id: str, request_id: str, occurred_at: datetime,
    ) -> None: ...

    def refund_market_purchase(
        self, *, purchase_id: str, tenant_id: str, request_id: str,
        reason: str, occurred_at: datetime,
    ) -> None: ...

    def find_idempotency(self, scope: IdempotencyScope) -> IdempotencyRecord | None: ...

    def save_idempotency(self, record: IdempotencyRecord) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...


class MarketplaceRepository(Protocol):
    """Replaceable production persistence boundary for the marketplace domain.

    ``search_market`` must filter stored rows by publication state and rights scope,
    apply all filters, sort by ``(published_at, id)`` descending and return at
    most ``spec.limit`` rows. The total is calculated before cursor slicing.
    """

    def atomic(self, operation: Callable[[MarketplaceTransaction], T]) -> T: ...

    def get_template(self, workspace_id: str, template_id: str) -> Template | None: ...

    def list_templates(self, workspace_id: str) -> tuple[Template, ...]: ...

    def search_market(self, spec: MarketSearchSpec) -> MarketSearchResult: ...

    def get_fork(self, workspace_id: str, fork_id: str) -> ForkRecord | None: ...

    def list_forks(self, workspace_id: str) -> tuple[ForkRecord, ...]: ...

    def get_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None: ...

    def list_audit_events(self, workspace_id: str, *, subject_id: str | None = None) -> tuple[AuditEvent, ...]: ...
