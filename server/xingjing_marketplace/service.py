from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from .domain import (
    ADMIN_BUSINESS_MANAGE,
    ADMIN_BUSINESS_VIEW,
    COMMUNITY_MANAGE,
    COMMUNITY_VIEW,
    TEMPLATE_MANAGE,
    TEMPLATE_VIEW,
    AuditEvent,
    CommandReceipt,
    CreateFork,
    CreateTemplate,
    CreateTemplateVersion,
    DecideTemplateReview,
    ForkNotAllowed,
    ForkNotFound,
    ForkProjectNotFound,
    ForkProjectSnapshot,
    ForkRecord,
    IdempotencyConflict,
    IdempotencyRecord,
    IdempotencyScope,
    InvalidInput,
    InvalidTransition,
    LineageNode,
    MarketItem,
    MarketPage,
    MarketPurchaseNotFound,
    MarketQuery,
    MarketSearchSpec,
    MarketSourceKind,
    PermissionDenied,
    PublicationState,
    PublishCommunityProject,
    RefundMarketPurchase,
    RequestContext,
    RevenueRuleVersion,
    RevenueShare,
    ReviewDecision,
    ReviewNotFound,
    ReviewState,
    RightsPolicyVersion,
    RightsRecord,
    RightsScope,
    SourceSnapshot,
    SubmitTemplateReview,
    Template,
    TemplateNotFound,
    TemplateReview,
    TemplateVersion,
    VersionConflict,
    WithdrawTemplate,
    canonical_json,
    fingerprint,
)
from .ids import uuid7
from .ports import MarketplaceRepository, MarketplaceTransaction

Clock = Callable[[], datetime]
IdGenerator = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MarketplaceService:
    def __init__(
        self,
        repository: MarketplaceRepository,
        *,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or _utc_now
        self._ids = ids or uuid7

    def create_template(self, context: RequestContext, command: CreateTemplate) -> CommandReceipt:
        self._require_context(context, TEMPLATE_MANAGE)
        title = command.title.strip()
        if not title:
            raise InvalidInput("template title is required")
        self._validate_price(command.price_minor)
        content_json = canonical_json(command.content)
        tags = self._normalize_tokens(command.tags, field="tags")
        inheritable_scopes = self._normalize_tokens(command.rights.inheritable_scopes, field="inheritable scopes")
        allowed_workspaces = self._normalize_tokens(command.rights.allowed_workspace_ids, field="allowed workspace ids")
        if command.rights.scope is RightsScope.ALLOWLIST and not allowed_workspaces:
            raise InvalidInput("allowlist rights require at least one workspace")
        if command.rights.scope is not RightsScope.ALLOWLIST and allowed_workspaces:
            raise InvalidInput("allowed workspaces are only valid for allowlist rights")
        shares = self._normalize_shares(command.revenue_shares)
        idempotency_key = command.idempotency_key.strip()
        if not idempotency_key:
            raise InvalidInput("idempotency key is required")
        requested_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "create_template", idempotency_key)
        request_fingerprint = fingerprint(
            {
                "title": title,
                "kind": command.kind.value,
                "content": command.content,
                "tags": list(tags),
                "price_minor": command.price_minor,
                "rights": {
                    "scope": command.rights.scope.value,
                    "commercial_use": command.rights.commercial_use,
                    "attribution_required": command.rights.attribution_required,
                    "inheritable_scopes": list(inheritable_scopes),
                    "allowed_workspace_ids": list(allowed_workspaces),
                    "allow_fork": command.rights.allow_fork,
                },
                "revenue_shares": [share.to_dict() for share in shares],
            }
        )

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)

            template_id = self._ids()
            rights = RightsPolicyVersion(
                id=self._ids(),
                scope=command.rights.scope,
                commercial_use=command.rights.commercial_use,
                attribution_required=command.rights.attribution_required,
                inheritable_scopes=inheritable_scopes,
                allowed_workspace_ids=allowed_workspaces,
                allow_fork=command.rights.allow_fork,
                created_at=requested_at,
            )
            revenue_rule = RevenueRuleVersion(id=self._ids(), shares=shares, created_at=requested_at)
            version = TemplateVersion(
                id=self._ids(),
                template_id=template_id,
                workspace_id=context.workspace_id,
                number=1,
                content_json=content_json,
                content_digest=fingerprint(command.content),
                price_minor=command.price_minor,
                rights=rights,
                revenue_rule=revenue_rule,
                created_by=context.actor_id,
                created_at=requested_at,
            )
            template = Template(
                id=template_id,
                workspace_id=context.workspace_id,
                owner_id=context.actor_id,
                title=title,
                kind=command.kind,
                tags=tags,
                revision=1,
                review_state=ReviewState.DRAFT,
                publication_state=PublicationState.UNPUBLISHED,
                latest_version_id=version.id,
                published_version_id=None,
                market_item_id=None,
                versions=(version,),
                reviews=(),
                created_at=requested_at,
                updated_at=requested_at,
            )
            receipt = CommandReceipt("template", template.id, template.revision)
            transaction.save_template(template, expected_revision=None)
            transaction.append_audit(
                AuditEvent(
                    id=self._ids(),
                    schema_version=1,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    subject_type="template",
                    subject_id=template.id,
                    action="template.created",
                    before_json="{}",
                    after_json=canonical_json(
                        {"revision": template.revision, "latest_version_id": template.latest_version_id}
                    ),
                    result="succeeded",
                    occurred_at=requested_at,
                )
            )
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), requested_at))
            return receipt

        return self._repository.atomic(operation)

    def create_fork(self, context: RequestContext, command: CreateFork) -> CommandReceipt:
        self._require_any_context(context, frozenset({COMMUNITY_MANAGE, TEMPLATE_MANAGE}))
        project_name = command.project_name.strip()
        idempotency_key = command.idempotency_key.strip()
        requested_scopes = self._normalize_tokens(
            command.requested_inheritable_scopes,
            field="requested inheritable scopes",
        )
        if (
            command.expected_market_revision < 1
            or not command.expected_source_version_id.strip()
            or not project_name
            or not requested_scopes
            or not idempotency_key
        ):
            raise InvalidInput("fork source versions, project name, scopes and idempotency key are required")
        requested_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "create_fork", idempotency_key)
        request_fingerprint = fingerprint(
            {
                "market_item_id": command.market_item_id,
                "expected_market_revision": command.expected_market_revision,
                "expected_source_version_id": command.expected_source_version_id,
                "project_name": project_name,
                "intended_commercial_use": command.intended_commercial_use,
                "requested_inheritable_scopes": list(requested_scopes),
            }
        )

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)
            item = transaction.find_market_item(command.market_item_id)
            if item is None or item.publication_state is not PublicationState.PUBLISHED:
                raise ForkNotAllowed("market source is unavailable")
            if item.revision != command.expected_market_revision or (
                item.source_version_id != command.expected_source_version_id
            ):
                raise VersionConflict(item.id)
            if not item.visible_to(context.workspace_id) or not item.rights.allow_fork:
                raise ForkNotAllowed("market source is not licensed to this workspace")
            if command.intended_commercial_use and not item.rights.commercial_use:
                raise ForkNotAllowed("source rights do not allow commercial use")
            if not frozenset(requested_scopes) <= frozenset(item.rights.inheritable_scopes):
                raise ForkNotAllowed("requested inheritance exceeds source rights")

            source_snapshot = SourceSnapshot(
                id=self._ids(),
                market_item_id=item.id,
                source_kind=item.source_kind,
                source_id=item.source_id,
                source_workspace_id=item.source_workspace_id,
                source_version_id=item.source_version_id,
                title=item.title,
                content_json=item.source_snapshot_json,
                content_digest=item.source_snapshot_digest,
                captured_at=requested_at,
            )
            if fingerprint(source_snapshot.content) != source_snapshot.content_digest:
                raise InvalidTransition("market source snapshot digest does not match its content")
            rights_record = RightsRecord(
                id=self._ids(),
                workspace_id=context.workspace_id,
                source_policy_version_id=item.rights.id,
                granted_scopes=requested_scopes,
                commercial_use=command.intended_commercial_use,
                attribution_required=item.rights.attribution_required,
                rights_holder_id=item.author_id,
                granted_at=requested_at,
            )
            parent = transaction.find_fork(item.source_fork_id) if item.source_fork_id else None
            if item.source_fork_id and parent is None:
                raise InvalidTransition("market source lineage is incomplete")
            ancestor_fork_ids = (*parent.ancestor_fork_ids, parent.id) if parent else ()
            project = ForkProjectSnapshot(
                id=self._ids(),
                version_id=self._ids(),
                workspace_id=context.workspace_id,
                name=project_name,
                revision=1,
                source_snapshot_id=source_snapshot.id,
                content_json=source_snapshot.content_json,
                content_digest=source_snapshot.content_digest,
                created_by=context.actor_id,
                created_at=requested_at,
            )
            record = ForkRecord(
                id=self._ids(),
                workspace_id=context.workspace_id,
                target_project_id=project.id,
                source_snapshot=source_snapshot,
                rights_record=rights_record,
                revenue_rule=item.revenue_rule,
                parent_fork_id=parent.id if parent else None,
                ancestor_fork_ids=ancestor_fork_ids,
                lineage=(
                    *parent.lineage,
                    LineageNode(
                        fork_id=parent.id,
                        workspace_id=parent.workspace_id,
                        target_project_id=parent.target_project_id,
                        source_kind=parent.source_snapshot.source_kind,
                        source_id=parent.source_snapshot.source_id,
                        source_version_id=parent.source_snapshot.source_version_id,
                        created_at=parent.created_at,
                    ),
                )
                if parent
                else (),
                created_by=context.actor_id,
                created_at=requested_at,
            )
            receipt = CommandReceipt("fork", record.id, 1)
            transaction.save_fork_project(project)
            transaction.save_fork(record)
            transaction.settle_market_purchase(
                buyer_workspace_id=context.workspace_id,
                tenant_id=context.tenant_id,
                item=item,
                target_project_id=project.id,
                fork_id=record.id,
                request_id=context.request_id,
                occurred_at=requested_at,
            )
            transaction.append_audit(
                AuditEvent(
                    id=self._ids(),
                    schema_version=1,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    subject_type="fork",
                    subject_id=record.id,
                    action="fork.created",
                    before_json="{}",
                    after_json=canonical_json(
                        {
                            "target_project_id": project.id,
                            "source_market_item_id": item.id,
                            "source_version_id": item.source_version_id,
                            "rights_policy_version_id": item.rights.id,
                            "revenue_rule_version_id": item.revenue_rule.id,
                        }
                    ),
                    result="succeeded",
                    occurred_at=requested_at,
                )
            )
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), requested_at))
            return receipt

        return self._repository.atomic(operation)

    def create_template_version(
        self,
        context: RequestContext,
        command: CreateTemplateVersion,
    ) -> CommandReceipt:
        self._require_context(context, TEMPLATE_MANAGE)
        if command.expected_revision < 1:
            raise InvalidInput("expected revision must be positive")
        self._validate_price(command.price_minor)
        content_json = canonical_json(command.content)
        inheritable_scopes = self._normalize_tokens(command.rights.inheritable_scopes, field="inheritable scopes")
        allowed_workspaces = self._normalize_tokens(command.rights.allowed_workspace_ids, field="allowed workspace ids")
        if command.rights.scope is RightsScope.ALLOWLIST and not allowed_workspaces:
            raise InvalidInput("allowlist rights require at least one workspace")
        if command.rights.scope is not RightsScope.ALLOWLIST and allowed_workspaces:
            raise InvalidInput("allowed workspaces are only valid for allowlist rights")
        shares = self._normalize_shares(command.revenue_shares)
        idempotency_key = command.idempotency_key.strip()
        if not idempotency_key:
            raise InvalidInput("idempotency key is required")
        requested_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "create_template_version", idempotency_key)
        request_fingerprint = fingerprint(
            {
                "template_id": command.template_id,
                "expected_revision": command.expected_revision,
                "content": command.content,
                "price_minor": command.price_minor,
                "rights": {
                    "scope": command.rights.scope.value,
                    "commercial_use": command.rights.commercial_use,
                    "attribution_required": command.rights.attribution_required,
                    "inheritable_scopes": list(inheritable_scopes),
                    "allowed_workspace_ids": list(allowed_workspaces),
                    "allow_fork": command.rights.allow_fork,
                },
                "revenue_shares": [share.to_dict() for share in shares],
            }
        )

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)
            template = transaction.find_template(context.workspace_id, command.template_id)
            if template is None:
                raise TemplateNotFound(command.template_id)
            if template.revision != command.expected_revision:
                raise VersionConflict(command.template_id)
            rights = RightsPolicyVersion(
                id=self._ids(),
                scope=command.rights.scope,
                commercial_use=command.rights.commercial_use,
                attribution_required=command.rights.attribution_required,
                inheritable_scopes=inheritable_scopes,
                allowed_workspace_ids=allowed_workspaces,
                allow_fork=command.rights.allow_fork,
                created_at=requested_at,
            )
            revenue_rule = RevenueRuleVersion(id=self._ids(), shares=shares, created_at=requested_at)
            version = TemplateVersion(
                id=self._ids(),
                template_id=template.id,
                workspace_id=template.workspace_id,
                number=template.versions[-1].number + 1,
                content_json=content_json,
                content_digest=fingerprint(command.content),
                price_minor=command.price_minor,
                rights=rights,
                revenue_rule=revenue_rule,
                created_by=context.actor_id,
                created_at=requested_at,
            )
            updated = replace(
                template,
                revision=template.revision + 1,
                review_state=ReviewState.DRAFT,
                latest_version_id=version.id,
                versions=(*template.versions, version),
                updated_at=requested_at,
            )
            receipt = CommandReceipt("template", updated.id, updated.revision)
            transaction.save_template(updated, expected_revision=command.expected_revision)
            transaction.append_audit(
                AuditEvent(
                    id=self._ids(),
                    schema_version=1,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    subject_type="template",
                    subject_id=template.id,
                    action="template.version_created",
                    before_json=canonical_json(
                        {"revision": template.revision, "latest_version_id": template.latest_version_id}
                    ),
                    after_json=canonical_json(
                        {"revision": updated.revision, "latest_version_id": updated.latest_version_id}
                    ),
                    result="succeeded",
                    occurred_at=requested_at,
                )
            )
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), requested_at))
            return receipt

        return self._repository.atomic(operation)

    def submit_template_review(
        self,
        context: RequestContext,
        command: SubmitTemplateReview,
    ) -> CommandReceipt:
        self._require_context(context, TEMPLATE_MANAGE)
        statement = command.statement.strip()
        idempotency_key = command.idempotency_key.strip()
        if command.expected_revision < 1 or not statement or not idempotency_key:
            raise InvalidInput("positive revision, statement and idempotency key are required")
        requested_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "submit_template_review", idempotency_key)
        request_fingerprint = fingerprint(
            {
                "template_id": command.template_id,
                "expected_revision": command.expected_revision,
                "statement": statement,
            }
        )

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)
            template = transaction.find_template(context.workspace_id, command.template_id)
            if template is None:
                raise TemplateNotFound(command.template_id)
            if template.revision != command.expected_revision:
                raise VersionConflict(command.template_id)
            if template.review_state is ReviewState.PENDING:
                raise InvalidTransition("a template cannot have two pending reviews")
            review = TemplateReview(
                id=self._ids(),
                template_id=template.id,
                template_version_id=template.latest_version_id,
                workspace_id=template.workspace_id,
                status=ReviewState.PENDING,
                statement=statement,
                submitted_by=context.actor_id,
                submitted_at=requested_at,
            )
            updated = replace(
                template,
                revision=template.revision + 1,
                review_state=ReviewState.PENDING,
                reviews=(*template.reviews, review),
                updated_at=requested_at,
            )
            receipt = CommandReceipt("template_review", review.id, updated.revision)
            transaction.save_template(updated, expected_revision=command.expected_revision)
            transaction.append_audit(
                AuditEvent(
                    id=self._ids(),
                    schema_version=1,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    subject_type="template_review",
                    subject_id=review.id,
                    action="template.review_submitted",
                    before_json=canonical_json(
                        {"template_revision": template.revision, "review_state": template.review_state.value}
                    ),
                    after_json=canonical_json(
                        {
                            "template_revision": updated.revision,
                            "review_state": updated.review_state.value,
                            "template_version_id": review.template_version_id,
                        }
                    ),
                    result="succeeded",
                    occurred_at=requested_at,
                )
            )
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), requested_at))
            return receipt

        return self._repository.atomic(operation)

    def decide_template_review(
        self,
        context: RequestContext,
        command: DecideTemplateReview,
    ) -> CommandReceipt:
        self._require_context(context, ADMIN_BUSINESS_MANAGE)
        reason = command.reason.strip()
        idempotency_key = command.idempotency_key.strip()
        if command.expected_revision < 1 or not reason or not idempotency_key:
            raise InvalidInput("positive revision, decision reason and idempotency key are required")
        requested_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "decide_template_review", idempotency_key)
        request_fingerprint = fingerprint(
            {
                "review_id": command.review_id,
                "expected_revision": command.expected_revision,
                "decision": command.decision.value,
                "reason": reason,
            }
        )

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)
            template = transaction.find_template_by_review(context.workspace_id, command.review_id)
            if template is None:
                raise ReviewNotFound(command.review_id)
            if template.revision != command.expected_revision:
                raise VersionConflict(template.id)
            review = next(item for item in template.reviews if item.id == command.review_id)
            if review.status is not ReviewState.PENDING or template.review_state is not ReviewState.PENDING:
                raise InvalidTransition("only a pending review can be decided")
            approved = command.decision is ReviewDecision.APPROVE
            decided_review = replace(
                review,
                status=ReviewState.APPROVED if approved else ReviewState.REJECTED,
                decided_by=context.actor_id,
                decided_at=requested_at,
                decision_reason=reason,
            )
            updated_reviews = tuple(decided_review if item.id == review.id else item for item in template.reviews)
            market_item_id = template.market_item_id
            if approved:
                reviewed_version = next(item for item in template.versions if item.id == review.template_version_id)
                existing_item = transaction.find_market_item_by_source(template.id)
                market_item_id = existing_item.id if existing_item else self._ids()
                market_item = MarketItem(
                    id=market_item_id,
                    source_kind=MarketSourceKind.TEMPLATE,
                    source_id=template.id,
                    source_workspace_id=template.workspace_id,
                    source_version_id=reviewed_version.id,
                    title=template.title,
                    template_kind=template.kind,
                    author_id=template.owner_id,
                    tags=template.tags,
                    price_minor=reviewed_version.price_minor,
                    rights=reviewed_version.rights,
                    revenue_rule=reviewed_version.revenue_rule,
                    source_snapshot_json=reviewed_version.content_json,
                    source_snapshot_digest=reviewed_version.content_digest,
                    publication_state=PublicationState.PUBLISHED,
                    revision=(existing_item.revision + 1) if existing_item else 1,
                    published_at=requested_at,
                )
                transaction.save_market_item(
                    market_item,
                    expected_revision=existing_item.revision if existing_item else None,
                )
            updated = replace(
                template,
                revision=template.revision + 1,
                review_state=decided_review.status,
                publication_state=PublicationState.PUBLISHED if approved else template.publication_state,
                published_version_id=review.template_version_id if approved else template.published_version_id,
                market_item_id=market_item_id,
                reviews=updated_reviews,
                updated_at=requested_at,
            )
            receipt = CommandReceipt("template_review", review.id, updated.revision)
            transaction.save_template(updated, expected_revision=command.expected_revision)
            transaction.append_audit(
                AuditEvent(
                    id=self._ids(),
                    schema_version=1,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    subject_type="template_review",
                    subject_id=review.id,
                    action="template.review_approved" if approved else "template.review_rejected",
                    before_json=canonical_json(
                        {
                            "template_revision": template.revision,
                            "review_state": review.status.value,
                            "publication_state": template.publication_state.value,
                        }
                    ),
                    after_json=canonical_json(
                        {
                            "template_revision": updated.revision,
                            "review_state": decided_review.status.value,
                            "publication_state": updated.publication_state.value,
                            "published_version_id": updated.published_version_id,
                        }
                    ),
                    result="succeeded",
                    occurred_at=requested_at,
                )
            )
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), requested_at))
            return receipt

        return self._repository.atomic(operation)

    def publish_community_project(
        self,
        context: RequestContext,
        command: PublishCommunityProject,
    ) -> CommandReceipt:
        self._require_context(context, COMMUNITY_MANAGE)
        title = command.title.strip()
        tags = self._normalize_tokens(command.tags, field="tags")
        inheritable_scopes = self._normalize_tokens(command.rights.inheritable_scopes, field="inheritable scopes")
        allowed_workspaces = self._normalize_tokens(
            command.rights.allowed_workspace_ids,
            field="allowed workspace ids",
        )
        if command.rights.scope is RightsScope.ALLOWLIST and not allowed_workspaces:
            raise InvalidInput("allowlist rights require at least one workspace")
        if command.rights.scope is not RightsScope.ALLOWLIST and allowed_workspaces:
            raise InvalidInput("allowed workspaces are only valid for allowlist rights")
        shares = self._normalize_shares(command.revenue_shares)
        self._validate_price(command.price_minor)
        idempotency_key = command.idempotency_key.strip()
        if command.expected_project_revision < 1 or not title or not idempotency_key:
            raise InvalidInput("project revision, title and idempotency key are required")
        requested_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "publish_community_project", idempotency_key)
        request_fingerprint = fingerprint(
            {
                "fork_id": command.fork_id,
                "expected_project_revision": command.expected_project_revision,
                "title": title,
                "tags": list(tags),
                "price_minor": command.price_minor,
                "rights": {
                    "scope": command.rights.scope.value,
                    "commercial_use": command.rights.commercial_use,
                    "attribution_required": command.rights.attribution_required,
                    "inheritable_scopes": list(inheritable_scopes),
                    "allowed_workspace_ids": list(allowed_workspaces),
                    "allow_fork": command.rights.allow_fork,
                },
                "revenue_shares": [share.to_dict() for share in shares],
            }
        )

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)
            source_fork = transaction.find_fork(command.fork_id)
            if source_fork is None or source_fork.workspace_id != context.workspace_id:
                raise ForkNotFound(command.fork_id)
            project = transaction.find_fork_project(context.workspace_id, source_fork.target_project_id)
            if project is None:
                raise ForkProjectNotFound(source_fork.target_project_id)
            if project.revision != command.expected_project_revision:
                raise VersionConflict(project.id)
            if transaction.find_market_item_by_source(project.id) is not None:
                raise InvalidTransition("community project is already published")
            rights = RightsPolicyVersion(
                id=self._ids(),
                scope=command.rights.scope,
                commercial_use=command.rights.commercial_use,
                attribution_required=command.rights.attribution_required,
                inheritable_scopes=inheritable_scopes,
                allowed_workspace_ids=allowed_workspaces,
                allow_fork=command.rights.allow_fork,
                created_at=requested_at,
            )
            revenue_rule = RevenueRuleVersion(id=self._ids(), shares=shares, created_at=requested_at)
            item = MarketItem(
                id=self._ids(),
                source_kind=MarketSourceKind.COMMUNITY_PROJECT,
                source_id=project.id,
                source_workspace_id=context.workspace_id,
                source_version_id=project.version_id,
                title=title,
                template_kind=None,
                author_id=context.actor_id,
                tags=tags,
                price_minor=command.price_minor,
                rights=rights,
                revenue_rule=revenue_rule,
                source_snapshot_json=project.content_json,
                source_snapshot_digest=project.content_digest,
                publication_state=PublicationState.PUBLISHED,
                revision=1,
                published_at=requested_at,
                source_fork_id=source_fork.id,
            )
            receipt = CommandReceipt("market_item", item.id, item.revision)
            transaction.save_market_item(item, expected_revision=None)
            transaction.append_audit(
                AuditEvent(
                    id=self._ids(),
                    schema_version=1,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    subject_type="community_project",
                    subject_id=project.id,
                    action="community_project.published",
                    before_json="{}",
                    after_json=canonical_json(
                        {
                            "market_item_id": item.id,
                            "source_version_id": item.source_version_id,
                            "source_fork_id": source_fork.id,
                            "rights_policy_version_id": rights.id,
                            "revenue_rule_version_id": revenue_rule.id,
                        }
                    ),
                    result="succeeded",
                    occurred_at=requested_at,
                )
            )
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), requested_at))
            return receipt

        return self._repository.atomic(operation)

    def withdraw_template(self, context: RequestContext, command: WithdrawTemplate) -> CommandReceipt:
        self._require_context(context, ADMIN_BUSINESS_MANAGE)
        reason = command.reason.strip()
        idempotency_key = command.idempotency_key.strip()
        if command.expected_revision < 1 or not reason or not idempotency_key:
            raise InvalidInput("positive revision, withdrawal reason and idempotency key are required")
        requested_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "withdraw_template", idempotency_key)
        request_fingerprint = fingerprint(
            {
                "template_id": command.template_id,
                "expected_revision": command.expected_revision,
                "reason": reason,
            }
        )

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)
            template = transaction.find_template(context.workspace_id, command.template_id)
            if template is None:
                raise TemplateNotFound(command.template_id)
            if template.revision != command.expected_revision:
                raise VersionConflict(template.id)
            if template.publication_state is not PublicationState.PUBLISHED or template.market_item_id is None:
                raise InvalidTransition("only a published template can be withdrawn")
            market_item = transaction.find_market_item_by_source(template.id)
            if market_item is None or market_item.id != template.market_item_id:
                raise InvalidTransition("published template has no matching market item")
            withdrawn_item = replace(
                market_item,
                publication_state=PublicationState.WITHDRAWN,
                revision=market_item.revision + 1,
                withdrawn_at=requested_at,
            )
            updated = replace(
                template,
                revision=template.revision + 1,
                publication_state=PublicationState.WITHDRAWN,
                updated_at=requested_at,
            )
            receipt = CommandReceipt("template", template.id, updated.revision)
            transaction.save_market_item(withdrawn_item, expected_revision=market_item.revision)
            transaction.save_template(updated, expected_revision=command.expected_revision)
            transaction.append_audit(
                AuditEvent(
                    id=self._ids(),
                    schema_version=1,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    subject_type="template",
                    subject_id=template.id,
                    action="template.withdrawn",
                    before_json=canonical_json(
                        {
                            "revision": template.revision,
                            "publication_state": template.publication_state.value,
                            "published_version_id": template.published_version_id,
                        }
                    ),
                    after_json=canonical_json(
                        {
                            "revision": updated.revision,
                            "publication_state": updated.publication_state.value,
                            "published_version_id": updated.published_version_id,
                            "reason": reason,
                        }
                    ),
                    result="succeeded",
                    occurred_at=requested_at,
                )
            )
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), requested_at))
            return receipt

        return self._repository.atomic(operation)

    def list_market(self, context: RequestContext, query: MarketQuery) -> MarketPage:
        self._require_any_context(context, frozenset({COMMUNITY_VIEW, TEMPLATE_VIEW}))
        if not 1 <= query.page_size <= 100:
            raise InvalidInput("market page size must be between 1 and 100")
        for price in (query.minimum_price_minor, query.maximum_price_minor):
            if price is not None:
                self._validate_price(price)
        if (
            query.minimum_price_minor is not None
            and query.maximum_price_minor is not None
            and query.minimum_price_minor > query.maximum_price_minor
        ):
            raise InvalidInput("minimum price cannot exceed maximum price")
        search = query.search.strip() if query.search and query.search.strip() else None
        tags = frozenset(self._normalize_tokens(tuple(sorted(query.tags)), field="market tags"))
        after = self._decode_market_cursor(query.cursor) if query.cursor else None
        result = self._repository.search_market(
            MarketSearchSpec(
                viewer_workspace_id=context.workspace_id,
                search=search,
                kinds=query.kinds,
                tags=tags,
                commercial_use=query.commercial_use,
                minimum_price_minor=query.minimum_price_minor,
                maximum_price_minor=query.maximum_price_minor,
                after=after,
                limit=query.page_size + 1,
            )
        )
        selected = result.items[: query.page_size]
        next_cursor = None
        if len(result.items) > query.page_size and selected:
            last = selected[-1]
            next_cursor = self._encode_market_cursor(last.published_at, last.id)
        return MarketPage(selected, next_cursor, result.total)

    def get_market_item(self, context: RequestContext, item_id: str) -> MarketItem:
        from .domain import MarketItemNotFound

        self._require_any_context(context, frozenset({COMMUNITY_VIEW, TEMPLATE_VIEW}))
        item = self._repository.atomic(lambda transaction: transaction.find_market_item(item_id))
        if item is None or item.withdrawn_at is not None or not item.visible_to(context.workspace_id):
            # Keep private and withdrawn item existence undisclosed.
            raise MarketItemNotFound(item_id)
        return item

    def count_market_item_usage(self, context: RequestContext, item_id: str) -> int:
        self.get_market_item(context, item_id)
        return sum(
            record.source_snapshot.market_item_id == item_id
            for record in self._repository.list_forks(context.workspace_id)
        )

    def get_template(self, context: RequestContext, template_id: str) -> Template:
        self._require_context(context, TEMPLATE_VIEW)
        template = self._repository.get_template(context.workspace_id, template_id)
        if template is None:
            raise TemplateNotFound(template_id)
        return template

    def list_templates(self, context: RequestContext) -> tuple[Template, ...]:
        self._require_context(context, TEMPLATE_VIEW)
        return self._repository.list_templates(context.workspace_id)

    def refund_market_purchase(
        self, context: RequestContext, command: RefundMarketPurchase,
    ) -> CommandReceipt:
        self._require_context(context, ADMIN_BUSINESS_MANAGE)
        purchase_id = command.purchase_id.strip()
        reason = command.reason.strip()
        key = command.idempotency_key.strip()
        if not purchase_id or not reason or not key:
            raise InvalidInput("purchase id, refund reason and idempotency key are required")
        occurred_at = self._clock()
        scope = IdempotencyScope(context.workspace_id, context.actor_id, "refund_market_purchase", key)
        request_fingerprint = fingerprint({"purchase_id": purchase_id, "reason": reason})

        def operation(transaction: MarketplaceTransaction) -> CommandReceipt:
            if previous := transaction.find_idempotency(scope):
                if previous.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different request")
                return CommandReceipt.from_json(previous.receipt_json)
            transaction.refund_market_purchase(
                purchase_id=purchase_id, tenant_id=context.tenant_id,
                request_id=context.request_id, reason=reason, occurred_at=occurred_at,
            )
            receipt = CommandReceipt("market_purchase_refund", purchase_id, 1)
            transaction.save_idempotency(IdempotencyRecord(scope, request_fingerprint, receipt.to_json(), occurred_at))
            transaction.append_audit(AuditEvent(
                id=self._ids(), schema_version=1, workspace_id=context.workspace_id,
                actor_id=context.actor_id, request_id=context.request_id,
                subject_type="market_purchase", subject_id=purchase_id,
                action="market_purchase.refunded", before_json=canonical_json({"status": "settled"}),
                after_json=canonical_json({"status": "refunded", "reason": reason}),
                result="success", occurred_at=occurred_at,
            ))
            return receipt

        try:
            return self._repository.atomic(operation)
        except MarketPurchaseNotFound:
            raise

    def list_templates_for_admin(self, context: RequestContext) -> tuple[Template, ...]:
        self._require_context(context, ADMIN_BUSINESS_VIEW)
        return self._repository.list_templates(context.workspace_id)

    def get_fork(self, context: RequestContext, fork_id: str) -> ForkRecord:
        self._require_any_context(context, frozenset({COMMUNITY_VIEW, TEMPLATE_VIEW}))
        record = self._repository.get_fork(context.workspace_id, fork_id)
        if record is None:
            raise ForkNotFound(fork_id)
        return record

    def list_forks(self, context: RequestContext) -> tuple[ForkRecord, ...]:
        self._require_any_context(context, frozenset({COMMUNITY_VIEW, TEMPLATE_VIEW}))
        return self._repository.list_forks(context.workspace_id)

    def get_lineage(self, context: RequestContext, fork_id: str) -> tuple[LineageNode, ...]:
        return self.get_fork(context, fork_id).lineage

    def get_fork_project(self, context: RequestContext, project_id: str) -> ForkProjectSnapshot:
        self._require_any_context(context, frozenset({COMMUNITY_VIEW, TEMPLATE_VIEW}))
        project = self._repository.get_fork_project(context.workspace_id, project_id)
        if project is None:
            raise ForkProjectNotFound(project_id)
        return project

    def list_audit_events(
        self,
        context: RequestContext,
        *,
        subject_id: str | None = None,
    ) -> tuple[AuditEvent, ...]:
        self._require_context(context, ADMIN_BUSINESS_VIEW)
        return self._repository.list_audit_events(context.workspace_id, subject_id=subject_id)

    @staticmethod
    def _require_context(context: RequestContext, permission: str) -> None:
        if not context.actor_id.strip() or not context.workspace_id.strip() or not context.request_id.strip():
            raise InvalidInput("actor, workspace and request ids are required")
        if permission not in context.permissions:
            raise PermissionDenied(permission)

    @classmethod
    def _require_any_context(cls, context: RequestContext, permissions: frozenset[str]) -> None:
        if not context.actor_id.strip() or not context.workspace_id.strip() or not context.request_id.strip():
            raise InvalidInput("actor, workspace and request ids are required")
        if context.permissions.isdisjoint(permissions):
            raise PermissionDenied(" or ".join(sorted(permissions)))

    @staticmethod
    def _validate_price(value: int) -> None:
        if type(value) is not int or value < 0:
            raise InvalidInput("price must be a non-negative integer in minor units")

    @staticmethod
    def _encode_market_cursor(published_at: datetime, item_id: str) -> str:
        raw = f"{published_at.isoformat()}\x1f{item_id}".encode()
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_market_cursor(cursor: str) -> tuple[datetime, str]:
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            timestamp, item_id = raw.split("\x1f", 1)
            published_at = datetime.fromisoformat(timestamp)
            if published_at.tzinfo is None or not item_id:
                raise ValueError
            return published_at, item_id
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidInput("invalid market cursor") from exc

    @staticmethod
    def _normalize_tokens(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        if len(normalized) != len(values):
            raise InvalidInput(f"{field} must contain unique non-empty values")
        return normalized

    @staticmethod
    def _normalize_shares(values: tuple[object, ...]) -> tuple[RevenueShare, ...]:
        shares: list[RevenueShare] = []
        roles: set[str] = set()
        for value in values:
            role = getattr(value, "beneficiary_role", "").strip()
            basis_points = getattr(value, "basis_points", None)
            if not role or role in roles or type(basis_points) is not int or not 0 < basis_points <= 10_000:
                raise InvalidInput("revenue shares require unique roles and positive integer basis points")
            roles.add(role)
            shares.append(RevenueShare(role, basis_points))
        if not shares or sum(share.basis_points for share in shares) != 10_000:
            raise InvalidInput("revenue shares must total 10000 basis points")
        return tuple(shares)
