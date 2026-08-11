from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

TEMPLATE_VIEW = "template.view"
TEMPLATE_MANAGE = "template.manage"
COMMUNITY_VIEW = "community.view"
COMMUNITY_MANAGE = "community.manage"
ADMIN_BUSINESS_VIEW = "admin.business.view"
ADMIN_BUSINESS_MANAGE = "admin.business.manage"


class MarketplaceError(Exception):
    code = "MARKETPLACE_ERROR"


class PermissionDenied(MarketplaceError):
    code = "PERMISSION_DENIED"


class InvalidInput(MarketplaceError):
    code = "INVALID_INPUT"


class TemplateNotFound(MarketplaceError):
    code = "TEMPLATE_NOT_FOUND"


class MarketItemNotFound(MarketplaceError):
    code = "MARKET_ITEM_NOT_FOUND"


class IdempotencyConflict(MarketplaceError):
    code = "IDEMPOTENCY_CONFLICT"


class VersionConflict(MarketplaceError):
    code = "VERSION_CONFLICT"


class InvalidTransition(MarketplaceError):
    code = "INVALID_STATUS_TRANSITION"


class ReviewNotFound(MarketplaceError):
    code = "REVIEW_NOT_FOUND"


class ForkNotFound(MarketplaceError):
    code = "FORK_NOT_FOUND"


class ForkProjectNotFound(MarketplaceError):
    code = "FORK_PROJECT_NOT_FOUND"


class ForkNotAllowed(MarketplaceError):
    code = "FORK_NOT_ALLOWED"


class MarketPurchaseNotFound(MarketplaceError):
    code = "MARKET_PURCHASE_NOT_FOUND"


class TemplateKind(StrEnum):
    PROJECT = "project"
    STORYBOARD = "storyboard"
    CHARACTER = "character"
    STYLE = "style"
    RELEASE_PACKAGE = "release_package"
    EXPORT = "export"
    MESSAGE = "message"


class MarketSourceKind(StrEnum):
    TEMPLATE = "template"
    COMMUNITY_PROJECT = "community_project"


class RightsScope(StrEnum):
    PUBLIC = "public"
    WORKSPACE = "workspace"
    ALLOWLIST = "allowlist"


class ReviewState(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublicationState(StrEnum):
    UNPUBLISHED = "unpublished"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RequestContext:
    actor_id: str
    workspace_id: str
    permissions: frozenset[str]
    request_id: str
    tenant_id: str = ""


@dataclass(frozen=True, slots=True)
class RightsPolicyInput:
    scope: RightsScope
    commercial_use: bool
    attribution_required: bool
    inheritable_scopes: tuple[str, ...]
    allowed_workspace_ids: tuple[str, ...] = ()
    allow_fork: bool = True

    @classmethod
    def public(
        cls,
        *,
        commercial_use: bool,
        attribution_required: bool,
        inheritable_scopes: tuple[str, ...],
    ) -> Self:
        return cls(
            scope=RightsScope.PUBLIC,
            commercial_use=commercial_use,
            attribution_required=attribution_required,
            inheritable_scopes=inheritable_scopes,
        )


@dataclass(frozen=True, slots=True)
class RevenueShareInput:
    beneficiary_role: str
    basis_points: int


@dataclass(frozen=True, slots=True)
class CreateTemplate:
    title: str
    kind: TemplateKind
    content: dict[str, object]
    tags: tuple[str, ...]
    rights: RightsPolicyInput
    revenue_shares: tuple[RevenueShareInput, ...]
    idempotency_key: str
    price_minor: int = 0


@dataclass(frozen=True, slots=True)
class CreateTemplateVersion:
    template_id: str
    expected_revision: int
    content: dict[str, object]
    rights: RightsPolicyInput
    revenue_shares: tuple[RevenueShareInput, ...]
    idempotency_key: str
    price_minor: int = 0


@dataclass(frozen=True, slots=True)
class SubmitTemplateReview:
    template_id: str
    expected_revision: int
    statement: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DecideTemplateReview:
    review_id: str
    expected_revision: int
    decision: ReviewDecision
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class WithdrawTemplate:
    template_id: str
    expected_revision: int
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CreateFork:
    market_item_id: str
    expected_market_revision: int
    expected_source_version_id: str
    project_name: str
    intended_commercial_use: bool
    requested_inheritable_scopes: tuple[str, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PublishCommunityProject:
    fork_id: str
    expected_project_revision: int
    title: str
    tags: tuple[str, ...]
    rights: RightsPolicyInput
    revenue_shares: tuple[RevenueShareInput, ...]
    price_minor: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RefundMarketPurchase:
    purchase_id: str
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RightsPolicyVersion:
    id: str
    scope: RightsScope
    commercial_use: bool
    attribution_required: bool
    inheritable_scopes: tuple[str, ...]
    allowed_workspace_ids: tuple[str, ...]
    allow_fork: bool
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "scope": self.scope.value,
            "commercial_use": self.commercial_use,
            "attribution_required": self.attribution_required,
            "inheritable_scopes": list(self.inheritable_scopes),
            "allowed_workspace_ids": list(self.allowed_workspace_ids),
            "allow_fork": self.allow_fork,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class RevenueShare:
    beneficiary_role: str
    basis_points: int

    def to_dict(self) -> dict[str, object]:
        return {"beneficiary_role": self.beneficiary_role, "basis_points": self.basis_points}


@dataclass(frozen=True, slots=True)
class RevenueRuleVersion:
    id: str
    shares: tuple[RevenueShare, ...]
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "shares": [share.to_dict() for share in self.shares],
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TemplateVersion:
    id: str
    template_id: str
    workspace_id: str
    number: int
    content_json: str
    content_digest: str
    price_minor: int
    rights: RightsPolicyVersion
    revenue_rule: RevenueRuleVersion
    created_by: str
    created_at: datetime

    @property
    def content(self) -> dict[str, object]:
        value = json.loads(self.content_json)
        if not isinstance(value, dict):
            raise RuntimeError("stored template content is not a JSON object")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "workspace_id": self.workspace_id,
            "number": self.number,
            "content": self.content,
            "content_digest": self.content_digest,
            "price_minor": self.price_minor,
            "rights": self.rights.to_dict(),
            "revenue_rule": self.revenue_rule.to_dict(),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TemplateReview:
    id: str
    template_id: str
    template_version_id: str
    workspace_id: str
    status: ReviewState
    statement: str
    submitted_by: str
    submitted_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "template_version_id": self.template_version_id,
            "workspace_id": self.workspace_id,
            "status": self.status.value,
            "statement": self.statement,
            "submitted_by": self.submitted_by,
            "submitted_at": self.submitted_at.isoformat(),
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_reason": self.decision_reason,
        }


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    workspace_id: str
    owner_id: str
    title: str
    kind: TemplateKind
    tags: tuple[str, ...]
    revision: int
    review_state: ReviewState
    publication_state: PublicationState
    latest_version_id: str
    published_version_id: str | None
    market_item_id: str | None
    versions: tuple[TemplateVersion, ...]
    reviews: tuple[TemplateReview, ...]
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "owner_id": self.owner_id,
            "title": self.title,
            "kind": self.kind.value,
            "tags": list(self.tags),
            "revision": self.revision,
            "review_state": self.review_state.value,
            "publication_state": self.publication_state.value,
            "latest_version_id": self.latest_version_id,
            "published_version_id": self.published_version_id,
            "market_item_id": self.market_item_id,
            "versions": [version.to_dict() for version in self.versions],
            "reviews": [review.to_dict() for review in self.reviews],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class MarketItem:
    id: str
    source_kind: MarketSourceKind
    source_id: str
    source_workspace_id: str
    source_version_id: str
    title: str
    template_kind: TemplateKind | None
    author_id: str
    tags: tuple[str, ...]
    price_minor: int
    rights: RightsPolicyVersion
    revenue_rule: RevenueRuleVersion
    source_snapshot_json: str
    source_snapshot_digest: str
    publication_state: PublicationState
    revision: int
    published_at: datetime
    withdrawn_at: datetime | None = None
    source_fork_id: str | None = None

    def visible_to(self, workspace_id: str) -> bool:
        if self.source_workspace_id == workspace_id:
            return True
        if self.rights.scope is RightsScope.PUBLIC:
            return True
        return self.rights.scope is RightsScope.ALLOWLIST and workspace_id in self.rights.allowed_workspace_ids

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_kind": self.source_kind.value,
            "source_id": self.source_id,
            "source_workspace_id": self.source_workspace_id,
            "source_version_id": self.source_version_id,
            "title": self.title,
            "template_kind": self.template_kind.value if self.template_kind else None,
            "author_id": self.author_id,
            "tags": list(self.tags),
            "price_minor": self.price_minor,
            "rights": self.rights.to_dict(),
            "revenue_rule": self.revenue_rule.to_dict(),
            "source_snapshot_digest": self.source_snapshot_digest,
            "publication_state": self.publication_state.value,
            "revision": self.revision,
            "published_at": self.published_at.isoformat(),
            "withdrawn_at": self.withdrawn_at.isoformat() if self.withdrawn_at else None,
            "source_fork_id": self.source_fork_id,
        }


@dataclass(frozen=True, slots=True)
class MarketQuery:
    search: str | None = None
    kinds: frozenset[TemplateKind] = frozenset()
    tags: frozenset[str] = frozenset()
    commercial_use: bool | None = None
    minimum_price_minor: int | None = None
    maximum_price_minor: int | None = None
    page_size: int = 50
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class MarketSearchSpec:
    viewer_workspace_id: str
    search: str | None
    kinds: frozenset[TemplateKind]
    tags: frozenset[str]
    commercial_use: bool | None
    minimum_price_minor: int | None
    maximum_price_minor: int | None
    after: tuple[datetime, str] | None
    limit: int


@dataclass(frozen=True, slots=True)
class MarketSearchResult:
    items: tuple[MarketItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class MarketPage:
    items: tuple[MarketItem, ...]
    next_cursor: str | None
    total: int

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "next_cursor": self.next_cursor,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    id: str
    market_item_id: str
    source_kind: MarketSourceKind
    source_id: str
    source_workspace_id: str
    source_version_id: str
    title: str
    content_json: str
    content_digest: str
    captured_at: datetime

    @property
    def content(self) -> dict[str, object]:
        value = json.loads(self.content_json)
        if not isinstance(value, dict):
            raise RuntimeError("stored source snapshot is not a JSON object")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "market_item_id": self.market_item_id,
            "source_kind": self.source_kind.value,
            "source_id": self.source_id,
            "source_workspace_id": self.source_workspace_id,
            "source_version_id": self.source_version_id,
            "title": self.title,
            "content": self.content,
            "content_digest": self.content_digest,
            "captured_at": self.captured_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class RightsRecord:
    id: str
    workspace_id: str
    source_policy_version_id: str
    granted_scopes: tuple[str, ...]
    commercial_use: bool
    attribution_required: bool
    rights_holder_id: str
    granted_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "source_policy_version_id": self.source_policy_version_id,
            "granted_scopes": list(self.granted_scopes),
            "commercial_use": self.commercial_use,
            "attribution_required": self.attribution_required,
            "rights_holder_id": self.rights_holder_id,
            "granted_at": self.granted_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ForkProjectSnapshot:
    id: str
    version_id: str
    workspace_id: str
    name: str
    revision: int
    source_snapshot_id: str
    content_json: str
    content_digest: str
    created_by: str
    created_at: datetime

    @property
    def content(self) -> dict[str, object]:
        value = json.loads(self.content_json)
        if not isinstance(value, dict):
            raise RuntimeError("stored fork project content is not a JSON object")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version_id": self.version_id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "revision": self.revision,
            "source_snapshot_id": self.source_snapshot_id,
            "content": self.content,
            "content_digest": self.content_digest,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ForkRecord:
    id: str
    workspace_id: str
    target_project_id: str
    source_snapshot: SourceSnapshot
    rights_record: RightsRecord
    revenue_rule: RevenueRuleVersion
    parent_fork_id: str | None
    ancestor_fork_ids: tuple[str, ...]
    lineage: tuple[LineageNode, ...]
    created_by: str
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "target_project_id": self.target_project_id,
            "source_snapshot": self.source_snapshot.to_dict(),
            "rights_record": self.rights_record.to_dict(),
            "revenue_rule": self.revenue_rule.to_dict(),
            "parent_fork_id": self.parent_fork_id,
            "ancestor_fork_ids": list(self.ancestor_fork_ids),
            "lineage": [node.to_dict() for node in self.lineage],
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class LineageNode:
    fork_id: str
    workspace_id: str
    target_project_id: str
    source_kind: MarketSourceKind
    source_id: str
    source_version_id: str
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "fork_id": self.fork_id,
            "workspace_id": self.workspace_id,
            "target_project_id": self.target_project_id,
            "source_kind": self.source_kind.value,
            "source_id": self.source_id,
            "source_version_id": self.source_version_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    resource_type: str
    resource_id: str
    resource_version: int

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "resource_version": self.resource_version,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str) -> Self:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise RuntimeError("stored idempotency result is invalid")
        return cls(
            resource_type=str(payload["resource_type"]),
            resource_id=str(payload["resource_id"]),
            resource_version=int(payload["resource_version"]),
        )


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    workspace_id: str
    actor_id: str
    operation: str
    key: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    scope: IdempotencyScope
    request_fingerprint: str
    receipt_json: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    schema_version: int
    workspace_id: str
    actor_id: str
    request_id: str
    subject_type: str
    subject_id: str
    action: str
    before_json: str
    after_json: str
    result: str
    occurred_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "request_id": self.request_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "action": self.action,
            "before": json.loads(self.before_json),
            "after": json.loads(self.after_json),
            "result": self.result,
            "occurred_at": self.occurred_at.isoformat(),
        }


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvalidInput("value must be JSON serializable") from exc


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
