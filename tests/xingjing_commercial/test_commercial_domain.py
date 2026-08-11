from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from server.xingjing_commercial import (
    AcceptanceDecision,
    AccountingRejected,
    Actor,
    CommercialOrder,
    CommercialService,
    DeliveryStatus,
    DisputeStatus,
    IdempotencyConflict,
    MilestoneInput,
    MilestoneStatus,
    OrderNotFound,
    PermissionDenied,
    QuoteStatus,
    SettlementBlocked,
    SettlementStatus,
    ValidationError,
    VersionConflict,
)
from tests.xingjing_commercial.support import (
    FakeAccountingPort,
    FakeDeliveryArtifactVerifier,
    InMemoryCommercialRepository,
)

NOW = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


def make_contracted_order():
    repository = InMemoryCommercialRepository()
    accounting = FakeAccountingPort()
    service = CommercialService(
        repository,
        accounting,
        delivery_artifacts=FakeDeliveryArtifactVerifier(),
        now=lambda: NOW,
    )
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    contractor = Actor.member("contractor", "workspace-contractor", {"commercial.manage", "commercial.view"})
    order = service.publish_order(
        actor=owner,
        title="商业短剧",
        requirements="交付版本必须绑定合同",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片", 10_000, "甲方书面验收"),),
        request_id="publish",
        idempotency_key="publish",
    )
    quoted = service.submit_quote(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        amount_minor=10_000,
        currency="CNY",
        proposal="七日交付",
        valid_until=NOW + timedelta(days=2),
        expected_version=order.version,
        request_id="quote",
        idempotency_key="quote",
    )
    awarded = service.accept_quote(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        quote_id=quoted.quotes[0].id,
        expected_version=quoted.version,
        request_id="accept-quote",
        idempotency_key="accept-quote",
    )
    contracted = service.record_contract_version(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        content_ref="object://contract.pdf",
        content_digest="sha256:contract",
        amount_minor=10_000,
        expected_version=awarded.version,
        request_id="contract",
        idempotency_key="contract",
    )
    return repository, accounting, service, owner, contractor, contracted


def make_accepted_order():
    repository, accounting, service, owner, contractor, contracted = make_contracted_order()
    milestone_id = contracted.milestones[0].id
    delivered = service.submit_delivery(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=contracted.id,
        milestone_id=milestone_id,
        artifact_version_id="final-v1",
        artifact_digest="sha256:final-v1",
        note=None,
        expected_version=contracted.version,
        request_id="delivery",
        idempotency_key="delivery",
    )
    accepted = service.decide_delivery(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=contracted.id,
        delivery_id=delivered.deliveries[0].id,
        decision=AcceptanceDecision.ACCEPTED,
        reason=None,
        evidence_ref="object://acceptance.json",
        expected_version=delivered.version,
        request_id="accept",
        idempotency_key="accept",
    )
    return repository, accounting, service, owner, contractor, accepted


def test_delivery_rejects_an_artifact_not_confirmed_by_the_authoritative_catalog() -> None:
    _, _, service, _, contractor, contracted = make_contracted_order()
    verifier = FakeDeliveryArtifactVerifier(valid=False)
    service.delivery_artifacts = verifier

    with pytest.raises(ValidationError, match="artifact"):
        service.submit_delivery(
            actor=contractor,
            owner_workspace_id=contracted.owner_workspace_id,
            order_id=contracted.id,
            milestone_id=contracted.milestones[0].id,
            artifact_version_id="forged-version",
            artifact_digest="sha256:forged",
            note=None,
            expected_version=contracted.version,
            request_id="delivery-controlled",
            idempotency_key="delivery-controlled",
        )

    assert verifier.calls == [("workspace-contractor", "forged-version", "sha256:forged")]


def test_publish_order_has_stable_ids_idempotency_serialization_and_audit() -> None:
    repository = InMemoryCommercialRepository()
    service = CommercialService(repository, FakeAccountingPort(), now=lambda: NOW)
    actor = Actor.member("owner-1", "workspace-owner", {"commercial.manage", "commercial.view"})
    milestones = (
        MilestoneInput("样片", 4_000, "通过客户审片", NOW + timedelta(days=7)),
        MilestoneInput("成片", 6_000, "交付无水印母版", NOW + timedelta(days=14)),
    )

    published = service.publish_order(
        actor=actor,
        title="十集短剧制作",
        requirements="按冻结剧本和风格版本交付",
        budget_minor=10_000,
        currency="CNY",
        milestones=milestones,
        request_id="request-publish-1",
        idempotency_key="publish-1",
    )
    replay = service.publish_order(
        actor=actor,
        title="十集短剧制作",
        requirements="按冻结剧本和风格版本交付",
        budget_minor=10_000,
        currency="CNY",
        milestones=milestones,
        request_id="request-publish-retry",
        idempotency_key="publish-1",
    )

    assert replay == published
    assert UUID(published.id).version == 7
    assert all(UUID(milestone.id).version == 7 for milestone in published.milestones)
    assert len({milestone.id for milestone in published.milestones}) == 2
    assert sum(milestone.amount_minor for milestone in published.milestones) == published.budget_minor
    assert published.version == 1
    assert json.loads(json.dumps(published.to_dict(), ensure_ascii=False))["id"] == published.id
    assert [event.event_type for event in service.audit_events(actor, "workspace-owner", published.id)] == [
        "commercial.order.published"
    ]

    with pytest.raises(IdempotencyConflict):
        service.publish_order(
            actor=actor,
            title="偷偷改变标题",
            requirements="按冻结剧本和风格版本交付",
            budget_minor=10_000,
            currency="CNY",
            milestones=milestones,
            request_id="request-publish-conflict",
            idempotency_key="publish-1",
        )


def test_order_reads_enforce_permissions_tenant_boundary_and_admin_data_scope() -> None:
    repository = InMemoryCommercialRepository()
    service = CommercialService(repository, FakeAccountingPort(), now=lambda: NOW)
    owner = Actor.member("owner-1", "workspace-owner", {"commercial.manage", "commercial.view"})
    order = service.publish_order(
        actor=owner,
        title="品牌短剧",
        requirements="交付三个版本",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片", 10_000, "客户书面通过"),),
        request_id="request-publish",
        idempotency_key="publish",
    )

    assert service.get_order(owner, "workspace-owner", order.id) == order
    with pytest.raises(PermissionDenied):
        service.get_order(Actor.member("owner-2", "workspace-owner", set()), "workspace-owner", order.id)
    with pytest.raises(OrderNotFound):
        service.get_order(Actor.member("intruder", "workspace-other", {"commercial.view"}), "workspace-owner", order.id)
    with pytest.raises(OrderNotFound):
        service.get_order(
            Actor.admin("admin-1", {"admin.commercial.view"}, {"workspace-other"}), "workspace-owner", order.id
        )

    scoped_admin = Actor.admin("admin-2", {"admin.commercial.view"}, {"workspace-owner"})
    assert service.get_order(scoped_admin, "workspace-owner", order.id) == order


def test_quote_selection_and_contract_versions_are_immutable_and_optimistically_locked() -> None:
    repository = InMemoryCommercialRepository()
    service = CommercialService(repository, FakeAccountingPort(), now=lambda: NOW)
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    contractor = Actor.member("contractor", "workspace-contractor", {"commercial.manage", "commercial.view"})
    order = service.publish_order(
        actor=owner,
        title="商业广告",
        requirements="绑定已冻结脚本",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片", 10_000, "甲方确认"),),
        request_id="publish",
        idempotency_key="publish",
    )

    quoted = service.submit_quote(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        amount_minor=9_000,
        currency="CNY",
        proposal="七日内交付",
        valid_until=NOW + timedelta(days=2),
        expected_version=1,
        request_id="quote",
        idempotency_key="quote-1",
    )
    quote = quoted.quotes[0]
    assert UUID(quote.id).version == 7
    assert service.get_order(contractor, "workspace-owner", order.id) == quoted
    with pytest.raises(VersionConflict):
        service.accept_quote(
            actor=owner,
            owner_workspace_id="workspace-owner",
            order_id=order.id,
            quote_id=quote.id,
            expected_version=1,
            request_id="stale-accept",
            idempotency_key="stale-accept",
        )

    awarded = service.accept_quote(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        quote_id=quote.id,
        expected_version=quoted.version,
        request_id="accept",
        idempotency_key="accept-1",
    )
    assert awarded.quotes[0].status is QuoteStatus.ACCEPTED
    assert awarded.contractor_workspace_id == "workspace-contractor"

    contract_v1 = service.record_contract_version(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        content_ref="object://contracts/contract-v1.pdf",
        content_digest="sha256:contract-v1",
        amount_minor=9_000,
        expected_version=awarded.version,
        request_id="contract-v1",
        idempotency_key="contract-v1",
    )
    contract_v2 = service.record_contract_version(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        content_ref="object://contracts/contract-v2.pdf",
        content_digest="sha256:contract-v2",
        amount_minor=9_000,
        expected_version=contract_v1.version,
        request_id="contract-v2",
        idempotency_key="contract-v2",
    )
    replay = service.record_contract_version(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=order.id,
        content_ref="object://contracts/contract-v2.pdf",
        content_digest="sha256:contract-v2",
        amount_minor=9_000,
        expected_version=contract_v1.version,
        request_id="contract-v2-retry",
        idempotency_key="contract-v2",
    )

    assert replay == contract_v2
    assert [version.sequence for version in contract_v2.contract_versions] == [1, 2]
    assert contract_v2.contract_versions[0].content_digest == "sha256:contract-v1"
    assert contract_v2.active_contract_version_id == contract_v2.contract_versions[1].id


def test_delivery_revisions_rejection_and_acceptance_preserve_versioned_evidence() -> None:
    _, _, service, owner, contractor, contracted = make_contracted_order()
    milestone_id = contracted.milestones[0].id

    delivered_v1 = service.submit_delivery(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=contracted.id,
        milestone_id=milestone_id,
        artifact_version_id="final-video-v1",
        artifact_digest="sha256:video-v1",
        note="首版交付",
        expected_version=contracted.version,
        request_id="delivery-v1",
        idempotency_key="delivery-v1",
    )
    replay = service.submit_delivery(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=contracted.id,
        milestone_id=milestone_id,
        artifact_version_id="final-video-v1",
        artifact_digest="sha256:video-v1",
        note="首版交付",
        expected_version=contracted.version,
        request_id="delivery-v1-retry",
        idempotency_key="delivery-v1",
    )
    assert replay == delivered_v1
    assert delivered_v1.deliveries[0].revision == 1
    assert delivered_v1.deliveries[0].contract_version_id == contracted.active_contract_version_id

    rejected = service.decide_delivery(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=contracted.id,
        delivery_id=delivered_v1.deliveries[0].id,
        decision=AcceptanceDecision.CHANGES_REQUESTED,
        reason="片尾标识不符合合同",
        evidence_ref=None,
        expected_version=delivered_v1.version,
        request_id="reject-v1",
        idempotency_key="reject-v1",
    )
    assert rejected.deliveries[0].status is DeliveryStatus.RETURNED
    assert rejected.milestones[0].status is MilestoneStatus.CHANGES_REQUESTED
    assert rejected.acceptance_records[0].delivery_revision == 1

    delivered_v2 = service.submit_delivery(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=contracted.id,
        milestone_id=milestone_id,
        artifact_version_id="final-video-v2",
        artifact_digest="sha256:video-v2",
        note="修订版",
        expected_version=rejected.version,
        request_id="delivery-v2",
        idempotency_key="delivery-v2",
    )
    accepted = service.decide_delivery(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=contracted.id,
        delivery_id=delivered_v2.deliveries[1].id,
        decision=AcceptanceDecision.ACCEPTED,
        reason=None,
        evidence_ref="object://acceptance/signature.json",
        expected_version=delivered_v2.version,
        request_id="accept-v2",
        idempotency_key="accept-v2",
    )

    assert [delivery.revision for delivery in accepted.deliveries] == [1, 2]
    assert accepted.deliveries[0].artifact_version_id == "final-video-v1"
    assert accepted.deliveries[1].status is DeliveryStatus.ACCEPTED
    assert accepted.milestones[0].status is MilestoneStatus.ACCEPTED
    assert [record.decision for record in accepted.acceptance_records] == [
        AcceptanceDecision.CHANGES_REQUESTED,
        AcceptanceDecision.ACCEPTED,
    ]
    assert accepted.settlements[0].status is SettlementStatus.READY
    assert accepted.settlements[0].amount_minor == 10_000
    assert CommercialOrder.from_dict(json.loads(json.dumps(accepted.to_dict()))) == accepted


def test_dispute_blocks_payment_and_accounting_success_gates_freeze_resume_and_settlement() -> None:
    _, accounting, service, owner, contractor, accepted = make_accepted_order()
    settlement_id = accepted.settlements[0].id
    milestone_id = accepted.milestones[0].id
    disputed = service.open_dispute(
        actor=contractor,
        owner_workspace_id="workspace-owner",
        order_id=accepted.id,
        milestone_id=milestone_id,
        kind="acceptance",
        reason="签收证据主体不一致",
        expected_version=accepted.version,
        request_id="open-dispute",
        idempotency_key="open-dispute",
    )
    assert disputed.disputes[0].status is DisputeStatus.OPEN

    with pytest.raises(SettlementBlocked):
        service.settle(
            actor=owner,
            owner_workspace_id="workspace-owner",
            order_id=accepted.id,
            settlement_id=settlement_id,
            expected_version=disputed.version,
            request_id="blocked-payment",
            idempotency_key="blocked-payment",
        )
    assert accounting.effective_operations("pay") == ()

    accounting.reject_next("freeze")
    with pytest.raises(AccountingRejected):
        service.freeze_settlement(
            actor=owner,
            owner_workspace_id="workspace-owner",
            order_id=accepted.id,
            settlement_id=settlement_id,
            expected_version=disputed.version,
            request_id="freeze-failed",
            idempotency_key="freeze",
        )
    unchanged = service.get_order(owner, "workspace-owner", accepted.id)
    assert unchanged.settlements[0].status is SettlementStatus.READY

    frozen = service.freeze_settlement(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=accepted.id,
        settlement_id=settlement_id,
        expected_version=disputed.version,
        request_id="freeze-retry",
        idempotency_key="freeze",
    )
    assert frozen.settlements[0].status is SettlementStatus.FROZEN
    assert len(accounting.effective_operations("freeze")) == 1

    admin = Actor.admin(
        "finance-admin",
        {"admin.commercial.manage", "admin.commercial.view"},
        {"workspace-owner"},
    )
    resolved = service.resolve_dispute(
        actor=admin,
        owner_workspace_id="workspace-owner",
        order_id=accepted.id,
        dispute_id=frozen.disputes[0].id,
        resolution="签收证据已补正，恢复结算",
        expected_version=frozen.version,
        request_id="resolve",
        idempotency_key="resolve",
    )
    assert resolved.disputes[0].status is DisputeStatus.RESOLVED
    assert resolved.settlements[0].status is SettlementStatus.FROZEN

    resumed = service.resume_settlement(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=accepted.id,
        settlement_id=settlement_id,
        expected_version=resolved.version,
        request_id="resume",
        idempotency_key="resume",
    )
    paid = service.settle(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=accepted.id,
        settlement_id=settlement_id,
        expected_version=resumed.version,
        request_id="pay",
        idempotency_key="pay",
    )

    assert resumed.settlements[0].status is SettlementStatus.READY
    assert paid.settlements[0].status is SettlementStatus.PAID
    assert paid.milestones[0].status is MilestoneStatus.SETTLED
    assert len(accounting.effective_operations("resume")) == 1
    assert len(accounting.effective_operations("pay")) == 1


def test_settlement_retry_recovers_after_local_commit_failure_without_duplicate_payment() -> None:
    repository, accounting, service, owner, _, accepted = make_accepted_order()
    settlement_id = accepted.settlements[0].id
    repository.fail_next_commit()

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        service.settle(
            actor=owner,
            owner_workspace_id="workspace-owner",
            order_id=accepted.id,
            settlement_id=settlement_id,
            expected_version=accepted.version,
            request_id="pay-first-attempt",
            idempotency_key="pay-recovery",
        )

    assert service.get_order(owner, "workspace-owner", accepted.id).settlements[0].status is SettlementStatus.READY
    recovered = service.settle(
        actor=owner,
        owner_workspace_id="workspace-owner",
        order_id=accepted.id,
        settlement_id=settlement_id,
        expected_version=accepted.version,
        request_id="pay-retry",
        idempotency_key="pay-recovery",
    )

    assert recovered.settlements[0].status is SettlementStatus.PAID
    assert len(accounting.effective_operations("pay")) == 1
    assert len([request for request in accounting.calls if request.action == "pay"]) == 2
