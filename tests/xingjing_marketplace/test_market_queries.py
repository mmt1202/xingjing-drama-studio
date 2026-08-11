from __future__ import annotations

import json
from datetime import UTC, datetime

from server.xingjing_marketplace import (
    ADMIN_BUSINESS_MANAGE,
    COMMUNITY_VIEW,
    TEMPLATE_MANAGE,
    TEMPLATE_VIEW,
    CreateTemplate,
    DecideTemplateReview,
    MarketplaceService,
    MarketQuery,
    RequestContext,
    RevenueShareInput,
    ReviewDecision,
    RightsPolicyInput,
    RightsScope,
    SubmitTemplateReview,
    TemplateKind,
    WithdrawTemplate,
)

from .fakes import FakeMarketplaceRepository

NOW = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)


def context(actor: str, workspace: str, *permissions: str) -> RequestContext:
    return RequestContext(actor, workspace, frozenset(permissions), f"request-{actor}")


def publish_template(
    service: MarketplaceService,
    *,
    workspace: str,
    title: str,
    kind: TemplateKind,
    tags: tuple[str, ...],
    rights: RightsPolicyInput,
    price_minor: int,
    key: str,
) -> str:
    creator = context(f"creator-{workspace}", workspace, TEMPLATE_MANAGE)
    created = service.create_template(
        creator,
        CreateTemplate(
            title=title,
            kind=kind,
            content={"title": title},
            tags=tags,
            rights=rights,
            revenue_shares=(
                RevenueShareInput("template_author", 8_000),
                RevenueShareInput("platform", 2_000),
            ),
            idempotency_key=f"create-{key}",
            price_minor=price_minor,
        ),
    )
    submitted = service.submit_template_review(
        creator,
        SubmitTemplateReview(created.resource_id, 1, "rights checked", f"submit-{key}"),
    )
    service.decide_template_review(
        context(f"admin-{workspace}", workspace, ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(
            submitted.resource_id,
            2,
            ReviewDecision.APPROVE,
            "approved",
            f"approve-{key}",
        ),
    )
    return created.resource_id


def test_market_query_filters_real_records_and_uses_a_stable_tenant_safe_cursor() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    public = RightsPolicyInput.public(
        commercial_use=True,
        attribution_required=True,
        inheritable_scopes=("storyboards",),
    )
    publish_template(
        service,
        workspace="workspace-a",
        title="都市悬疑模板",
        kind=TemplateKind.PROJECT,
        tags=("悬疑", "都市"),
        rights=public,
        price_minor=1_200,
        key="public-project",
    )
    publish_template(
        service,
        workspace="workspace-a",
        title="悬疑分镜模板",
        kind=TemplateKind.STORYBOARD,
        tags=("悬疑",),
        rights=public,
        price_minor=800,
        key="public-storyboard",
    )
    publish_template(
        service,
        workspace="workspace-a",
        title="内部悬疑模板",
        kind=TemplateKind.PROJECT,
        tags=("悬疑",),
        rights=RightsPolicyInput(
            scope=RightsScope.WORKSPACE,
            commercial_use=True,
            attribution_required=False,
            inheritable_scopes=("storyboards",),
        ),
        price_minor=100,
        key="private-project",
    )
    consumer = context("consumer-1", "workspace-b", COMMUNITY_VIEW)

    filtered = service.list_market(
        consumer,
        MarketQuery(
            search="都市",
            kinds=frozenset({TemplateKind.PROJECT}),
            tags=frozenset({"悬疑"}),
            commercial_use=True,
            maximum_price_minor=1_500,
            page_size=10,
        ),
    )
    first = service.list_market(consumer, MarketQuery(tags=frozenset({"悬疑"}), page_size=1))
    second = service.list_market(
        consumer,
        MarketQuery(tags=frozenset({"悬疑"}), page_size=1, cursor=first.next_cursor),
    )

    assert [item.title for item in filtered.items] == ["都市悬疑模板"]
    assert first.total == second.total == 2
    assert first.next_cursor is not None
    assert {first.items[0].id, second.items[0].id} == {
        item.id for item in service.list_market(consumer, MarketQuery(page_size=10)).items
    }
    assert all(item.source_workspace_id == "workspace-a" for item in (*first.items, *second.items))
    json.dumps(filtered.to_dict(), ensure_ascii=False)


def test_withdraw_hides_listing_but_preserves_the_published_history() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    template_id = publish_template(
        service,
        workspace="workspace-a",
        title="可下架模板",
        kind=TemplateKind.PROJECT,
        tags=("历史",),
        rights=RightsPolicyInput.public(
            commercial_use=True,
            attribution_required=True,
            inheritable_scopes=("storyboards",),
        ),
        price_minor=500,
        key="withdrawable",
    )
    consumer = context("consumer-1", "workspace-b", COMMUNITY_VIEW)
    before = service.list_market(consumer, MarketQuery(page_size=10)).items[0]
    command = WithdrawTemplate(template_id, 3, "授权策略调整", "withdraw-1")

    receipt = service.withdraw_template(
        context("admin-workspace-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        command,
    )
    replay = service.withdraw_template(
        context("admin-workspace-a", "workspace-a", ADMIN_BUSINESS_MANAGE),
        command,
    )
    detail = service.get_template(context("reader-a", "workspace-a", TEMPLATE_VIEW), template_id)

    assert replay == receipt
    assert service.list_market(consumer, MarketQuery(page_size=10)).items == ()
    assert detail.publication_state.value == "withdrawn"
    assert detail.published_version_id == before.source_version_id
    assert detail.market_item_id == before.id
    assert detail.reviews[0].status.value == "approved"
