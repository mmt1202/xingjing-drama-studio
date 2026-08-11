from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from server.xingjing_marketplace import (
    ADMIN_BUSINESS_MANAGE,
    ADMIN_BUSINESS_VIEW,
    TEMPLATE_MANAGE,
    TEMPLATE_VIEW,
    CreateTemplate,
    CreateTemplateVersion,
    DecideTemplateReview,
    MarketplaceService,
    RequestContext,
    RevenueShareInput,
    ReviewDecision,
    RightsPolicyInput,
    SubmitTemplateReview,
    TemplateKind,
    VersionConflict,
)

from .fakes import FakeMarketplaceRepository

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def creator_context(workspace_id: str = "workspace-a") -> RequestContext:
    return RequestContext(
        actor_id="creator-1",
        workspace_id=workspace_id,
        permissions=frozenset({TEMPLATE_VIEW, TEMPLATE_MANAGE}),
        request_id="request-1",
    )


def admin_context(workspace_id: str = "workspace-a") -> RequestContext:
    return RequestContext(
        actor_id="operator-1",
        workspace_id=workspace_id,
        permissions=frozenset({ADMIN_BUSINESS_VIEW, ADMIN_BUSINESS_MANAGE}),
        request_id="admin-request-1",
    )


def create_project_template(service: MarketplaceService, *, idempotency_key: str = "create-template-1") -> str:
    receipt = service.create_template(
        creator_context(),
        CreateTemplate(
            title="都市悬疑项目模板",
            kind=TemplateKind.PROJECT,
            content={"project_type": "drama", "style": {"palette": "noir"}},
            tags=("悬疑", "都市"),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards", "characters"),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 8_000),
                RevenueShareInput("platform", 2_000),
            ),
            idempotency_key=idempotency_key,
        ),
    )
    return receipt.resource_id


def test_create_template_persists_an_immutable_wire_safe_initial_version() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)

    template_id = create_project_template(service)
    receipt = service.create_template(
        creator_context(),
        CreateTemplate(
            title="都市悬疑项目模板",
            kind=TemplateKind.PROJECT,
            content={"project_type": "drama", "style": {"palette": "noir"}},
            tags=("悬疑", "都市"),
            rights=RightsPolicyInput.public(
                commercial_use=True,
                attribution_required=True,
                inheritable_scopes=("storyboards", "characters"),
            ),
            revenue_shares=(
                RevenueShareInput("template_author", 8_000),
                RevenueShareInput("platform", 2_000),
            ),
            idempotency_key="create-template-1",
        ),
    )

    detail = service.get_template(creator_context(), template_id)

    assert receipt.resource_version == 1
    assert uuid.UUID(detail.id).version == 7
    assert uuid.UUID(detail.versions[0].id).version == 7
    assert detail.versions[0].number == 1
    assert detail.versions[0].content == {
        "project_type": "drama",
        "style": {"palette": "noir"},
    }
    assert json.loads(json.dumps(detail.to_dict(), ensure_ascii=False))["versions"][0]["number"] == 1


def test_edit_appends_an_immutable_version_with_idempotency_and_optimistic_locking() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    template_id = create_project_template(service)
    first = service.get_template(creator_context(), template_id).versions[0]
    command = CreateTemplateVersion(
        template_id=template_id,
        expected_revision=1,
        content={"project_type": "drama", "style": {"palette": "amber"}},
        rights=RightsPolicyInput.public(
            commercial_use=False,
            attribution_required=True,
            inheritable_scopes=("storyboards",),
        ),
        revenue_shares=(
            RevenueShareInput("template_author", 7_500),
            RevenueShareInput("platform", 2_500),
        ),
        idempotency_key="template-version-2",
    )

    receipt = service.create_template_version(creator_context(), command)
    replay = service.create_template_version(creator_context(), command)
    detail = service.get_template(creator_context(), template_id)

    assert replay == receipt
    assert receipt.resource_version == 2
    assert [(version.number, version.content["style"]) for version in detail.versions] == [
        (1, {"palette": "noir"}),
        (2, {"palette": "amber"}),
    ]
    assert detail.versions[0] == first

    with pytest.raises(VersionConflict):
        service.create_template_version(
            creator_context(),
            CreateTemplateVersion(
                template_id=template_id,
                expected_revision=1,
                content={"project_type": "drama", "style": {"palette": "blue"}},
                rights=command.rights,
                revenue_shares=command.revenue_shares,
                idempotency_key="stale-edit",
            ),
        )


def test_review_submission_freezes_and_publishes_the_exact_approved_version() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    template_id = create_project_template(service)
    version_id = service.get_template(creator_context(), template_id).latest_version_id

    submitted = service.submit_template_review(
        creator_context(),
        SubmitTemplateReview(
            template_id=template_id,
            expected_revision=1,
            statement="版权和素材来源已核验",
            idempotency_key="submit-review-1",
        ),
    )
    decision = DecideTemplateReview(
        review_id=submitted.resource_id,
        expected_revision=2,
        decision=ReviewDecision.APPROVE,
        reason="审核证据完整",
        idempotency_key="approve-review-1",
    )

    approved = service.decide_template_review(admin_context(), decision)
    replay = service.decide_template_review(admin_context(), decision)
    detail = service.get_template(creator_context(), template_id)

    assert replay == approved
    assert detail.revision == 3
    assert detail.published_version_id == version_id
    assert detail.publication_state.value == "published"
    assert [(review.id, review.template_version_id, review.status.value) for review in detail.reviews] == [
        (submitted.resource_id, version_id, "approved")
    ]


def test_rejection_stays_unpublished_and_exposes_an_immutable_audit_trail() -> None:
    service = MarketplaceService(FakeMarketplaceRepository(), clock=lambda: NOW)
    template_id = create_project_template(service)
    submitted = service.submit_template_review(
        creator_context(),
        SubmitTemplateReview(template_id, 1, "提交权利证据", "submit-reject-review"),
    )

    service.decide_template_review(
        admin_context(),
        DecideTemplateReview(
            submitted.resource_id,
            2,
            ReviewDecision.REJECT,
            "素材授权范围不完整",
            "reject-review-1",
        ),
    )

    detail = service.get_template(creator_context(), template_id)
    events = service.list_audit_events(admin_context(), subject_id=submitted.resource_id)

    assert detail.review_state.value == "rejected"
    assert detail.publication_state.value == "unpublished"
    assert [event.action for event in events] == [
        "template.review_submitted",
        "template.review_rejected",
    ]
    assert events[-1].request_id == "admin-request-1"
    assert events[-1].result == "succeeded"
    assert json.loads(json.dumps(events[-1].to_dict(), ensure_ascii=False))["after"]["review_state"] == "rejected"
