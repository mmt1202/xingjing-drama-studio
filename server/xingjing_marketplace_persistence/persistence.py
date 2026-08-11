"""MarketplaceRepository 的 SQLAlchemy/PostgreSQL 生产实现。"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from datetime import UTC, datetime
from typing import Protocol, TypeVar, cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    delete,
    event,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from server.xingjing_generation_persistence.repository import (
    GenerationBillingAccountRow,
    GenerationBillingJournalRow,
)
from server.xingjing_marketplace.domain import (
    AuditEvent,
    CommandReceipt,
    ForkProjectSnapshot,
    ForkRecord,
    IdempotencyConflict,
    IdempotencyRecord,
    IdempotencyScope,
    InvalidInput,
    LineageNode,
    MarketItem,
    MarketPurchaseNotFound,
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
from server.xingjing_platform_persistence.persistence import OutboxRow, ProjectRow

T = TypeVar("T")


class _RowCountResult(Protocol):
    rowcount: int


class Base(DeclarativeBase):
    """独立 metadata；主线必须通过 Alembic 显式接入，运行时不隐式建表。"""


class TemplateRow(Base):
    __tablename__ = "xingjing_marketplace_templates"
    __table_args__ = (Index("ix_xj_marketplace_template_workspace", "workspace_id", "updated_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    publication_state: Mapped[str] = mapped_column(String(32), nullable=False)
    latest_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    published_version_id: Mapped[str | None] = mapped_column(String(36))
    market_item_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TemplateVersionRow(Base):

    __tablename__ = "xingjing_marketplace_template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "number", name="uq_xj_marketplace_template_version_number"),
        Index("ix_xj_marketplace_template_version_workspace", "workspace_id", "template_id", "number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    rights_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    rights_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rights_commercial_use: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_inheritable_scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    rights_allowed_workspace_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    rights_allow_fork: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revenue_rule_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    revenue_shares_json: Mapped[str] = mapped_column(Text, nullable=False)
    revenue_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TemplateReviewRow(Base):
    __tablename__ = "xingjing_marketplace_template_reviews"
    __table_args__ = (Index("ix_xj_marketplace_review_workspace", "workspace_id", "template_id", "submitted_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    template_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class TemplateStateHistoryRow(Base):
    __tablename__ = "xingjing_marketplace_template_state_history"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "template_id",
            "revision",
            name="uq_xj_marketplace_template_state_revision",
        ),
        Index("ix_xj_marketplace_template_state_time", "workspace_id", "template_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    publication_state: Mapped[str] = mapped_column(String(32), nullable=False)
    latest_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    published_version_id: Mapped[str | None] = mapped_column(String(36))
    market_item_id: Mapped[str | None] = mapped_column(String(36))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketItemRow(Base):
    __tablename__ = "xingjing_marketplace_items"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_xj_marketplace_item_source"),
        Index("ix_xj_marketplace_item_publication", "publication_state", "published_at", "id"),
        Index("ix_xj_marketplace_item_workspace", "source_workspace_id", "published_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    template_kind: Mapped[str | None] = mapped_column(String(32))
    author_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    rights_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    rights_commercial_use: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_inheritable_scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    rights_allowed_workspace_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    rights_allow_fork: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revenue_rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revenue_shares_json: Mapped[str] = mapped_column(Text, nullable=False)
    revenue_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_fork_id: Mapped[str | None] = mapped_column(String(36))


class MarketItemTagRow(Base):
    __tablename__ = "xingjing_marketplace_item_tags"
    __table_args__ = (
        UniqueConstraint("item_id", "tag", name="uq_xj_marketplace_item_tag"),
        Index("ix_xj_marketplace_item_tag_lookup", "tag", "item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tag: Mapped[str] = mapped_column(String(128), nullable=False)


class MarketItemAllowedWorkspaceRow(Base):
    __tablename__ = "xingjing_marketplace_item_allowed_workspaces"
    __table_args__ = (
        UniqueConstraint("item_id", "workspace_id", name="uq_xj_marketplace_item_allowed_workspace"),
        Index("ix_xj_marketplace_allowed_workspace_lookup", "workspace_id", "item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)


class MarketItemRevisionRow(Base):
    __tablename__ = "xingjing_marketplace_item_revision_history"
    __table_args__ = (
        UniqueConstraint("item_id", "revision", name="uq_xj_marketplace_item_revision"),
        Index("ix_xj_marketplace_item_revision_time", "item_id", "published_at", "revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rights_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revenue_rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    publication_state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)


class ForkProjectRow(Base):
    __tablename__ = "xingjing_marketplace_fork_projects"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_xj_marketplace_fork_project_version"),
        Index("ix_xj_marketplace_fork_project_workspace", "workspace_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForkRow(Base):
    __tablename__ = "xingjing_marketplace_forks"
    __table_args__ = (
        UniqueConstraint("target_project_id", name="uq_xj_marketplace_fork_target_project"),
        Index("ix_xj_marketplace_fork_workspace", "workspace_id", "created_at", "id"),
        Index("ix_xj_marketplace_fork_parent", "parent_fork_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_fork_id: Mapped[str | None] = mapped_column(String(36))
    ancestor_fork_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    revenue_rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revenue_shares_json: Mapped[str] = mapped_column(Text, nullable=False)
    revenue_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForkSourceSnapshotRow(Base):
    __tablename__ = "xingjing_marketplace_fork_source_snapshots"
    __table_args__ = (
        UniqueConstraint("fork_id", name="uq_xj_marketplace_fork_source_snapshot"),
        Index("ix_xj_marketplace_source_snapshot_item", "market_item_id", "captured_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fork_id: Mapped[str] = mapped_column(String(36), nullable=False)
    market_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForkRightsRecordRow(Base):
    __tablename__ = "xingjing_marketplace_fork_rights_records"
    __table_args__ = (
        UniqueConstraint("fork_id", name="uq_xj_marketplace_fork_rights_record"),
        Index("ix_xj_marketplace_rights_record_workspace", "workspace_id", "granted_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fork_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    granted_scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    commercial_use: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_holder_id: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForkLineageRow(Base):
    __tablename__ = "xingjing_marketplace_fork_lineage"
    __table_args__ = (
        UniqueConstraint("fork_id", "position", name="uq_xj_marketplace_fork_lineage_position"),
        UniqueConstraint("fork_id", "ancestor_fork_id", name="uq_xj_marketplace_fork_lineage_ancestor"),
        Index("ix_xj_marketplace_lineage_ancestor", "ancestor_fork_id", "fork_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fork_id: Mapped[str] = mapped_column(String(36), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    ancestor_fork_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketPurchaseRow(Base):
    __tablename__ = "xingjing_marketplace_purchases"
    __table_args__ = (
        UniqueConstraint("buyer_workspace_id", "fork_id", name="uq_xj_marketplace_purchase_fork"),
        Index("ix_xj_marketplace_purchase_buyer_time", "buyer_workspace_id", "created_at", "id"),
        CheckConstraint("amount_minor >= 0", name="ck_xj_marketplace_purchase_amount"),
        CheckConstraint("status IN ('settled', 'refunded')", name="ck_xj_marketplace_purchase_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    buyer_workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seller_workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    market_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fork_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revenue_rule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    distributions_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRow(Base):
    __tablename__ = "xingjing_marketplace_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "actor_id",
            "operation",
            "idempotency_key",
            name="uq_xj_marketplace_idempotency_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "xingjing_marketplace_audit_events"
    __table_args__ = (
        Index("ix_xj_marketplace_audit_workspace", "workspace_id", "occurred_at", "id"),
        Index("ix_xj_marketplace_audit_subject", "workspace_id", "subject_id", "occurred_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    before_json: Mapped[str] = mapped_column(Text, nullable=False)
    after_json: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _json_tuple(value: str) -> tuple[str, ...]:
    import json

    payload = json.loads(value)
    if not isinstance(payload, list):
        raise RuntimeError("stored list payload is invalid")
    return tuple(str(item) for item in payload)


def _shares(value: str) -> tuple[RevenueShare, ...]:
    import json

    payload = json.loads(value)
    if not isinstance(payload, list):
        raise RuntimeError("stored revenue shares are invalid")
    return tuple(RevenueShare(str(item["beneficiary_role"]), int(item["basis_points"])) for item in payload)


def _version(row: TemplateVersionRow) -> TemplateVersion:
    rights_created_at = _as_utc(row.rights_created_at)
    revenue_created_at = _as_utc(row.revenue_created_at)
    created_at = _as_utc(row.created_at)
    assert rights_created_at is not None and revenue_created_at is not None and created_at is not None
    return TemplateVersion(
        id=row.id,
        template_id=row.template_id,
        workspace_id=row.workspace_id,
        number=row.number,
        content_json=row.content_json,
        content_digest=row.content_digest,
        price_minor=row.price_minor,
        rights=RightsPolicyVersion(
            id=row.rights_id,
            scope=RightsScope(row.rights_scope),
            commercial_use=row.rights_commercial_use,
            attribution_required=row.rights_attribution_required,
            inheritable_scopes=_json_tuple(row.rights_inheritable_scopes_json),
            allowed_workspace_ids=_json_tuple(row.rights_allowed_workspace_ids_json),
            allow_fork=row.rights_allow_fork,
            created_at=rights_created_at,
        ),
        revenue_rule=RevenueRuleVersion(
            id=row.revenue_rule_id,
            shares=_shares(row.revenue_shares_json),
            created_at=revenue_created_at,
        ),
        created_by=row.created_by,
        created_at=created_at,
    )


def _review(row: TemplateReviewRow) -> TemplateReview:
    submitted_at = _as_utc(row.submitted_at)
    assert submitted_at is not None
    return TemplateReview(
        id=row.id,
        template_id=row.template_id,
        template_version_id=row.template_version_id,
        workspace_id=row.workspace_id,
        status=ReviewState(row.status),
        statement=row.statement,
        submitted_by=row.submitted_by,
        submitted_at=submitted_at,
        decided_by=row.decided_by,
        decided_at=_as_utc(row.decided_at),
        decision_reason=row.decision_reason,
    )


def _template(session: Session, row: TemplateRow) -> Template:
    versions = session.scalars(
        select(TemplateVersionRow)
        .where(
            TemplateVersionRow.workspace_id == row.workspace_id,
            TemplateVersionRow.template_id == row.id,
        )
        .order_by(TemplateVersionRow.number, TemplateVersionRow.id)
    ).all()
    reviews = session.scalars(
        select(TemplateReviewRow)
        .where(
            TemplateReviewRow.workspace_id == row.workspace_id,
            TemplateReviewRow.template_id == row.id,
        )
        .order_by(TemplateReviewRow.submitted_at, TemplateReviewRow.id)
    ).all()
    created_at = _as_utc(row.created_at)
    updated_at = _as_utc(row.updated_at)
    assert created_at is not None and updated_at is not None
    return Template(
        id=row.id,
        workspace_id=row.workspace_id,
        owner_id=row.owner_id,
        title=row.title,
        kind=TemplateKind(row.kind),
        tags=_json_tuple(row.tags_json),
        revision=row.revision,
        review_state=ReviewState(row.review_state),
        publication_state=PublicationState(row.publication_state),
        latest_version_id=row.latest_version_id,
        published_version_id=row.published_version_id,
        market_item_id=row.market_item_id,
        versions=tuple(_version(item) for item in versions),
        reviews=tuple(_review(item) for item in reviews),
        created_at=created_at,
        updated_at=updated_at,
    )


def _market_item(row: MarketItemRow) -> MarketItem:
    rights_created_at = _as_utc(row.rights_created_at)
    revenue_created_at = _as_utc(row.revenue_created_at)
    published_at = _as_utc(row.published_at)
    assert rights_created_at is not None and revenue_created_at is not None and published_at is not None
    return MarketItem(
        id=row.id,
        source_kind=MarketSourceKind(row.source_kind),
        source_id=row.source_id,
        source_workspace_id=row.source_workspace_id,
        source_version_id=row.source_version_id,
        title=row.title,
        template_kind=TemplateKind(row.template_kind) if row.template_kind is not None else None,
        author_id=row.author_id,
        tags=_json_tuple(row.tags_json),
        price_minor=row.price_minor,
        rights=RightsPolicyVersion(
            id=row.rights_id,
            scope=RightsScope(row.rights_scope),
            commercial_use=row.rights_commercial_use,
            attribution_required=row.rights_attribution_required,
            inheritable_scopes=_json_tuple(row.rights_inheritable_scopes_json),
            allowed_workspace_ids=_json_tuple(row.rights_allowed_workspace_ids_json),
            allow_fork=row.rights_allow_fork,
            created_at=rights_created_at,
        ),
        revenue_rule=RevenueRuleVersion(
            id=row.revenue_rule_id,
            shares=_shares(row.revenue_shares_json),
            created_at=revenue_created_at,
        ),
        source_snapshot_json=row.source_snapshot_json,
        source_snapshot_digest=row.source_snapshot_digest,
        publication_state=PublicationState(row.publication_state),
        revision=row.revision,
        published_at=published_at,
        withdrawn_at=_as_utc(row.withdrawn_at),
        source_fork_id=row.source_fork_id,
    )


def _fork_project(row: ForkProjectRow) -> ForkProjectSnapshot:
    created_at = _as_utc(row.created_at)
    assert created_at is not None
    return ForkProjectSnapshot(
        id=row.id,
        version_id=row.version_id,
        workspace_id=row.workspace_id,
        name=row.name,
        revision=row.revision,
        source_snapshot_id=row.source_snapshot_id,
        content_json=row.content_json,
        content_digest=row.content_digest,
        created_by=row.created_by,
        created_at=created_at,
    )


def _fork_record(session: Session, row: ForkRow) -> ForkRecord:
    source = session.scalar(select(ForkSourceSnapshotRow).where(ForkSourceSnapshotRow.fork_id == row.id))
    rights = session.scalar(select(ForkRightsRecordRow).where(ForkRightsRecordRow.fork_id == row.id))
    if source is None or rights is None:
        raise RuntimeError(f"fork {row.id} is missing immutable source or rights records")
    lineage_rows = session.scalars(
        select(ForkLineageRow).where(ForkLineageRow.fork_id == row.id).order_by(ForkLineageRow.position)
    ).all()
    source_captured_at = _as_utc(source.captured_at)
    rights_granted_at = _as_utc(rights.granted_at)
    revenue_created_at = _as_utc(row.revenue_created_at)
    created_at = _as_utc(row.created_at)
    assert (
        source_captured_at is not None
        and rights_granted_at is not None
        and revenue_created_at is not None
        and created_at is not None
    )
    return ForkRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        target_project_id=row.target_project_id,
        source_snapshot=SourceSnapshot(
            id=source.id,
            market_item_id=source.market_item_id,
            source_kind=MarketSourceKind(source.source_kind),
            source_id=source.source_id,
            source_workspace_id=source.source_workspace_id,
            source_version_id=source.source_version_id,
            title=source.title,
            content_json=source.content_json,
            content_digest=source.content_digest,
            captured_at=source_captured_at,
        ),
        rights_record=RightsRecord(
            id=rights.id,
            workspace_id=rights.workspace_id,
            source_policy_version_id=rights.source_policy_version_id,
            granted_scopes=_json_tuple(rights.granted_scopes_json),
            commercial_use=rights.commercial_use,
            attribution_required=rights.attribution_required,
            rights_holder_id=rights.rights_holder_id,
            granted_at=rights_granted_at,
        ),
        revenue_rule=RevenueRuleVersion(
            id=row.revenue_rule_id,
            shares=_shares(row.revenue_shares_json),
            created_at=revenue_created_at,
        ),
        parent_fork_id=row.parent_fork_id,
        ancestor_fork_ids=_json_tuple(row.ancestor_fork_ids_json),
        lineage=tuple(
            LineageNode(
                fork_id=item.ancestor_fork_id,
                workspace_id=item.workspace_id,
                target_project_id=item.target_project_id,
                source_kind=MarketSourceKind(item.source_kind),
                source_id=item.source_id,
                source_version_id=item.source_version_id,
                created_at=_required_utc(item.created_at),
            )
            for item in lineage_rows
        ),
        created_by=row.created_by,
        created_at=created_at,
    )


def _required_utc(value: datetime) -> datetime:
    result = _as_utc(value)
    assert result is not None
    return result


class _LoopRunner:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._thread = threading.Thread(target=self._run_forever, name="marketplace-sqlalchemy", daemon=True)
        self._thread.start()
        self._started.wait()

    def _run_forever(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()
        self._loop.close()

    def run(self, awaitable: Coroutine[object, object, T]) -> T:
        future: Future[T] = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def close(self) -> None:
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


class _IdempotencyRace(RuntimeError):
    def __init__(self, record: IdempotencyRecord) -> None:
        super().__init__(record.scope.key)
        self.record = record


class _Transaction:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_template(self, workspace_id: str, template_id: str) -> Template | None:
        row = self._session.scalar(
            select(TemplateRow).where(TemplateRow.workspace_id == workspace_id, TemplateRow.id == template_id)
        )
        return None if row is None else _template(self._session, row)

    def find_template_by_review(self, workspace_id: str, review_id: str) -> Template | None:
        review = self._session.scalar(
            select(TemplateReviewRow).where(
                TemplateReviewRow.workspace_id == workspace_id,
                TemplateReviewRow.id == review_id,
            )
        )
        return None if review is None else self.find_template(workspace_id, review.template_id)

    def find_market_item_by_source(self, source_id: str) -> MarketItem | None:
        row = self._session.scalar(select(MarketItemRow).where(MarketItemRow.source_id == source_id))
        return None if row is None else _market_item(row)

    def find_market_item(self, item_id: str) -> MarketItem | None:
        row = self._session.get(MarketItemRow, item_id)
        return None if row is None else _market_item(row)

    def find_fork(self, fork_id: str) -> ForkRecord | None:
        row = self._session.get(ForkRow, fork_id)
        return None if row is None else _fork_record(self._session, row)

    def find_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None:
        row = self._session.scalar(
            select(ForkProjectRow).where(
                ForkProjectRow.workspace_id == workspace_id,
                ForkProjectRow.id == project_id,
            )
        )
        return None if row is None else _fork_project(row)

    def save_template(self, template: Template, *, expected_revision: int | None) -> None:
        try:
            if expected_revision is None:
                self._session.add(TemplateRow(**_template_values(template)))
            else:
                statement = (
                    update(TemplateRow)
                    .where(
                        TemplateRow.workspace_id == template.workspace_id,
                        TemplateRow.id == template.id,
                        TemplateRow.revision == expected_revision,
                    )
                    .values(**_template_update_values(template))
                    .execution_options(synchronize_session=False)
                )
                result = cast(_RowCountResult, self._session.execute(statement))
                if result.rowcount != 1:
                    raise VersionConflict(template.id)
            self._sync_versions(template.versions)
            self._sync_reviews(template.reviews)
            self._session.add(
                TemplateStateHistoryRow(
                    workspace_id=template.workspace_id,
                    template_id=template.id,
                    revision=template.revision,
                    review_state=template.review_state.value,
                    publication_state=template.publication_state.value,
                    latest_version_id=template.latest_version_id,
                    published_version_id=template.published_version_id,
                    market_item_id=template.market_item_id,
                    recorded_at=template.updated_at,
                )
            )
            self._session.flush()
        except IntegrityError as error:
            raise VersionConflict(template.id) from error

    def _sync_versions(self, versions: tuple[TemplateVersion, ...]) -> None:
        existing_rows = {
            row.id: row
            for row in self._session.scalars(
                select(TemplateVersionRow).where(
                    TemplateVersionRow.id.in_(tuple(version.id for version in versions)),
                )
            ).all()
        }
        for version in versions:
            existing = existing_rows.get(version.id)
            if existing is not None:
                if _version(existing) != version:
                    raise VersionConflict(version.id)
                continue
            self._session.add(
                TemplateVersionRow(
                    id=version.id,
                    template_id=version.template_id,
                    workspace_id=version.workspace_id,
                    number=version.number,
                    content_json=version.content_json,
                    content_digest=version.content_digest,
                    price_minor=version.price_minor,
                    rights_id=version.rights.id,
                    rights_scope=version.rights.scope.value,
                    rights_commercial_use=version.rights.commercial_use,
                    rights_attribution_required=version.rights.attribution_required,
                    rights_inheritable_scopes_json=_string_list(version.rights.inheritable_scopes),
                    rights_allowed_workspace_ids_json=_string_list(version.rights.allowed_workspace_ids),
                    rights_allow_fork=version.rights.allow_fork,
                    rights_created_at=version.rights.created_at,
                    revenue_rule_id=version.revenue_rule.id,
                    revenue_shares_json=_revenue_shares(version.revenue_rule.shares),
                    revenue_created_at=version.revenue_rule.created_at,
                    created_by=version.created_by,
                    created_at=version.created_at,
                )
            )

    def _sync_reviews(self, reviews: tuple[TemplateReview, ...]) -> None:
        existing_rows = {
            row.id: row
            for row in self._session.scalars(
                select(TemplateReviewRow).where(
                    TemplateReviewRow.id.in_(tuple(review.id for review in reviews)),
                )
            ).all()
        }
        for review in reviews:
            existing = existing_rows.get(review.id)
            if existing is None:
                self._session.add(
                    TemplateReviewRow(
                        id=review.id,
                        template_id=review.template_id,
                        template_version_id=review.template_version_id,
                        workspace_id=review.workspace_id,
                        status=review.status.value,
                        statement=review.statement,
                        submitted_by=review.submitted_by,
                        submitted_at=review.submitted_at,
                        decided_by=review.decided_by,
                        decided_at=review.decided_at,
                        decision_reason=review.decision_reason,
                    )
                )
                continue
            if (
                existing.template_id != review.template_id
                or existing.template_version_id != review.template_version_id
                or existing.workspace_id != review.workspace_id
                or existing.statement != review.statement
                or existing.submitted_by != review.submitted_by
                or _as_utc(existing.submitted_at) != review.submitted_at
            ):
                raise VersionConflict(review.id)
            existing.status = review.status.value
            existing.decided_by = review.decided_by
            existing.decided_at = review.decided_at
            existing.decision_reason = review.decision_reason

    def save_market_item(self, item: MarketItem, *, expected_revision: int | None) -> None:
        try:
            if expected_revision is None:
                self._session.add(MarketItemRow(**_market_item_values(item)))
            else:
                statement = (
                    update(MarketItemRow)
                    .where(MarketItemRow.id == item.id, MarketItemRow.revision == expected_revision)
                    .values(**_market_item_update_values(item))
                    .execution_options(synchronize_session=False)
                )
                result = cast(_RowCountResult, self._session.execute(statement))
                if result.rowcount != 1:
                    raise VersionConflict(item.id)
                self._session.execute(delete(MarketItemTagRow).where(MarketItemTagRow.item_id == item.id))
                self._session.execute(
                    delete(MarketItemAllowedWorkspaceRow).where(MarketItemAllowedWorkspaceRow.item_id == item.id)
                )
            self._session.add_all(MarketItemTagRow(item_id=item.id, tag=tag) for tag in item.tags)
            self._session.add_all(
                MarketItemAllowedWorkspaceRow(item_id=item.id, workspace_id=workspace_id)
                for workspace_id in item.rights.allowed_workspace_ids
            )
            self._session.add(
                MarketItemRevisionRow(
                    item_id=item.id,
                    source_version_id=item.source_version_id,
                    rights_id=item.rights.id,
                    revenue_rule_id=item.revenue_rule.id,
                    publication_state=item.publication_state.value,
                    revision=item.revision,
                    published_at=item.published_at,
                    withdrawn_at=item.withdrawn_at,
                    snapshot_json=_market_history_json(item),
                )
            )
            self._session.flush()
        except IntegrityError as error:
            raise VersionConflict(item.id) from error

    def save_fork(self, record: ForkRecord) -> None:
        try:
            self._session.add(
                ForkRow(
                    id=record.id,
                    workspace_id=record.workspace_id,
                    target_project_id=record.target_project_id,
                    parent_fork_id=record.parent_fork_id,
                    ancestor_fork_ids_json=_string_list(record.ancestor_fork_ids),
                    revenue_rule_id=record.revenue_rule.id,
                    revenue_shares_json=_revenue_shares(record.revenue_rule.shares),
                    revenue_created_at=record.revenue_rule.created_at,
                    created_by=record.created_by,
                    created_at=record.created_at,
                )
            )
            self._session.add(
                ForkSourceSnapshotRow(
                    id=record.source_snapshot.id,
                    fork_id=record.id,
                    market_item_id=record.source_snapshot.market_item_id,
                    source_kind=record.source_snapshot.source_kind.value,
                    source_id=record.source_snapshot.source_id,
                    source_workspace_id=record.source_snapshot.source_workspace_id,
                    source_version_id=record.source_snapshot.source_version_id,
                    title=record.source_snapshot.title,
                    content_json=record.source_snapshot.content_json,
                    content_digest=record.source_snapshot.content_digest,
                    captured_at=record.source_snapshot.captured_at,
                )
            )
            self._session.add(
                ForkRightsRecordRow(
                    id=record.rights_record.id,
                    fork_id=record.id,
                    workspace_id=record.rights_record.workspace_id,
                    source_policy_version_id=record.rights_record.source_policy_version_id,
                    granted_scopes_json=_string_list(record.rights_record.granted_scopes),
                    commercial_use=record.rights_record.commercial_use,
                    attribution_required=record.rights_record.attribution_required,
                    rights_holder_id=record.rights_record.rights_holder_id,
                    granted_at=record.rights_record.granted_at,
                )
            )
            self._session.add_all(
                ForkLineageRow(
                    fork_id=record.id,
                    position=position,
                    ancestor_fork_id=node.fork_id,
                    workspace_id=node.workspace_id,
                    target_project_id=node.target_project_id,
                    source_kind=node.source_kind.value,
                    source_id=node.source_id,
                    source_version_id=node.source_version_id,
                    created_at=node.created_at,
                )
                for position, node in enumerate(record.lineage)
            )
            self._session.flush()
        except IntegrityError as error:
            raise VersionConflict(record.id) from error

    def save_fork_project(self, project: ForkProjectSnapshot) -> None:
        try:
            self._session.add(
                ForkProjectRow(
                    id=project.id,
                    version_id=project.version_id,
                    workspace_id=project.workspace_id,
                    name=project.name,
                    revision=project.revision,
                    source_snapshot_id=project.source_snapshot_id,
                    content_json=project.content_json,
                    content_digest=project.content_digest,
                    created_by=project.created_by,
                    created_at=project.created_at,
                )
            )
            self._session.flush()
        except IntegrityError as error:
            raise VersionConflict(project.id) from error

    def settle_market_purchase(
        self, *, buyer_workspace_id: str, tenant_id: str, item: MarketItem,
        target_project_id: str, fork_id: str, request_id: str, occurred_at: datetime,
    ) -> None:
        existing = self._session.scalar(select(MarketPurchaseRow).where(
            MarketPurchaseRow.buyer_workspace_id == buyer_workspace_id,
            MarketPurchaseRow.fork_id == fork_id,
        ))
        if existing is not None:
            return
        project = self._session.get(ForkProjectRow, target_project_id)
        if project is None or project.workspace_id != buyer_workspace_id:
            raise InvalidInput("fork project is not available for purchase settlement")
        if not tenant_id.strip():
            raise InvalidInput("tenant context is required for a real fork project")

        amount = item.price_minor
        currency = "CNY"
        distributions: list[dict[str, object]] = []
        if amount > 0 and buyer_workspace_id != item.source_workspace_id:
            buyer = self._session.get(GenerationBillingAccountRow, buyer_workspace_id, with_for_update=True)
            if buyer is None or buyer.currency != currency or buyer.available_minor < amount:
                raise InvalidInput("MARKETPLACE_BALANCE_INSUFFICIENT")
            seller = self._session.get(GenerationBillingAccountRow, item.source_workspace_id, with_for_update=True)
            if seller is None:
                seller = GenerationBillingAccountRow(workspace_id=item.source_workspace_id, currency=currency,
                    available_minor=0, held_minor=0, spent_minor=0, version=1, updated_at=occurred_at)
                self._session.add(seller)
            author_basis_points = sum(share.basis_points for share in item.revenue_rule.shares
                                      if share.beneficiary_role in {"author", "creator", "rights_holder",
                                                                    "template_author", "community_author"})
            seller_amount = amount * author_basis_points // 10_000
            platform_amount = amount - seller_amount
            buyer.available_minor -= amount
            buyer.spent_minor += amount
            buyer.version += 1
            buyer.updated_at = occurred_at
            seller.available_minor += seller_amount
            seller.version += 1
            seller.updated_at = occurred_at
            distributions = [
                {"beneficiaryRole": "rights_holder", "workspaceId": item.source_workspace_id, "amountMinor": seller_amount},
                {"beneficiaryRole": "platform", "workspaceId": None, "amountMinor": platform_amount},
            ]
            self._session.add(GenerationBillingJournalRow(event_id=f"market-debit:{fork_id}", workspace_id=buyer_workspace_id,
                project_id=target_project_id, task_id=None, action="market_purchase", currency=currency,
                amount_minor=amount, postings=[{"account": "workspace.available", "amount_minor": -amount},
                    {"account": "marketplace.clearing", "amount_minor": amount}],
                reference=f"market:{item.id}:fork:{fork_id}", occurred_at=occurred_at))
            self._session.add(GenerationBillingJournalRow(event_id=f"market-credit:{fork_id}", workspace_id=item.source_workspace_id,
                project_id=target_project_id, task_id=None, action="market_revenue", currency=currency,
                amount_minor=seller_amount, postings=[{"account": "marketplace.clearing", "amount_minor": -seller_amount},
                    {"account": "workspace.available", "amount_minor": seller_amount}],
                reference=f"market:{item.id}:fork:{fork_id}", occurred_at=occurred_at))

        self._session.add(ProjectRow(id=target_project_id, tenant_id=tenant_id, workspace_id=buyer_workspace_id,
            idempotency_key=f"market-fork:{fork_id}", name=project.name, version=1,
            created_at=occurred_at, updated_at=occurred_at, deleted_at=None))
        self._session.add(MarketPurchaseRow(id=fork_id, tenant_id=tenant_id, buyer_workspace_id=buyer_workspace_id,
            seller_workspace_id=item.source_workspace_id, market_item_id=item.id, fork_id=fork_id,
            target_project_id=target_project_id, currency=currency, amount_minor=amount,
            revenue_rule_id=item.revenue_rule.id, distributions_json=json.dumps(distributions, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")), request_id=request_id,
            status="settled", created_at=occurred_at, refunded_at=None))
        self._session.add(OutboxRow(
            id=fork_id, tenant_id=tenant_id, workspace_id=buyer_workspace_id,
            event_type="marketplace.fork.project_created", aggregate_type="project",
            aggregate_id=target_project_id,
            payload={"forkId": fork_id, "marketItemId": item.id,
                     "sourceVersionId": item.source_version_id,
                     "sourceWorkspaceId": item.source_workspace_id,
                     "rightsPolicyId": item.rights.id,
                     "revenueRuleId": item.revenue_rule.id},
            schema_version=1, created_at=occurred_at, published_at=None,
        ))
        self._session.flush()

    def refund_market_purchase(
        self, *, purchase_id: str, tenant_id: str, request_id: str,
        reason: str, occurred_at: datetime,
    ) -> None:
        purchase = self._session.scalar(select(MarketPurchaseRow).where(
            MarketPurchaseRow.id == purchase_id,
            MarketPurchaseRow.tenant_id == tenant_id,
        ).with_for_update())
        if purchase is None:
            raise MarketPurchaseNotFound(purchase_id)
        if purchase.status == "refunded":
            return
        if purchase.status != "settled":
            raise InvalidInput("market purchase is not refundable")
        distributions = json.loads(purchase.distributions_json)
        seller_amount = sum(int(entry.get("amountMinor", 0)) for entry in distributions
                            if isinstance(entry, dict) and entry.get("workspaceId") == purchase.seller_workspace_id)
        if purchase.amount_minor > 0 and purchase.buyer_workspace_id != purchase.seller_workspace_id:
            buyer = self._session.get(GenerationBillingAccountRow, purchase.buyer_workspace_id, with_for_update=True)
            seller = self._session.get(GenerationBillingAccountRow, purchase.seller_workspace_id, with_for_update=True)
            if buyer is None or seller is None or seller.available_minor < seller_amount:
                raise InvalidInput("MARKETPLACE_REFUND_BALANCE_UNAVAILABLE")
            buyer.available_minor += purchase.amount_minor
            buyer.spent_minor -= purchase.amount_minor
            buyer.version += 1
            buyer.updated_at = occurred_at
            seller.available_minor -= seller_amount
            seller.version += 1
            seller.updated_at = occurred_at
            self._session.add(GenerationBillingJournalRow(
                event_id=f"market-refund:{purchase.id}", workspace_id=purchase.buyer_workspace_id,
                project_id=purchase.target_project_id, task_id=None, action="market_refund",
                currency=purchase.currency, amount_minor=purchase.amount_minor,
                postings=[{"account": "marketplace.clearing", "amount_minor": -purchase.amount_minor},
                          {"account": "workspace.available", "amount_minor": purchase.amount_minor}],
                reference=f"market:{purchase.market_item_id}:refund:{purchase.id}:{request_id}",
                occurred_at=occurred_at,
            ))
            self._session.add(GenerationBillingJournalRow(
                event_id=f"market-revenue-reversal:{purchase.id}", workspace_id=purchase.seller_workspace_id,
                project_id=purchase.target_project_id, task_id=None, action="market_revenue_reversal",
                currency=purchase.currency, amount_minor=seller_amount,
                postings=[{"account": "workspace.available", "amount_minor": -seller_amount},
                          {"account": "marketplace.clearing", "amount_minor": seller_amount}],
                reference=f"market:{purchase.market_item_id}:refund:{purchase.id}:{reason}",
                occurred_at=occurred_at,
            ))
        purchase.status = "refunded"
        purchase.refunded_at = occurred_at
        self._session.add(OutboxRow(
            tenant_id=tenant_id, workspace_id=purchase.buyer_workspace_id,
            event_type="marketplace.purchase.refunded", aggregate_type="market_purchase",
            aggregate_id=purchase.id,
            payload={"purchaseId": purchase.id, "projectId": purchase.target_project_id,
                     "amountMinor": purchase.amount_minor, "currency": purchase.currency,
                     "reason": reason, "requestId": request_id},
            schema_version=1, created_at=occurred_at, published_at=None,
        ))
        self._session.flush()

    def find_idempotency(self, scope: IdempotencyScope) -> IdempotencyRecord | None:
        row = self._session.scalar(
            select(IdempotencyRow).where(
                IdempotencyRow.workspace_id == scope.workspace_id,
                IdempotencyRow.actor_id == scope.actor_id,
                IdempotencyRow.operation == scope.operation,
                IdempotencyRow.idempotency_key == scope.key,
            )
        )
        if row is None:
            return None
        created_at = _as_utc(row.created_at)
        assert created_at is not None
        return IdempotencyRecord(scope, row.request_fingerprint, row.receipt_json, created_at)

    def save_idempotency(self, record: IdempotencyRecord) -> None:
        try:
            self._session.add(
                IdempotencyRow(
                    workspace_id=record.scope.workspace_id,
                    actor_id=record.scope.actor_id,
                    operation=record.scope.operation,
                    idempotency_key=record.scope.key,
                    request_fingerprint=record.request_fingerprint,
                    receipt_json=record.receipt_json,
                    created_at=record.created_at,
                )
            )
            self._session.flush()
        except IntegrityError as error:
            raise _IdempotencyRace(record) from error

    def append_audit(self, event: AuditEvent) -> None:
        self._session.add(
            AuditEventRow(
                id=event.id,
                schema_version=event.schema_version,
                workspace_id=event.workspace_id,
                actor_id=event.actor_id,
                request_id=event.request_id,
                subject_type=event.subject_type,
                subject_id=event.subject_id,
                action=event.action,
                before_json=event.before_json,
                after_json=event.after_json,
                result=event.result,
                occurred_at=event.occurred_at,
            )
        )
        self._session.flush()


def _string_list(values: tuple[str, ...]) -> str:
    import json

    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _revenue_shares(values: tuple[RevenueShare, ...]) -> str:
    import json

    return json.dumps(
        [share.to_dict() for share in values],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _template_values(template: Template) -> dict[str, object]:
    return {
        "id": template.id,
        "workspace_id": template.workspace_id,
        "owner_id": template.owner_id,
        "title": template.title,
        "kind": template.kind.value,
        "tags_json": _string_list(template.tags),
        "revision": template.revision,
        "review_state": template.review_state.value,
        "publication_state": template.publication_state.value,
        "latest_version_id": template.latest_version_id,
        "published_version_id": template.published_version_id,
        "market_item_id": template.market_item_id,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _template_update_values(template: Template) -> dict[str, object]:
    values = _template_values(template)
    del values["id"]
    del values["workspace_id"]
    del values["created_at"]
    return values


def _market_item_values(item: MarketItem) -> dict[str, object]:
    return {
        "id": item.id,
        "source_kind": item.source_kind.value,
        "source_id": item.source_id,
        "source_workspace_id": item.source_workspace_id,
        "source_version_id": item.source_version_id,
        "title": item.title,
        "template_kind": item.template_kind.value if item.template_kind is not None else None,
        "author_id": item.author_id,
        "tags_json": _string_list(item.tags),
        "price_minor": item.price_minor,
        "rights_id": item.rights.id,
        "rights_scope": item.rights.scope.value,
        "rights_commercial_use": item.rights.commercial_use,
        "rights_attribution_required": item.rights.attribution_required,
        "rights_inheritable_scopes_json": _string_list(item.rights.inheritable_scopes),
        "rights_allowed_workspace_ids_json": _string_list(item.rights.allowed_workspace_ids),
        "rights_allow_fork": item.rights.allow_fork,
        "rights_created_at": item.rights.created_at,
        "revenue_rule_id": item.revenue_rule.id,
        "revenue_shares_json": _revenue_shares(item.revenue_rule.shares),
        "revenue_created_at": item.revenue_rule.created_at,
        "source_snapshot_json": item.source_snapshot_json,
        "source_snapshot_digest": item.source_snapshot_digest,
        "publication_state": item.publication_state.value,
        "revision": item.revision,
        "published_at": item.published_at,
        "withdrawn_at": item.withdrawn_at,
        "source_fork_id": item.source_fork_id,
    }


def _market_item_update_values(item: MarketItem) -> dict[str, object]:
    values = _market_item_values(item)
    del values["id"]
    del values["source_id"]
    return values


def _market_history_json(item: MarketItem) -> str:
    import json

    return json.dumps(
        {
            **item.to_dict(),
            "source_snapshot_json": item.source_snapshot_json,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


type SessionFactory = async_sessionmaker[AsyncSession]


class SqlAlchemyMarketplaceRepository:
    """同步领域端口到 SQLAlchemy asyncio 驱动的生产桥接。"""

    def __init__(
        self,
        session_factory: SessionFactory | Callable[[], AsyncSession],
        *,
        _owned_engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._owned_engine = _owned_engine
        self._runner = _LoopRunner()

    @classmethod
    def from_url(cls, database_url: str) -> SqlAlchemyMarketplaceRepository:
        """创建由适配器独占事件循环和连接池的生产仓储；不会隐式建表。"""

        connect_args: dict[str, object] = {}
        engine_options: dict[str, object] = {"pool_pre_ping": True}
        if database_url.startswith("sqlite"):
            connect_args["timeout"] = 30
        else:
            engine_options.update(pool_size=10, max_overflow=20, pool_recycle=3600)
        engine = create_async_engine(database_url, connect_args=connect_args, **engine_options)
        if database_url.startswith("sqlite"):

            @event.listens_for(engine.sync_engine, "connect")
            def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        return cls(async_sessionmaker(engine, expire_on_commit=False), _owned_engine=engine)

    def close(self) -> None:
        if self._owned_engine is not None:
            self._runner.run(self._owned_engine.dispose())
            self._owned_engine = None
        self._runner.close()

    def atomic(self, operation: Callable[[MarketplaceTransaction], T]) -> T:
        async def execute() -> T:
            async with self._session_factory() as session:
                try:
                    async with session.begin():
                        return await session.run_sync(lambda sync_session: operation(_Transaction(sync_session)))
                except _IdempotencyRace as race:
                    raced_record = race.record
                    existing = await session.run_sync(
                        lambda sync_session: _Transaction(sync_session).find_idempotency(raced_record.scope)
                    )
                    if existing is None:
                        raise RuntimeError("idempotency constraint failed without a committed receipt") from race
                    if existing.request_fingerprint != raced_record.request_fingerprint:
                        raise IdempotencyConflict("idempotency key was reused with a different request") from race
                    return cast(T, CommandReceipt.from_json(existing.receipt_json))

        return self._runner.run(execute())

    def get_template(self, workspace_id: str, template_id: str) -> Template | None:
        return self._read(
            lambda session: _Transaction(session).find_template(workspace_id, template_id),
        )

    def list_templates(self, workspace_id: str) -> tuple[Template, ...]:
        def read(session: Session) -> tuple[Template, ...]:
            ids = session.scalars(select(TemplateRow.id).where(TemplateRow.workspace_id == workspace_id)
                                  .order_by(TemplateRow.updated_at.desc(), TemplateRow.id)).all()
            transaction = _Transaction(session)
            return tuple(template for template_id in ids
                         if (template := transaction.find_template(workspace_id, template_id)) is not None)
        return self._read(read)

    def search_market(self, spec: MarketSearchSpec) -> MarketSearchResult:
        def read(session: Session) -> MarketSearchResult:
            visibility = or_(
                MarketItemRow.source_workspace_id == spec.viewer_workspace_id,
                MarketItemRow.rights_scope == RightsScope.PUBLIC.value,
                and_(
                    MarketItemRow.rights_scope == RightsScope.ALLOWLIST.value,
                    exists(
                        select(MarketItemAllowedWorkspaceRow.id).where(
                            MarketItemAllowedWorkspaceRow.item_id == MarketItemRow.id,
                            MarketItemAllowedWorkspaceRow.workspace_id == spec.viewer_workspace_id,
                        )
                    ),
                ),
            )
            clauses = [MarketItemRow.publication_state == PublicationState.PUBLISHED.value, visibility]
            if spec.search:
                needle = spec.search.casefold()
                clauses.append(
                    or_(
                        func.lower(MarketItemRow.id).contains(needle, autoescape=True),
                        func.lower(MarketItemRow.title).contains(needle, autoescape=True),
                        exists(
                            select(MarketItemTagRow.id).where(
                                MarketItemTagRow.item_id == MarketItemRow.id,
                                func.lower(MarketItemTagRow.tag).contains(needle, autoescape=True),
                            )
                        ),
                    )
                )
            if spec.kinds:
                clauses.append(MarketItemRow.template_kind.in_(tuple(kind.value for kind in spec.kinds)))
            for tag in sorted(spec.tags):
                clauses.append(
                    exists(
                        select(MarketItemTagRow.id).where(
                            MarketItemTagRow.item_id == MarketItemRow.id,
                            MarketItemTagRow.tag == tag,
                        )
                    )
                )
            if spec.commercial_use is not None:
                clauses.append(MarketItemRow.rights_commercial_use == spec.commercial_use)
            if spec.minimum_price_minor is not None:
                clauses.append(MarketItemRow.price_minor >= spec.minimum_price_minor)
            if spec.maximum_price_minor is not None:
                clauses.append(MarketItemRow.price_minor <= spec.maximum_price_minor)
            total = session.scalar(select(func.count()).select_from(MarketItemRow).where(*clauses)) or 0
            page_clauses = list(clauses)
            if spec.after is not None:
                published_at, item_id = spec.after
                page_clauses.append(
                    or_(
                        MarketItemRow.published_at < published_at,
                        and_(MarketItemRow.published_at == published_at, MarketItemRow.id < item_id),
                    )
                )
            rows = session.scalars(
                select(MarketItemRow)
                .where(*page_clauses)
                .order_by(MarketItemRow.published_at.desc(), MarketItemRow.id.desc())
                .limit(spec.limit)
            ).all()
            return MarketSearchResult(tuple(_market_item(row) for row in rows), total)

        return self._read(read)

    def get_fork(self, workspace_id: str, fork_id: str) -> ForkRecord | None:
        def read(session: Session) -> ForkRecord | None:
            row = session.scalar(select(ForkRow).where(ForkRow.workspace_id == workspace_id, ForkRow.id == fork_id))
            return None if row is None else _fork_record(session, row)

        return self._read(read)

    def list_forks(self, workspace_id: str) -> tuple[ForkRecord, ...]:
        def read(session: Session) -> tuple[ForkRecord, ...]:
            rows = session.scalars(
                select(ForkRow)
                .where(ForkRow.workspace_id == workspace_id)
                .order_by(ForkRow.created_at.desc(), ForkRow.id.desc())
            ).all()
            return tuple(_fork_record(session, row) for row in rows)

        return self._read(read)

    def get_fork_project(self, workspace_id: str, project_id: str) -> ForkProjectSnapshot | None:
        return self._read(
            lambda session: _Transaction(session).find_fork_project(workspace_id, project_id),
        )

    def list_audit_events(self, workspace_id: str, *, subject_id: str | None = None) -> tuple[AuditEvent, ...]:
        def read(session: Session) -> tuple[AuditEvent, ...]:
            clauses = [AuditEventRow.workspace_id == workspace_id]
            if subject_id is not None:
                clauses.append(AuditEventRow.subject_id == subject_id)
            rows = session.scalars(
                select(AuditEventRow).where(*clauses).order_by(AuditEventRow.occurred_at, AuditEventRow.id)
            ).all()
            return tuple(_audit_event(row) for row in rows)

        return self._read(read)

    def _read(self, operation: Callable[[Session], T]) -> T:
        async def execute() -> T:
            async with self._session_factory() as session:
                return await session.run_sync(operation)

        return self._runner.run(execute())


def _audit_event(row: AuditEventRow) -> AuditEvent:
    occurred_at = _as_utc(row.occurred_at)
    assert occurred_at is not None
    return AuditEvent(
        id=row.id,
        schema_version=row.schema_version,
        workspace_id=row.workspace_id,
        actor_id=row.actor_id,
        request_id=row.request_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        action=row.action,
        before_json=row.before_json,
        after_json=row.after_json,
        result=row.result,
        occurred_at=occurred_at,
    )
