from dataclasses import FrozenInstanceError

import pytest

from server.xingjing_admin_finance import (
    Actor,
    ApprovalRequired,
    EntitlementService,
    FinanceService,
    Health,
    InMemoryAuditLog,
    InMemoryLedger,
    ModelService,
    OrderService,
    OrderStatus,
    PermissionDenied,
    RouteRequest,
    UsageEvidence,
    ValidationError,
)


def actor(actor_id: str = "finance", *permissions: str) -> Actor:
    return Actor(actor_id, frozenset(permissions))


def test_model_versions_prices_and_routing_use_published_healthy_snapshot() -> None:
    audit = InMemoryAuditLog()
    service = ModelService(audit)
    operator = actor("ops", "admin.model.manage")
    service.register_model(operator, "req-1", "p1", "m1", {"text-to-video"})
    service.add_version(operator, "req-2", "p1", "m1", "v1")
    price = service.publish_price(operator, "req-3", "p1", "m1", "v1", 7, "credit")
    service.set_health(operator, "req-4", "p1", "m1", "v1", Health.HEALTHY)
    service.publish_version(operator, "req-5", "p1", "m1", "v1")

    route = service.route(RouteRequest("tenant-1", "text-to-video", frozenset({"m1"})))

    assert route.model_id == "m1"
    assert route.version == "v1"
    assert route.price_snapshot == price
    assert len(audit.records()) == 5


def test_router_rejects_unhealthy_or_unentitled_models() -> None:
    service = ModelService(InMemoryAuditLog())
    operator = actor("ops", "admin.model.manage")
    service.register_model(operator, "1", "p1", "m1", {"image"})
    service.add_version(operator, "2", "p1", "m1", "v1")
    service.publish_price(operator, "3", "p1", "m1", "v1", 1, "credit")
    service.publish_version(operator, "4", "p1", "m1", "v1")

    with pytest.raises(LookupError):
        service.route(RouteRequest("t", "image", frozenset({"m1"})))


def test_integer_only_ledger_conserves_hold_settlement_and_release() -> None:
    ledger = InMemoryLedger()
    ledger.credit("t", "seed", 100)
    ledger.hold("t", "task-1", 80, "price-v1")
    ledger.settle("t", "task-1", 55, evidence_id="call-1")
    ledger.release("t", "task-1", 25, evidence_id="call-1")

    account = ledger.account("t")
    assert (account.available, account.held, account.spent) == (45, 0, 55)
    assert ledger.hold_record("t", "task-1").settled + ledger.hold_record("t", "task-1").released == 80
    with pytest.raises(ValidationError):
        ledger.credit("t", "bad", 1.5)  # type: ignore[arg-type]


def test_duplicate_settlement_is_idempotent_and_over_settlement_is_rejected() -> None:
    ledger = InMemoryLedger()
    ledger.credit("t", "seed", 10)
    ledger.hold("t", "task", 10, "p")
    first = ledger.settle("t", "task", 8, "call")
    assert ledger.settle("t", "task", 8, "call") == first
    with pytest.raises(ValidationError):
        ledger.release("t", "task", 3, "different")


def test_refund_and_adjustment_require_separate_approver_and_use_ledger_port() -> None:
    ledger = InMemoryLedger()
    audit = InMemoryAuditLog()
    service = FinanceService(ledger, audit)
    requester = actor("alice", "admin.finance.manage", "admin.finance.approve")
    approver = actor("bob", "admin.finance.approve")
    ledger.credit("t", "order", 100)

    approval = service.request_adjustment(requester, "r1", "t", -30, "correction")
    with pytest.raises(ApprovalRequired):
        service.approve_adjustment(requester, "r2", approval.approval_id)
    service.approve_adjustment(approver, "r3", approval.approval_id)

    assert ledger.account("t").available == 70
    assert [entry.kind for entry in ledger.entries("t")][-1] == "adjustment"


def test_finance_permissions_are_enforced_server_side() -> None:
    service = FinanceService(InMemoryLedger(), InMemoryAuditLog())
    with pytest.raises(PermissionDenied):
        service.request_refund(actor("visitor"), "r", "t", "order-1", 10, "reason")


def test_reconciliation_reports_evidence_level_differences_without_mutating_ledger() -> None:
    ledger = InMemoryLedger()
    service = FinanceService(ledger, InMemoryAuditLog())
    ledger.credit("t", "payment-1", 100)
    before = ledger.entries("t")

    report = service.reconcile("t", {"payment-1": 90, "payment-2": 5})

    assert [(item.reference_id, item.difference) for item in report] == [("payment-1", 10), ("payment-2", -5)]
    assert ledger.entries("t") == before


def test_entitlement_state_machine_and_optimistic_version() -> None:
    service = EntitlementService(InMemoryAuditLog())
    admin = actor("finance", "admin.finance.manage")
    plan = service.create_plan(admin, "r1", "pro", {"models": frozenset({"m1"}), "seats": 3})
    plan = service.activate_plan(admin, "r2", "pro", plan.version)
    grant = service.grant(admin, "r3", "t", "pro")
    service.suspend(admin, "r4", "t", grant.version)

    assert service.entitlement("t").status.value == "suspended"
    with pytest.raises(ValidationError):
        service.activate_plan(admin, "r5", "pro", 0)


def test_audit_records_are_frozen_and_hash_chained() -> None:
    audit = InMemoryAuditLog()
    record = audit.append("r1", "alice", "model", "m1", "create", None, {"enabled": True}, "ok")
    second = audit.append("r2", "alice", "model", "m1", "update", {"enabled": True}, {"enabled": False}, "ok")

    assert second.previous_hash == record.record_hash
    assert audit.verify()
    with pytest.raises(FrozenInstanceError):
        record.action = "tamper"  # type: ignore[misc]


def test_order_payment_callback_is_idempotent_and_does_not_charge_directly() -> None:
    audit = InMemoryAuditLog()
    service = OrderService(audit)
    admin = actor("finance", "admin.finance.manage")
    order = service.create(admin, "r1", "o1", "t", 199)

    paid = service.record_verified_payment("r2", order.order_id, "gateway-callback-1")

    assert paid.status is OrderStatus.PAID
    assert service.record_verified_payment("r3", order.order_id, "gateway-callback-1") == paid


def test_usage_evidence_binds_provider_model_price_and_integer_metering() -> None:
    evidence = UsageEvidence("e1", "t", "task", "provider", "model", "v2", "price-v4", 12, 3, "job-7")
    assert (evidence.price_snapshot_id, evidence.input_units + evidence.output_units) == ("price-v4", 15)
    with pytest.raises(ValidationError):
        UsageEvidence("e2", "t", "task", "p", "m", "v", "price", 1.2, 0, "job")  # type: ignore[arg-type]
