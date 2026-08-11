from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from server.xingjing_marketplace import AuditEvent, MarketplaceTransaction, Template, VersionConflict
from server.xingjing_marketplace.domain import (
    ForkProjectSnapshot,
    ForkRecord,
    IdempotencyRecord,
    IdempotencyScope,
    MarketItem,
    MarketSearchResult,
    MarketSearchSpec,
    PublicationState,
)

T = TypeVar("T")


class InjectedPersistenceFailure(RuntimeError):
    pass


@dataclass(slots=True)
class _State:
    templates: dict[tuple[str, str], Template] = field(default_factory=dict)
    market_items: dict[str, MarketItem] = field(default_factory=dict)
    forks: dict[str, ForkRecord] = field(default_factory=dict)
    fork_projects: dict[tuple[str, str], ForkProjectSnapshot] = field(default_factory=dict)
    idempotency: dict[IdempotencyScope, IdempotencyRecord] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)

    def clone(self) -> _State:
        return _State(
            dict(self.templates),
            dict(self.market_items),
            dict(self.forks),
            dict(self.fork_projects),
            dict(self.idempotency),
            list(self.audits),
        )


class _Transaction:
    def __init__(self, state: _State, fail: Callable[[str], None]) -> None:
        self.state = state
        self._fail = fail

    def find_template(self, workspace_id: str, template_id: str) -> Template | None:
        return self.state.templates.get((workspace_id, template_id))

    def find_template_by_review(self, workspace_id: str, review_id: str) -> Template | None:
        return next(
            (
                template
                for (tenant_id, _), template in self.state.templates.items()
                if tenant_id == workspace_id and any(review.id == review_id for review in template.reviews)
            ),
            None,
        )

    def find_market_item_by_source(self, source_id: str) -> MarketItem | None:
        return next((item for item in self.state.market_items.values() if item.source_id == source_id), None)

    def find_market_item(self, item_id: str) -> MarketItem | None:
        return self.state.market_items.get(item_id)

    def find_fork(self, fork_id: str) -> ForkRecord | None:
        return self.state.forks.get(fork_id)

    def find_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None:
        return self.state.fork_projects.get((workspace_id, project_id))

    def save_template(self, template: Template, *, expected_revision: int | None) -> None:
        key = (template.workspace_id, template.id)
        current = self.state.templates.get(key)
        if expected_revision is None:
            if current is not None:
                raise VersionConflict(template.id)
        elif current is None or current.revision != expected_revision:
            raise VersionConflict(template.id)
        self.state.templates[key] = template

    def save_market_item(self, item: MarketItem, *, expected_revision: int | None) -> None:
        current = self.state.market_items.get(item.id)
        if expected_revision is None:
            if current is not None:
                raise VersionConflict(item.id)
        elif current is None or current.revision != expected_revision:
            raise VersionConflict(item.id)
        self.state.market_items[item.id] = item

    def save_fork(self, record: ForkRecord) -> None:
        if record.id in self.state.forks:
            raise VersionConflict(record.id)
        self.state.forks[record.id] = record

    def save_fork_project(self, project: ForkProjectSnapshot) -> None:
        key = (project.workspace_id, project.id)
        if key in self.state.fork_projects:
            raise VersionConflict(project.id)
        self.state.fork_projects[key] = project

    def find_idempotency(self, scope: IdempotencyScope) -> IdempotencyRecord | None:
        return self.state.idempotency.get(scope)

    def save_idempotency(self, record: IdempotencyRecord) -> None:
        self.state.idempotency[record.scope] = record

    def append_audit(self, event: AuditEvent) -> None:
        self._fail("append_audit")
        self.state.audits.append(event)


class FakeMarketplaceRepository:
    """Copy-on-write transaction fake used only by marketplace domain tests."""

    def __init__(self) -> None:
        self._state = _State()
        self._failure_point: str | None = None

    def fail_once_on(self, operation: str) -> None:
        self._failure_point = operation

    def _fail(self, operation: str) -> None:
        if self._failure_point == operation:
            self._failure_point = None
            raise InjectedPersistenceFailure(operation)

    def atomic(self, operation: Callable[[MarketplaceTransaction], T]) -> T:
        working = self._state.clone()
        result = operation(_Transaction(working, self._fail))
        self._state = working
        return result

    def get_template(self, workspace_id: str, template_id: str) -> Template | None:
        return self._state.templates.get((workspace_id, template_id))

    def search_market(self, spec: MarketSearchSpec) -> MarketSearchResult:
        items = [
            item
            for item in self._state.market_items.values()
            if item.publication_state is PublicationState.PUBLISHED and item.visible_to(spec.viewer_workspace_id)
        ]
        if spec.search:
            needle = spec.search.casefold()
            items = [
                item
                for item in items
                if needle in item.id.casefold()
                or needle in item.title.casefold()
                or any(needle in tag.casefold() for tag in item.tags)
            ]
        if spec.kinds:
            items = [item for item in items if item.template_kind in spec.kinds]
        if spec.tags:
            items = [item for item in items if spec.tags <= frozenset(item.tags)]
        if spec.commercial_use is not None:
            items = [item for item in items if item.rights.commercial_use is spec.commercial_use]
        if spec.minimum_price_minor is not None:
            items = [item for item in items if item.price_minor >= spec.minimum_price_minor]
        if spec.maximum_price_minor is not None:
            items = [item for item in items if item.price_minor <= spec.maximum_price_minor]
        items.sort(key=lambda item: (item.published_at, item.id), reverse=True)
        total = len(items)
        if spec.after is not None:
            items = [item for item in items if (item.published_at, item.id) < spec.after]
        return MarketSearchResult(tuple(items[: spec.limit]), total)

    def get_fork(self, workspace_id: str, fork_id: str) -> ForkRecord | None:
        record = self._state.forks.get(fork_id)
        return record if record is not None and record.workspace_id == workspace_id else None

    def list_forks(self, workspace_id: str) -> tuple[ForkRecord, ...]:
        records = [record for record in self._state.forks.values() if record.workspace_id == workspace_id]
        records.sort(key=lambda record: (record.created_at, record.id), reverse=True)
        return tuple(records)

    def get_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None:
        return self._state.fork_projects.get((workspace_id, project_id))

    def list_audit_events(self, workspace_id: str, *, subject_id: str | None = None) -> tuple[AuditEvent, ...]:
        return tuple(
            event
            for event in self._state.audits
            if event.workspace_id == workspace_id and (subject_id is None or event.subject_id == subject_id)
        )
