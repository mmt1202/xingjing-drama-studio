from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from server.xingjing_marketplace import (
    ADMIN_BUSINESS_MANAGE,
    COMMUNITY_MANAGE,
    COMMUNITY_VIEW,
    TEMPLATE_MANAGE,
    TEMPLATE_VIEW,
    CreateFork,
    CreateTemplate,
    CreateTemplateVersion,
    DecideTemplateReview,
    ForkNotAllowed,
    ForkNotFound,
    MarketplaceService,
    MarketQuery,
    PermissionDenied,
    PublishCommunityProject,
    RequestContext,
    RevenueShareInput,
    ReviewDecision,
    RightsPolicyInput,
    RightsScope,
    SubmitTemplateReview,
    TemplateKind,
    TemplateNotFound,
    WithdrawTemplate,
)

from .fakes import FakeMarketplaceRepository, InjectedPersistenceFailure

NOW = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


def context(actor: str, workspace: str, *permissions: str) -> RequestContext:
    return RequestContext(actor, workspace, frozenset(permissions), f"request-{actor}")


def published_template(
    service: MarketplaceService,
    *,
    title: str = "公开项目模板",
    rights: RightsPolicyInput | None = None,
    key: str = "source-template",
) -> tuple[str, str]:
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    source_rights = rights or RightsPolicyInput.public(
        commercial_use=True,
        attribution_required=True,
        inheritable_scopes=("storyboards", "characters"),
    )
    created = service.create_template(
        creator,
        CreateTemplate(
            title=title,
            kind=TemplateKind.PROJECT,
            content={"project": {"mode": "drama"}, "assets": ["character-a"]},
            tags=("公开",),
            rights=source_rights,
            revenue_shares=(
                RevenueShareInput("template_author", 7_000),
                RevenueShareInput("fork_creator", 2_000),
                RevenueShareInput("platform", 1_000),
            ),
            idempotency_key=f"create-{key}",
            price_minor=2_000,
        ),
    )
    submitted = service.submit_template_review(
        creator,
        SubmitTemplateReview(created.resource_id, 1, "rights complete", f"submit-{key}"),
    )
    service.decide_template_review(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(submitted.resource_id, 2, ReviewDecision.APPROVE, "approved", f"approve-{key}"),
    )
    version_id = service.get_template(creator, created.resource_id).published_version_id
    assert version_id is not None
    return created.resource_id, version_id


def test_fork_atomically_freezes_source_rights_revenue_and_target_project_snapshot() -> None:
    repository = FakeMarketplaceRepository()
    service = MarketplaceService(repository, clock=lambda: NOW)
    template_id, version_id = published_template(service)
    consumer = context("consumer-b", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    market_item = service.list_market(consumer, MarketQuery(page_size=10)).items[0]
    command = CreateFork(
        market_item_id=market_item.id,
        expected_market_revision=market_item.revision,
        expected_source_version_id=version_id,
        project_name="悬疑二创项目",
        intended_commercial_use=True,
        requested_inheritable_scopes=("storyboards",),
        idempotency_key="fork-public-template-1",
    )

    receipt = service.create_fork(consumer, command)
    replay = service.create_fork(consumer, command)
    fork = service.get_fork(consumer, receipt.resource_id)
    project = service.get_fork_project(consumer, fork.target_project_id)

    assert replay == receipt
    assert uuid.UUID(fork.id).version == 7
    assert uuid.UUID(fork.target_project_id).version == 7
    assert fork.source_snapshot.source_id == template_id
    assert fork.source_snapshot.source_version_id == version_id
    assert fork.source_snapshot.content == {
        "assets": ["character-a"],
        "project": {"mode": "drama"},
    }
    assert fork.rights_record.granted_scopes == ("storyboards",)
    assert fork.rights_record.source_policy_version_id == market_item.rights.id
    assert fork.revenue_rule.id == market_item.revenue_rule.id
    assert project.source_snapshot_id == fork.source_snapshot.id
    assert project.content_digest == fork.source_snapshot.content_digest
    json.dumps(fork.to_dict(), ensure_ascii=False)

    with pytest.raises(TemplateNotFound):
        service.get_template(context("reader-b", "workspace-b", TEMPLATE_VIEW), template_id)


def test_withdrawal_preserves_existing_fork_snapshot_and_blocks_new_forks() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    template_id, version_id = published_template(service)
    consumer = context("consumer-b", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    market_item = service.list_market(consumer, MarketQuery(page_size=10)).items[0]
    command = CreateFork(
        market_item.id,
        market_item.revision,
        version_id,
        "历史项目",
        True,
        ("storyboards",),
        "historical-fork",
    )
    created = service.create_fork(consumer, command)
    before = service.get_fork(consumer, created.resource_id)

    service.withdraw_template(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        WithdrawTemplate(template_id, 3, "下架但保留历史授权", "withdraw-source"),
    )

    after = service.get_fork(consumer, created.resource_id)
    project = service.get_fork_project(consumer, after.target_project_id)
    records = service.list_forks(consumer)

    assert after == before
    assert project.content == before.source_snapshot.content
    assert records == (before,)
    with pytest.raises(ForkNotAllowed):
        service.create_fork(
            consumer,
            CreateFork(
                market_item.id,
                market_item.revision,
                version_id,
                "下架后的新项目",
                True,
                ("storyboards",),
                "fork-after-withdrawal",
            ),
        )


def test_community_republication_builds_a_frozen_multi_generation_lineage() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    _, source_version_id = published_template(service)
    workspace_b = context("creator-b", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    template_item = service.list_market(workspace_b, MarketQuery(page_size=10)).items[0]
    first_receipt = service.create_fork(
        workspace_b,
        CreateFork(
            template_item.id,
            template_item.revision,
            source_version_id,
            "第一代二创",
            True,
            ("storyboards",),
            "first-generation",
        ),
    )
    first_fork = service.get_fork(workspace_b, first_receipt.resource_id)
    service.publish_community_project(
        workspace_b,
        PublishCommunityProject(
            fork_id=first_fork.id,
            expected_project_revision=1,
            title="第一代公开二创",
            tags=("社区", "二创"),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards",),
            ),
            revenue_shares=(
                RevenueShareInput("source_project", 4_000),
                RevenueShareInput("fork_creator", 5_000),
                RevenueShareInput("platform", 1_000),
            ),
            price_minor=1_000,
            idempotency_key="publish-first-generation",
        ),
    )
    workspace_c = context("creator-c", "workspace-c", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    community_item = service.list_market(
        workspace_c,
        MarketQuery(search="第一代公开", page_size=10),
    ).items[0]

    second_receipt = service.create_fork(
        workspace_c,
        CreateFork(
            community_item.id,
            community_item.revision,
            community_item.source_version_id,
            "第二代二创",
            True,
            ("storyboards",),
            "second-generation",
        ),
    )
    second_fork = service.get_fork(workspace_c, second_receipt.resource_id)
    lineage = service.get_lineage(workspace_c, second_fork.id)

    assert second_fork.parent_fork_id == first_fork.id
    assert second_fork.ancestor_fork_ids == (first_fork.id,)
    assert [node.fork_id for node in lineage] == [first_fork.id]
    assert lineage[0].target_project_id == first_fork.target_project_id
    assert second_fork.source_snapshot.source_kind.value == "community_project"
    assert second_fork.revenue_rule.id == community_item.revenue_rule.id


def test_atomic_failure_rolls_back_fork_project_audit_and_idempotency_for_safe_retry() -> None:
    repository = FakeMarketplaceRepository()
    service = MarketplaceService(repository, clock=lambda: NOW)
    _, source_version_id = published_template(service)
    consumer = context("consumer-b", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    market_item = service.list_market(consumer, MarketQuery(page_size=10)).items[0]
    command = CreateFork(
        market_item.id,
        market_item.revision,
        source_version_id,
        "故障恢复项目",
        True,
        ("storyboards",),
        "recoverable-fork",
    )
    repository.fail_once_on("append_audit")

    with pytest.raises(InjectedPersistenceFailure):
        service.create_fork(consumer, command)

    assert service.list_forks(consumer) == ()
    created = service.create_fork(consumer, command)
    replay = service.create_fork(consumer, command)
    events = service.list_audit_events(
        context("admin-b", "workspace-b", "admin.business.view"),
        subject_id=created.resource_id,
    )

    assert replay == created
    assert len(service.list_forks(consumer)) == 1
    assert [event.action for event in events] == ["fork.created"]


def test_fork_enforces_permission_tenant_commercial_and_inheritance_scope() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    _, private_version = published_template(
        service,
        title="工作区私有模板",
        rights=RightsPolicyInput(
            scope=RightsScope.WORKSPACE,
            commercial_use=True,
            attribution_required=False,
            inheritable_scopes=("storyboards",),
        ),
        key="private",
    )
    owner_view = context("owner-view", "workspace-a", COMMUNITY_VIEW)
    private_item = service.list_market(owner_view, MarketQuery(search="工作区私有", page_size=10)).items[0]
    consumer = context("consumer-b", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    private_command = CreateFork(
        private_item.id,
        private_item.revision,
        private_version,
        "越权项目",
        True,
        ("storyboards",),
        "private-source-fork",
    )

    with pytest.raises(ForkNotAllowed):
        service.create_fork(consumer, private_command)
    with pytest.raises(PermissionDenied):
        service.create_fork(context("viewer-b", "workspace-b", COMMUNITY_VIEW), private_command)

    _, noncommercial_version = published_template(
        service,
        title="仅非商用模板",
        rights=RightsPolicyInput.public(
            commercial_use=False,
            attribution_required=True,
            inheritable_scopes=("storyboards",),
        ),
        key="noncommercial",
    )
    public_item = service.list_market(consumer, MarketQuery(search="仅非商用", page_size=10)).items[0]
    with pytest.raises(ForkNotAllowed):
        service.create_fork(
            consumer,
            CreateFork(
                public_item.id,
                public_item.revision,
                noncommercial_version,
                "违规商用",
                True,
                ("storyboards",),
                "commercial-denied",
            ),
        )
    with pytest.raises(ForkNotAllowed):
        service.create_fork(
            consumer,
            CreateFork(
                public_item.id,
                public_item.revision,
                noncommercial_version,
                "超范围继承",
                False,
                ("characters",),
                "scope-denied",
            ),
        )

    allowed = service.create_fork(
        consumer,
        CreateFork(
            public_item.id,
            public_item.revision,
            noncommercial_version,
            "合法非商用",
            False,
            ("storyboards",),
            "noncommercial-allowed",
        ),
    )
    with pytest.raises(ForkNotFound):
        service.get_fork(context("viewer-c", "workspace-c", COMMUNITY_VIEW), allowed.resource_id)


def test_revenue_rule_versions_change_for_new_forks_without_rewriting_history() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    template_id, first_version_id = published_template(service)
    consumer = context("consumer-b", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    first_item = service.list_market(consumer, MarketQuery(page_size=10)).items[0]
    first_fork_receipt = service.create_fork(
        consumer,
        CreateFork(
            first_item.id,
            first_item.revision,
            first_version_id,
            "旧规则项目",
            True,
            ("storyboards",),
            "old-rule-fork",
        ),
    )
    first_fork = service.get_fork(consumer, first_fork_receipt.resource_id)
    creator = context("creator-a", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW)
    version_receipt = service.create_template_version(
        creator,
        CreateTemplateVersion(
            template_id=template_id,
            expected_revision=3,
            content={"project": {"mode": "drama-v2"}},
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards",),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 5_000),
                RevenueShareInput("fork_creator", 4_000),
                RevenueShareInput("platform", 1_000),
            ),
            idempotency_key="create-version-two",
            price_minor=2_500,
        ),
    )
    submitted = service.submit_template_review(
        creator,
        SubmitTemplateReview(template_id, version_receipt.resource_version, "new rule", "submit-version-two"),
    )
    service.decide_template_review(
        context("admin-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(
            submitted.resource_id,
            5,
            ReviewDecision.APPROVE,
            "new version approved",
            "approve-version-two",
        ),
    )
    second_item = service.list_market(consumer, MarketQuery(page_size=10)).items[0]
    second_fork_receipt = service.create_fork(
        consumer,
        CreateFork(
            second_item.id,
            second_item.revision,
            second_item.source_version_id,
            "新规则项目",
            True,
            ("storyboards",),
            "new-rule-fork",
        ),
    )
    second_fork = service.get_fork(consumer, second_fork_receipt.resource_id)

    assert second_item.id == first_item.id
    assert second_item.revision == first_item.revision + 1
    assert first_fork.revenue_rule.id == first_item.revenue_rule.id
    assert second_fork.revenue_rule.id == second_item.revenue_rule.id
    assert second_fork.revenue_rule.id != first_fork.revenue_rule.id
    assert first_fork.source_snapshot.source_version_id == first_version_id
