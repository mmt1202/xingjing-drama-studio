from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
    MarketplaceService,
    RevenueShareInput,
    ReviewDecision,
    RightsPolicyInput,
    RightsScope,
    SubmitTemplateReview,
    TemplateKind,
    VersionConflict,
)
from server.xingjing_marketplace.domain import RequestContext
from server.xingjing_marketplace_persistence import MarketplaceRepository, metadata

NOW = datetime(2026, 7, 16, tzinfo=UTC)


@pytest.fixture
def repository() -> MarketplaceRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return MarketplaceRepository(sessionmaker(engine, expire_on_commit=False))


def context(actor: str, workspace: str, *permissions: str) -> RequestContext:
    return RequestContext(actor, workspace, frozenset(permissions), "request-1")


def rights() -> RightsPolicyInput:
    return RightsPolicyInput(RightsScope.PUBLIC, True, True, ("project.read",))


def create_template(service: MarketplaceService, workspace: str = "workspace-a") -> str:
    return service.create_template(
        context("creator", workspace, TEMPLATE_MANAGE, TEMPLATE_VIEW, COMMUNITY_MANAGE, COMMUNITY_VIEW),
        CreateTemplate(
            "模板",
            TemplateKind.PROJECT,
            {"style": "ink"},
            ("drama",),
            rights(),
            (RevenueShareInput("creator", 10_000),),
            "create-template",
        ),
    ).resource_id


def test_sqlite_persists_template_versions_audit_and_database_cas(repository: MarketplaceRepository) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    creator = context("creator", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW, COMMUNITY_MANAGE, COMMUNITY_VIEW)
    template_id = create_template(service)
    initial = service.get_template(creator, template_id)

    receipt = service.create_template_version(
        creator,
        CreateTemplateVersion(
            template_id, 1, {"style": "noir"}, rights(), (RevenueShareInput("creator", 10_000),), "v2"
        ),
    )

    assert receipt.resource_version == 2
    assert [item.number for item in service.get_template(creator, template_id).versions] == [1, 2]
    assert [event.action for event in repository.list_audit_events("workspace-a")] == [
        "template.created",
        "template.version_created",
    ]
    with pytest.raises(VersionConflict):
        repository.atomic(lambda tx: tx.save_template(initial, expected_revision=1))


def test_sqlite_rolls_back_fork_snapshot_audit_and_idempotency(repository: MarketplaceRepository) -> None:
    service = MarketplaceService(repository, clock=lambda: NOW)
    creator = context("creator", "workspace-a", TEMPLATE_MANAGE, TEMPLATE_VIEW, COMMUNITY_MANAGE, COMMUNITY_VIEW)
    template_id = create_template(service)
    submitted = service.submit_template_review(creator, SubmitTemplateReview(template_id, 1, "rights", "submit"))
    service.decide_template_review(
        context("reviewer", "workspace-a", ADMIN_BUSINESS_MANAGE),
        DecideTemplateReview(submitted.resource_id, 2, ReviewDecision.APPROVE, "approved", "approve"),
    )
    item = service.list_market(
        context("consumer", "workspace-b", COMMUNITY_VIEW),
        __import__("server.xingjing_marketplace", fromlist=["MarketQuery"]).MarketQuery(),
    ).items[0]
    command = CreateFork(item.id, item.revision, item.source_version_id, "forked", True, ("project.read",), "fork")

    consumer = context("consumer", "workspace-b", COMMUNITY_VIEW, COMMUNITY_MANAGE)
    created = service.create_fork(consumer, command)
    assert service.get_fork(consumer, created.resource_id).target_project_id
    assert (
        repository.get_fork_project("workspace-b", service.get_fork(consumer, created.resource_id).target_project_id)
        is not None
    )
