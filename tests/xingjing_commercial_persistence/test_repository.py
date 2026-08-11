from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.xingjing_commercial import (
    AcceptanceDecision,
    Actor,
    AuditEvent,
    CommercialService,
    DeliveryStatus,
    DisputeStatus,
    IdempotencyConflict,
    MilestoneInput,
    MilestoneStatus,
    QuoteStatus,
    SettlementStatus,
    VersionConflict,
)
from server.xingjing_commercial.models import CommandRecord, OrderStatus
from server.xingjing_commercial_persistence import (
    CommercialPersistenceBase,
    FailClosedAccountingPort,
    SqlAlchemyCommercialRepository,
)
from tests.xingjing_commercial.support import FakeAccountingPort, FakeDeliveryArtifactVerifier

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
pytestmark = pytest.mark.uses_db


def test_published_order_command_and_audit_survive_new_repository_instance(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'commercial.db'}")
    CommercialPersistenceBase.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    actor = Actor.member("owner-1", "workspace-owner", {"commercial.manage", "commercial.view"})

    service = CommercialService(
        SqlAlchemyCommercialRepository(factory),
        FailClosedAccountingPort(),
        now=lambda: NOW,
        id_factory=iter(("order-1", "milestone-1", "audit-1")).__next__,
    )
    published = service.publish_order(
        actor=actor,
        title="品牌短剧",
        requirements="按冻结合同版本交付",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片", 10_000, "客户书面通过"),),
        request_id="request-publish",
        idempotency_key="publish-1",
    )

    reopened = CommercialService(
        SqlAlchemyCommercialRepository(factory),
        FailClosedAccountingPort(),
        now=lambda: NOW,
    )
    assert reopened.get_order(actor, "workspace-owner", published.id) == published
    assert (
        reopened.publish_order(
            actor=actor,
            title="品牌短剧",
            requirements="按冻结合同版本交付",
            budget_minor=10_000,
            currency="CNY",
            milestones=(MilestoneInput("成片", 10_000, "客户书面通过"),),
            request_id="request-retry",
            idempotency_key="publish-1",
        )
        == published
    )
    assert [event.event_type for event in reopened.audit_events(actor, "workspace-owner", published.id)] == [
        "commercial.order.published"
    ]

    engine.dispose()


def test_order_command_and_audit_roll_back_together(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'rollback.db'}")
    CommercialPersistenceBase.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlAlchemyCommercialRepository(factory)
    actor = Actor.member("owner-1", "workspace-owner", {"commercial.manage", "commercial.view"})
    service = CommercialService(
        repository,
        FailClosedAccountingPort(),
        now=lambda: NOW,
        id_factory=iter(("order-1", "milestone-1", "audit-1")).__next__,
    )
    published = service.publish_order(
        actor=actor,
        title="品牌短剧",
        requirements="按冻结合同版本交付",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片", 10_000, "客户书面通过"),),
        request_id="request-publish",
        idempotency_key="publish-1",
    )
    updated = replace(published, title="不应提交", version=2, updated_at=NOW)

    with pytest.raises(RuntimeError, match="force rollback"):
        with repository.atomic() as uow:
            uow.put_order(updated, expected_version=published.version)
            uow.put_command(
                CommandRecord(
                    "workspace-owner",
                    "commercial.test.rollback",
                    "rollback-1",
                    "fingerprint",
                    "{}",
                    NOW,
                )
            )
            uow.append_audit(
                AuditEvent(
                    event_id="audit-rollback",
                    event_type="commercial.test.rollback",
                    occurred_at=NOW,
                    actor_id=actor.actor_id,
                    workspace_id="workspace-owner",
                    object_type="commercial_order",
                    object_id=published.id,
                    request_id="request-rollback",
                    before=published.to_dict(),
                    after=updated.to_dict(),
                )
            )
            raise RuntimeError("force rollback")

    with repository.atomic() as uow:
        assert uow.get_order("workspace-owner", published.id) == published
        assert uow.get_command("workspace-owner", "commercial.test.rollback", "rollback-1") is None
        assert [event.event_id for event in uow.list_audit("workspace-owner", published.id)] == ["audit-1"]

    engine.dispose()


def test_stale_order_version_cannot_overwrite_committed_state(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'cas.db'}")
    CommercialPersistenceBase.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlAlchemyCommercialRepository(factory)
    actor = Actor.member("owner-1", "workspace-owner", {"commercial.manage", "commercial.view"})
    service = CommercialService(
        repository,
        FailClosedAccountingPort(),
        now=lambda: NOW,
        id_factory=iter(("order-1", "milestone-1", "audit-1")).__next__,
    )
    published = service.publish_order(
        actor=actor,
        title="品牌短剧",
        requirements="按冻结合同版本交付",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片", 10_000, "客户书面通过"),),
        request_id="request-publish",
        idempotency_key="publish-1",
    )

    with repository.atomic() as uow:
        stale = uow.get_order("workspace-owner", published.id)
    assert stale is not None
    committed = replace(stale, title="已提交版本", version=stale.version + 1)
    with repository.atomic() as uow:
        uow.put_order(committed, expected_version=stale.version)

    with pytest.raises(VersionConflict):
        with repository.atomic() as uow:
            uow.put_order(replace(committed, title="未推进版本"), expected_version=committed.version)

    stale_write = replace(stale, title="过期写入", version=stale.version + 1)
    with pytest.raises(VersionConflict):
        with repository.atomic() as uow:
            uow.put_order(stale_write, expected_version=stale.version)

    with repository.atomic() as uow:
        assert uow.get_order("workspace-owner", published.id) == committed

    engine.dispose()


def test_workspace_scope_isolates_orders_commands_and_audit_with_identical_ids(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tenant.db'}")
    CommercialPersistenceBase.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlAlchemyCommercialRepository(factory)

    for workspace_id, title in (("workspace-a", "甲方商单"), ("workspace-b", "乙方商单")):
        actor = Actor.member(f"owner-{workspace_id}", workspace_id, {"commercial.manage", "commercial.view"})
        service = CommercialService(
            repository,
            FailClosedAccountingPort(),
            now=lambda: NOW,
            id_factory=iter(("shared-order", "shared-milestone", "shared-audit")).__next__,
        )
        service.publish_order(
            actor=actor,
            title=title,
            requirements="按冻结合同版本交付",
            budget_minor=10_000,
            currency="CNY",
            milestones=(MilestoneInput("成片", 10_000, "客户书面通过"),),
            request_id="shared-request",
            idempotency_key="shared-command",
        )

    with repository.atomic() as uow:
        order_a = uow.get_order("workspace-a", "shared-order")
        order_b = uow.get_order("workspace-b", "shared-order")
        assert order_a is not None and order_a.title == "甲方商单"
        assert order_b is not None and order_b.title == "乙方商单"
        assert uow.get_order("workspace-c", "shared-order") is None
        assert uow.get_command("workspace-a", "commercial.order.publish", "shared-command") is not None
        assert uow.get_command("workspace-b", "commercial.order.publish", "shared-command") is not None
        assert uow.get_command("workspace-c", "commercial.order.publish", "shared-command") is None
        assert [event.workspace_id for event in uow.list_audit("workspace-a", "shared-order")] == ["workspace-a"]
        assert [event.workspace_id for event in uow.list_audit("workspace-b", "shared-order")] == ["workspace-b"]
        assert uow.list_audit("workspace-c", "shared-order") == ()

    engine.dispose()


def test_full_commercial_lifecycle_round_trips_all_status_and_evidence(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}")
    CommercialPersistenceBase.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlAlchemyCommercialRepository(factory)
    accounting = FakeAccountingPort()
    service = CommercialService(
        repository,
        accounting,
        delivery_artifacts=FakeDeliveryArtifactVerifier(),
        now=lambda: NOW,
    )
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    contractor = Actor.member("contractor", "workspace-contractor", {"commercial.manage", "commercial.view"})

    published = service.publish_order(
        actor=owner,
        title="商业广告",
        requirements="绑定已冻结脚本与成片版本",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片", 10_000, "甲方书面确认"),),
        request_id="publish",
        idempotency_key="publish",
    )
    quoted = service.submit_quote(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        amount_minor=9_000,
        currency="CNY",
        proposal="七日内交付",
        valid_until=NOW + timedelta(days=2),
        expected_version=published.version,
        request_id="quote",
        idempotency_key="quote",
    )
    awarded = service.accept_quote(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        quote_id=quoted.quotes[0].id,
        expected_version=quoted.version,
        request_id="award",
        idempotency_key="award",
    )
    contracted = service.record_contract_version(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        content_ref="object://contracts/v1.pdf",
        content_digest="sha256:contract-v1",
        amount_minor=9_000,
        expected_version=awarded.version,
        request_id="contract",
        idempotency_key="contract",
    )
    delivered_v1 = service.submit_delivery(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        milestone_id=contracted.milestones[0].id,
        artifact_version_id="final-video-v1",
        artifact_digest="sha256:video-v1",
        note="首版",
        expected_version=contracted.version,
        request_id="delivery-v1",
        idempotency_key="delivery-v1",
    )
    returned = service.decide_delivery(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        delivery_id=delivered_v1.deliveries[0].id,
        decision=AcceptanceDecision.CHANGES_REQUESTED,
        reason="片尾标识不符合合同",
        evidence_ref=None,
        expected_version=delivered_v1.version,
        request_id="return-v1",
        idempotency_key="return-v1",
    )
    delivered_v2 = service.submit_delivery(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        milestone_id=contracted.milestones[0].id,
        artifact_version_id="final-video-v2",
        artifact_digest="sha256:video-v2",
        note="修订版",
        expected_version=returned.version,
        request_id="delivery-v2",
        idempotency_key="delivery-v2",
    )
    accepted = service.decide_delivery(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        delivery_id=delivered_v2.deliveries[1].id,
        decision=AcceptanceDecision.ACCEPTED,
        reason=None,
        evidence_ref="object://acceptance/signature.json",
        expected_version=delivered_v2.version,
        request_id="accept-v2",
        idempotency_key="accept-v2",
    )
    disputed = service.open_dispute(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        milestone_id=contracted.milestones[0].id,
        kind="acceptance",
        reason="签收证据主体不一致",
        expected_version=accepted.version,
        request_id="dispute",
        idempotency_key="dispute",
    )
    frozen = service.freeze_settlement(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        settlement_id=accepted.settlements[0].id,
        expected_version=disputed.version,
        request_id="freeze",
        idempotency_key="freeze",
    )
    admin = Actor.admin("finance-admin", {"admin.commercial.manage", "admin.commercial.view"}, {"workspace-owner"})
    resolved = service.resolve_dispute(
        actor=admin,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        dispute_id=frozen.disputes[0].id,
        resolution="签收证据已补正",
        expected_version=frozen.version,
        request_id="resolve",
        idempotency_key="resolve",
    )
    resumed = service.resume_settlement(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        settlement_id=accepted.settlements[0].id,
        expected_version=resolved.version,
        request_id="resume",
        idempotency_key="resume",
    )
    paid = service.settle(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=published.id,
        settlement_id=accepted.settlements[0].id,
        expected_version=resumed.version,
        request_id="pay",
        idempotency_key="pay",
    )

    reopened = CommercialService(SqlAlchemyCommercialRepository(factory), accounting, now=lambda: NOW)
    stored = reopened.get_order(owner, "workspace-owner", published.id)
    assert stored == paid
    assert stored.status is OrderStatus.SETTLED
    assert stored.quotes[0].status is QuoteStatus.ACCEPTED
    assert len(stored.contract_versions) == 1
    assert stored.milestones[0].status is MilestoneStatus.SETTLED
    assert [delivery.status for delivery in stored.deliveries] == [DeliveryStatus.RETURNED, DeliveryStatus.ACCEPTED]
    assert [record.decision for record in stored.acceptance_records] == [
        AcceptanceDecision.CHANGES_REQUESTED,
        AcceptanceDecision.ACCEPTED,
    ]
    assert stored.disputes[0].status is DisputeStatus.RESOLVED
    assert stored.settlements[0].status is SettlementStatus.PAID
    assert len(reopened.audit_events(owner, "workspace-owner", published.id)) == 13

    engine.dispose()


def test_duplicate_command_key_raises_idempotency_conflict_and_rolls_back_other_writes(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'idempotency.db'}")
    CommercialPersistenceBase.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlAlchemyCommercialRepository(factory)
    existing = CommandRecord(
        "workspace-owner",
        "commercial.order.publish",
        "publish-1",
        "fingerprint-a",
        '{"id":"order-a"}',
        NOW,
    )
    with repository.atomic() as uow:
        uow.put_command(existing)

    with pytest.raises(IdempotencyConflict):
        with repository.atomic() as uow:
            uow.append_audit(
                AuditEvent(
                    event_id="audit-should-roll-back",
                    event_type="commercial.test.duplicate",
                    occurred_at=NOW,
                    actor_id="owner",
                    workspace_id="workspace-owner",
                    object_type="commercial_order",
                    object_id="order-b",
                    request_id="duplicate-request",
                    before=None,
                    after=None,
                )
            )
            uow.put_command(
                CommandRecord(
                    "workspace-owner",
                    "commercial.order.publish",
                    "publish-1",
                    "fingerprint-b",
                    '{"id":"order-b"}',
                    NOW,
                )
            )

    with repository.atomic() as uow:
        assert uow.get_command("workspace-owner", "commercial.order.publish", "publish-1") == existing
        assert uow.list_audit("workspace-owner", "order-b") == ()

    engine.dispose()
