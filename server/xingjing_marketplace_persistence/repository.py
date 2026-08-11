from __future__ import annotations

# ruff: noqa: E701, E702
import json
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from server.xingjing_marketplace.domain import (
    AuditEvent,
    ForkProjectSnapshot,
    ForkRecord,
    IdempotencyRecord,
    IdempotencyScope,
    LineageNode,
    MarketItem,
    MarketSearchResult,
    MarketSearchSpec,
    MarketSourceKind,
    PublicationState,
    RevenueRuleVersion,
    RevenueShare,
    ReviewState,
    RightsPolicyVersion,
    RightsRecord,
    RightsScope,
    SourceSnapshot,
    Template,
    TemplateKind,
    TemplateReview,
    TemplateVersion,
    VersionConflict,
)
from server.xingjing_marketplace.ports import MarketplaceTransaction

T = TypeVar("T")


class Base(DeclarativeBase):
    pass


metadata = Base.metadata


class TemplateRow(Base):
    __tablename__ = "xingjing_marketplace_templates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class TemplateVersionRow(Base):
    __tablename__ = "xingjing_marketplace_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "workspace_id", "number", name="uq_xj_market_template_version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class ReviewRow(Base):
    __tablename__ = "xingjing_marketplace_reviews"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class AggregateRow(Base):
    __tablename__ = "xingjing_marketplace_aggregates"
    __table_args__ = (UniqueConstraint("kind", "workspace_id", "id", name="uq_xj_market_aggregate"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class IdempotencyRow(Base):
    __tablename__ = "xingjing_marketplace_idempotency"
    __table_args__ = (
        UniqueConstraint("workspace_id", "actor_id", "operation", "key", name="uq_xj_market_idempotency"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRow(Base):
    __tablename__ = "xingjing_marketplace_audit"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


def _dump(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str) -> dict[str, object]:
    payload = json.loads(value)
    assert isinstance(payload, dict)
    return payload


def _dt(value: object | None) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value is not None else None


def _rights(data: dict[str, object]) -> RightsPolicyVersion:
    return RightsPolicyVersion(
        str(data["id"]),
        RightsScope(str(data["scope"])),
        bool(data["commercial_use"]),
        bool(data["attribution_required"]),
        tuple(data["inheritable_scopes"]),
        tuple(data["allowed_workspace_ids"]),
        bool(data["allow_fork"]),
        _dt(data["created_at"]),
    )  # type: ignore[arg-type]


def _revenue(data: dict[str, object]) -> RevenueRuleVersion:
    return RevenueRuleVersion(
        str(data["id"]),
        tuple(RevenueShare(str(x["beneficiary_role"]), int(x["basis_points"])) for x in data["shares"]),
        _dt(data["created_at"]),
    )  # type: ignore[index,arg-type]


def _version(data: dict[str, object]) -> TemplateVersion:
    return TemplateVersion(
        str(data["id"]),
        str(data["template_id"]),
        str(data["workspace_id"]),
        int(data["number"]),
        _dump(data["content"]),
        str(data["content_digest"]),
        int(data["price_minor"]),
        _rights(data["rights"]),
        _revenue(data["revenue_rule"]),
        str(data["created_by"]),
        _dt(data["created_at"]),
    )  # type: ignore[arg-type]


def _review(data: dict[str, object]) -> TemplateReview:
    return TemplateReview(
        str(data["id"]),
        str(data["template_id"]),
        str(data["template_version_id"]),
        str(data["workspace_id"]),
        ReviewState(str(data["status"])),
        str(data["statement"]),
        str(data["submitted_by"]),
        _dt(data["submitted_at"]),
        data.get("decided_by") and str(data["decided_by"]),
        _dt(data.get("decided_at")),
        data.get("decision_reason") and str(data["decision_reason"]),
    )  # type: ignore[arg-type]


def _template(row: TemplateRow, versions: list[TemplateVersion], reviews: list[TemplateReview]) -> Template:
    data = _load(row.payload)
    return Template(
        row.id,
        row.workspace_id,
        str(data["owner_id"]),
        str(data["title"]),
        TemplateKind(str(data["kind"])),
        tuple(data["tags"]),
        row.revision,
        ReviewState(str(data["review_state"])),
        PublicationState(str(data["publication_state"])),
        str(data["latest_version_id"]),
        data.get("published_version_id") and str(data["published_version_id"]),
        data.get("market_item_id") and str(data["market_item_id"]),
        tuple(sorted(versions, key=lambda item: item.number)),
        tuple(reviews),
        _dt(data["created_at"]),
        _dt(data["updated_at"]),
    )  # type: ignore[arg-type]


def _market(data: dict[str, object]) -> MarketItem:
    return MarketItem(
        str(data["id"]),
        MarketSourceKind(str(data["source_kind"])),
        str(data["source_id"]),
        str(data["source_workspace_id"]),
        str(data["source_version_id"]),
        str(data["title"]),
        TemplateKind(str(data["template_kind"])) if data.get("template_kind") else None,
        str(data["author_id"]),
        tuple(data["tags"]),
        int(data["price_minor"]),
        _rights(data["rights"]),
        _revenue(data["revenue_rule"]),
        _dump(data.get("source_snapshot", {})),
        str(data["source_snapshot_digest"]),
        PublicationState(str(data["publication_state"])),
        int(data["revision"]),
        _dt(data["published_at"]),
        _dt(data.get("withdrawn_at")),
        data.get("source_fork_id") and str(data["source_fork_id"]),
    )  # type: ignore[arg-type]


def _fork(data: dict[str, object]) -> ForkRecord:
    source = data["source_snapshot"]
    snapshot = SourceSnapshot(
        str(source["id"]),
        str(source["market_item_id"]),
        MarketSourceKind(str(source["source_kind"])),
        str(source["source_id"]),
        str(source["source_workspace_id"]),
        str(source["source_version_id"]),
        str(source["title"]),
        _dump(source["content"]),
        str(source["content_digest"]),
        _dt(source["captured_at"]),
    )
    rights = data["rights_record"]
    record = RightsRecord(
        str(rights["id"]),
        str(rights["workspace_id"]),
        str(rights["source_policy_version_id"]),
        tuple(rights["granted_scopes"]),
        bool(rights["commercial_use"]),
        bool(rights["attribution_required"]),
        str(rights["rights_holder_id"]),
        _dt(rights["granted_at"]),
    )
    lineage = tuple(
        LineageNode(
            str(node["fork_id"]),
            str(node["workspace_id"]),
            str(node["target_project_id"]),
            MarketSourceKind(str(node["source_kind"])),
            str(node["source_id"]),
            str(node["source_version_id"]),
            _dt(node["created_at"]),
        )
        for node in data["lineage"]
    )
    return ForkRecord(
        str(data["id"]),
        str(data["workspace_id"]),
        str(data["target_project_id"]),
        snapshot,
        record,
        _revenue(data["revenue_rule"]),
        data.get("parent_fork_id") and str(data["parent_fork_id"]),
        tuple(data["ancestor_fork_ids"]),
        lineage,
        str(data["created_by"]),
        _dt(data["created_at"]),
    )


class _Transaction:
    def __init__(self, session: Session):
        self.s = session

    def find_template(self, workspace_id: str, template_id: str) -> Template | None:
        row = self.s.get(TemplateRow, {"id": template_id, "workspace_id": workspace_id})
        if row is None:
            return None
        versions = [
            _version(_load(x.payload))
            for x in self.s.scalars(
                select(TemplateVersionRow).where(
                    TemplateVersionRow.template_id == template_id, TemplateVersionRow.workspace_id == workspace_id
                )
            )
        ]
        reviews = [
            _review(_load(x.payload))
            for x in self.s.scalars(
                select(ReviewRow).where(ReviewRow.template_id == template_id, ReviewRow.workspace_id == workspace_id)
            )
        ]
        return _template(row, versions, reviews)

    def find_template_by_review(self, workspace_id: str, review_id: str) -> Template | None:
        row = self.s.get(ReviewRow, review_id)
        return self.find_template(workspace_id, row.template_id) if row and row.workspace_id == workspace_id else None

    def _aggregate(self, kind: str, item_id: str) -> AggregateRow | None:
        return self.s.get(AggregateRow, {"id": item_id, "kind": kind})

    def find_market_item_by_source(self, source_id: str) -> MarketItem | None:
        return next(
            (
                (_market(_load(row.payload)))
                for row in self.s.scalars(select(AggregateRow).where(AggregateRow.kind == "market"))
                if _load(row.payload)["source_id"] == source_id
            ),
            None,
        )

    def find_market_item(self, item_id: str) -> MarketItem | None:
        row = self._aggregate("market", item_id)
        return _market(_load(row.payload)) if row else None

    def find_fork(self, fork_id: str) -> ForkRecord | None:
        row = self._aggregate("fork", fork_id)
        return _fork(_load(row.payload)) if row else None

    def find_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None:
        row = self.s.get(AggregateRow, {"id": project_id, "kind": "project"})
        if row is None or row.workspace_id != workspace_id:
            return None
        data = _load(row.payload)
        return ForkProjectSnapshot(
            row.id,
            str(data["version_id"]),
            row.workspace_id,
            str(data["name"]),
            row.revision or 1,
            str(data["source_snapshot_id"]),
            _dump(data["content"]),
            str(data["content_digest"]),
            str(data["created_by"]),
            _dt(data["created_at"]),
        )  # type: ignore[arg-type]

    def save_template(self, template: Template, *, expected_revision: int | None) -> None:
        row = self.s.get(TemplateRow, {"id": template.id, "workspace_id": template.workspace_id})
        data = template.to_dict()
        data.pop("versions")
        data.pop("reviews")
        if expected_revision is None:
            if row is not None:
                raise VersionConflict(template.id)
            self.s.add(
                TemplateRow(
                    id=template.id, workspace_id=template.workspace_id, revision=template.revision, payload=_dump(data)
                )
            )
        else:
            result = self.s.execute(
                update(TemplateRow)
                .where(
                    TemplateRow.id == template.id,
                    TemplateRow.workspace_id == template.workspace_id,
                    TemplateRow.revision == expected_revision,
                )
                .values(revision=template.revision, payload=_dump(data))
            )
            if result.rowcount != 1:
                raise VersionConflict(template.id)
            self.s.execute(
                delete(TemplateVersionRow).where(
                    TemplateVersionRow.template_id == template.id,
                    TemplateVersionRow.workspace_id == template.workspace_id,
                )
            )
            self.s.execute(
                delete(ReviewRow).where(
                    ReviewRow.template_id == template.id, ReviewRow.workspace_id == template.workspace_id
                )
            )
        self.s.add_all(
            [
                TemplateVersionRow(
                    id=v.id,
                    template_id=template.id,
                    workspace_id=template.workspace_id,
                    number=v.number,
                    payload=_dump(v.to_dict()),
                )
                for v in template.versions
            ]
            + [
                ReviewRow(
                    id=r.id, template_id=template.id, workspace_id=template.workspace_id, payload=_dump(r.to_dict())
                )
                for r in template.reviews
            ]
        )
        self.s.flush()

    def save_market_item(self, item: MarketItem, *, expected_revision: int | None) -> None:
        payload = item.to_dict()
        payload["source_snapshot"] = json.loads(item.source_snapshot_json)
        self._save_aggregate(
            "market", item.id, item.source_workspace_id, item.revision, item.published_at, payload, expected_revision
        )

    def _save_aggregate(
        self,
        kind: str,
        item_id: str,
        workspace_id: str,
        revision: int | None,
        created_at: datetime | None,
        payload: dict[str, object],
        expected: int | None,
    ) -> None:
        if expected is None:
            self.s.add(
                AggregateRow(
                    id=item_id,
                    kind=kind,
                    workspace_id=workspace_id,
                    revision=revision,
                    created_at=created_at,
                    payload=_dump(payload),
                )
            )
        else:
            result = self.s.execute(
                update(AggregateRow)
                .where(AggregateRow.id == item_id, AggregateRow.kind == kind, AggregateRow.revision == expected)
                .values(revision=revision, payload=_dump(payload))
            )
            if result.rowcount != 1:
                raise VersionConflict(item_id)
        self.s.flush()

    def save_fork(self, record: ForkRecord) -> None:
        self._save_aggregate("fork", record.id, record.workspace_id, None, record.created_at, record.to_dict(), None)

    def save_fork_project(self, project: ForkProjectSnapshot) -> None:
        self._save_aggregate(
            "project", project.id, project.workspace_id, project.revision, project.created_at, project.to_dict(), None
        )

    def find_idempotency(self, scope: IdempotencyScope) -> IdempotencyRecord | None:
        row = self.s.scalar(
            select(IdempotencyRow).where(
                IdempotencyRow.workspace_id == scope.workspace_id,
                IdempotencyRow.actor_id == scope.actor_id,
                IdempotencyRow.operation == scope.operation,
                IdempotencyRow.key == scope.key,
            )
        )
        return IdempotencyRecord(scope, row.fingerprint, row.receipt_json, row.created_at) if row else None

    def save_idempotency(self, record: IdempotencyRecord) -> None:
        self.s.add(
            IdempotencyRow(
                workspace_id=record.scope.workspace_id,
                actor_id=record.scope.actor_id,
                operation=record.scope.operation,
                key=record.scope.key,
                fingerprint=record.request_fingerprint,
                receipt_json=record.receipt_json,
                created_at=record.created_at,
            )
        )
        self.s.flush()

    def append_audit(self, event: AuditEvent) -> None:
        self.s.add(
            AuditRow(
                id=event.id,
                workspace_id=event.workspace_id,
                subject_id=event.subject_id,
                occurred_at=event.occurred_at,
                payload=_dump(event.to_dict()),
            )
        )


class MarketplaceRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def atomic(self, operation: Callable[[MarketplaceTransaction], T]) -> T:
        with self._sessions.begin() as session:
            return operation(_Transaction(session))

    def get_template(self, workspace_id: str, template_id: str) -> Template | None:
        with self._sessions() as s:
            return _Transaction(s).find_template(workspace_id, template_id)

    def search_market(self, spec: MarketSearchSpec) -> MarketSearchResult:
        with self._sessions() as s:
            items = [
                item
                for item in (
                    _market(_load(row.payload))
                    for row in s.scalars(select(AggregateRow).where(AggregateRow.kind == "market"))
                )
                if item.publication_state is PublicationState.PUBLISHED and item.visible_to(spec.viewer_workspace_id)
            ]
            if spec.search:
                items = [
                    x
                    for x in items
                    if spec.search.casefold() in x.title.casefold()
                    or spec.search.casefold() in x.id.casefold()
                    or any(spec.search.casefold() in tag.casefold() for tag in x.tags)
                ]
            if spec.kinds:
                items = [x for x in items if x.template_kind in spec.kinds]
            if spec.tags:
                items = [x for x in items if spec.tags <= frozenset(x.tags)]
            if spec.commercial_use is not None:
                items = [x for x in items if x.rights.commercial_use is spec.commercial_use]
            if spec.minimum_price_minor is not None:
                items = [x for x in items if x.price_minor >= spec.minimum_price_minor]
            if spec.maximum_price_minor is not None:
                items = [x for x in items if x.price_minor <= spec.maximum_price_minor]
            items.sort(key=lambda x: (x.published_at, x.id), reverse=True)
            total = len(items)
            if spec.after:
                items = [x for x in items if (x.published_at, x.id) < spec.after]
            return MarketSearchResult(tuple(items[: spec.limit]), total)

    def get_fork(self, workspace_id: str, fork_id: str) -> ForkRecord | None:
        with self._sessions() as s:
            value = _Transaction(s).find_fork(fork_id)
            return value if value and value.workspace_id == workspace_id else None

    def list_forks(self, workspace_id: str) -> tuple[ForkRecord, ...]:
        with self._sessions() as s:
            values = [
                _fork(_load(row.payload))
                for row in s.scalars(
                    select(AggregateRow).where(AggregateRow.kind == "fork", AggregateRow.workspace_id == workspace_id)
                )
            ]
            return tuple(sorted(values, key=lambda x: (x.created_at, x.id), reverse=True))

    def get_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None:
        with self._sessions() as s:
            return _Transaction(s).find_fork_project(workspace_id, project_id)

    def list_audit_events(self, workspace_id: str, *, subject_id: str | None = None) -> tuple[AuditEvent, ...]:
        with self._sessions() as s:
            q = select(AuditRow).where(AuditRow.workspace_id == workspace_id)
            if subject_id:
                q = q.where(AuditRow.subject_id == subject_id)
            rows = s.scalars(q.order_by(AuditRow.occurred_at, AuditRow.id)).all()
            return tuple(
                AuditEvent(
                    str(data["id"]),
                    int(data["schema_version"]),
                    str(data["workspace_id"]),
                    str(data["actor_id"]),
                    str(data["request_id"]),
                    str(data["subject_type"]),
                    str(data["subject_id"]),
                    str(data["action"]),
                    _dump(data["before"]),
                    _dump(data["after"]),
                    str(data["result"]),
                    _dt(data["occurred_at"]),
                )
                for data in (_load(row.payload) for row in rows)
            )  # type: ignore[arg-type]
