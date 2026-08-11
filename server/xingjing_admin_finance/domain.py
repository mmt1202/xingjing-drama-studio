from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol


class DomainError(Exception):
    pass


class ValidationError(DomainError):
    pass


class PermissionDenied(DomainError):
    pass


class ApprovalRequired(DomainError):
    pass


def units(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("amount must be a non-negative integer in the smallest unit")
    return value


@dataclass(frozen=True)
class Actor:
    actor_id: str
    permissions: frozenset[str] = frozenset()

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PermissionDenied(permission)


@dataclass(frozen=True)
class AuditRecord:
    request_id: str
    actor_id: str
    object_type: str
    object_id: str
    action: str
    before: Any
    after: Any
    result: str
    previous_hash: str
    record_hash: str


class AuditPort(Protocol):
    def append(
        self,
        request_id: str,
        actor_id: str,
        object_type: str,
        object_id: str,
        action: str,
        before: Any,
        after: Any,
        result: str,
    ) -> AuditRecord: ...


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def append(
        self,
        request_id: str,
        actor_id: str,
        object_type: str,
        object_id: str,
        action: str,
        before: Any,
        after: Any,
        result: str,
    ) -> AuditRecord:
        previous = self._records[-1].record_hash if self._records else "0" * 64
        payload = json.dumps(
            [request_id, actor_id, object_type, object_id, action, before, after, result, previous],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        record = AuditRecord(
            request_id,
            actor_id,
            object_type,
            object_id,
            action,
            before,
            after,
            result,
            previous,
            sha256(payload.encode()).hexdigest(),
        )
        self._records.append(record)
        return record

    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def verify(self) -> bool:
        previous = "0" * 64
        for record in self._records:
            payload = json.dumps(
                [
                    record.request_id,
                    record.actor_id,
                    record.object_type,
                    record.object_id,
                    record.action,
                    record.before,
                    record.after,
                    record.result,
                    previous,
                ],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if record.previous_hash != previous or record.record_hash != sha256(payload.encode()).hexdigest():
                return False
            previous = record.record_hash
        return True


class Health(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class PriceSnapshot:
    price_id: str
    provider_id: str
    model_id: str
    version: str
    amount: int
    unit: str


@dataclass
class ModelVersion:
    version: str
    published: bool = False
    health: Health = Health.UNKNOWN
    price: PriceSnapshot | None = None


@dataclass
class ModelDefinition:
    provider_id: str
    model_id: str
    capabilities: frozenset[str]
    versions: dict[str, ModelVersion] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteRequest:
    tenant_id: str
    capability: str
    entitled_models: frozenset[str]


@dataclass(frozen=True)
class RouteDecision:
    provider_id: str
    model_id: str
    version: str
    price_snapshot: PriceSnapshot


class ModelService:
    def __init__(self, audit: AuditPort) -> None:
        self._audit = audit
        self._models: dict[tuple[str, str], ModelDefinition] = {}

    def register_model(
        self, actor: Actor, request_id: str, provider_id: str, model_id: str, capabilities: set[str]
    ) -> ModelDefinition:
        actor.require("admin.model.manage")
        model = ModelDefinition(provider_id, model_id, frozenset(capabilities))
        self._models[(provider_id, model_id)] = model
        self._audit.append(
            request_id,
            actor.actor_id,
            "model",
            model_id,
            "register",
            None,
            {"provider": provider_id, "capabilities": sorted(capabilities)},
            "ok",
        )
        return model

    def add_version(self, actor: Actor, request_id: str, provider_id: str, model_id: str, version: str) -> ModelVersion:
        actor.require("admin.model.manage")
        item = ModelVersion(version)
        self._models[(provider_id, model_id)].versions[version] = item
        self._audit.append(
            request_id,
            actor.actor_id,
            "model_version",
            f"{model_id}:{version}",
            "create",
            None,
            {"published": False},
            "ok",
        )
        return item

    def publish_price(
        self, actor: Actor, request_id: str, provider_id: str, model_id: str, version: str, amount: int, unit: str
    ) -> PriceSnapshot:
        actor.require("admin.model.manage")
        amount = units(amount)
        snapshot = PriceSnapshot(request_id, provider_id, model_id, version, amount, unit)
        self._models[(provider_id, model_id)].versions[version].price = snapshot
        self._audit.append(
            request_id, actor.actor_id, "price", request_id, "publish", None, {"amount": amount, "unit": unit}, "ok"
        )
        return snapshot

    def set_health(
        self, actor: Actor, request_id: str, provider_id: str, model_id: str, version: str, health: Health
    ) -> None:
        actor.require("admin.model.manage")
        item = self._models[(provider_id, model_id)].versions[version]
        before = item.health.value
        item.health = health
        self._audit.append(
            request_id, actor.actor_id, "model_health", f"{model_id}:{version}", "set", before, health.value, "ok"
        )

    def publish_version(self, actor: Actor, request_id: str, provider_id: str, model_id: str, version: str) -> None:
        actor.require("admin.model.manage")
        item = self._models[(provider_id, model_id)].versions[version]
        if item.price is None:
            raise ValidationError("a version requires a price snapshot before publication")
        item.published = True
        self._audit.append(
            request_id,
            actor.actor_id,
            "model_version",
            f"{model_id}:{version}",
            "publish",
            {"published": False},
            {"published": True},
            "ok",
        )

    def route(self, request: RouteRequest) -> RouteDecision:
        candidates: list[tuple[int, str, str, PriceSnapshot]] = []
        for model in self._models.values():
            if request.capability not in model.capabilities or model.model_id not in request.entitled_models:
                continue
            for version in model.versions.values():
                if version.published and version.health is Health.HEALTHY and version.price is not None:
                    candidates.append((version.price.amount, model.provider_id, model.model_id, version.price))
        if not candidates:
            raise LookupError("no healthy, published and entitled model route")
        _, provider_id, model_id, price = min(candidates)
        return RouteDecision(provider_id, model_id, price.version, price)


@dataclass(frozen=True)
class Account:
    tenant_id: str
    available: int
    held: int
    spent: int


@dataclass(frozen=True)
class LedgerEntry:
    tenant_id: str
    reference_id: str
    kind: str
    amount: int
    evidence_id: str | None = None


@dataclass
class HoldRecord:
    amount: int
    price_snapshot_id: str
    settled: int = 0
    released: int = 0
    evidence_results: dict[tuple[str, str], LedgerEntry] = field(default_factory=dict)


class LedgerPort(Protocol):
    def post_adjustment(self, tenant_id: str, reference_id: str, amount: int) -> LedgerEntry: ...
    def post_refund(self, tenant_id: str, reference_id: str, amount: int) -> LedgerEntry: ...


class InMemoryLedger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._holds: dict[tuple[str, str], HoldRecord] = {}

    def entries(self, tenant_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(item for item in self._entries if item.tenant_id == tenant_id)

    def account(self, tenant_id: str) -> Account:
        available = sum(e.amount for e in self.entries(tenant_id) if e.kind in {"credit", "refund", "adjustment"})
        holds = [h for (tenant, _), h in self._holds.items() if tenant == tenant_id]
        held = sum(h.amount - h.settled - h.released for h in holds)
        spent = sum(h.settled for h in holds)
        return Account(tenant_id, available - sum(h.amount - h.released for h in holds), held, spent)

    def credit(self, tenant_id: str, reference_id: str, amount: int) -> LedgerEntry:
        return self._append(tenant_id, reference_id, "credit", units(amount))

    def hold(self, tenant_id: str, reference_id: str, amount: int, price_snapshot_id: str) -> HoldRecord:
        amount = units(amount)
        if self.account(tenant_id).available < amount:
            raise ValidationError("insufficient available units")
        key = (tenant_id, reference_id)
        if key in self._holds:
            return self._holds[key]
        self._holds[key] = HoldRecord(amount, price_snapshot_id)
        return self._holds[key]

    def hold_record(self, tenant_id: str, reference_id: str) -> HoldRecord:
        return self._holds[(tenant_id, reference_id)]

    def settle(self, tenant_id: str, reference_id: str, amount: int, evidence_id: str) -> LedgerEntry:
        return self._resolve_hold(tenant_id, reference_id, "settlement", amount, evidence_id)

    def release(self, tenant_id: str, reference_id: str, amount: int, evidence_id: str) -> LedgerEntry:
        return self._resolve_hold(tenant_id, reference_id, "release", amount, evidence_id)

    def _resolve_hold(self, tenant_id: str, reference_id: str, kind: str, amount: int, evidence_id: str) -> LedgerEntry:
        amount = units(amount)
        hold = self.hold_record(tenant_id, reference_id)
        idempotency = (kind, evidence_id)
        if idempotency in hold.evidence_results:
            entry = hold.evidence_results[idempotency]
            if entry.amount != amount:
                raise ValidationError("idempotency evidence reused with a different amount")
            return entry
        if hold.settled + hold.released + amount > hold.amount:
            raise ValidationError("settled plus released cannot exceed the hold")
        entry = self._append(tenant_id, reference_id, kind, amount, evidence_id)
        if kind == "settlement":
            hold.settled += amount
        else:
            hold.released += amount
        hold.evidence_results[idempotency] = entry
        return entry

    def post_adjustment(self, tenant_id: str, reference_id: str, amount: object) -> LedgerEntry:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
            raise ValidationError("adjustment must be a non-zero integer")
        if amount < 0 and self.account(tenant_id).available < -amount:
            raise ValidationError("adjustment would make available units negative")
        return self._append(tenant_id, reference_id, "adjustment", amount)

    def post_refund(self, tenant_id: str, reference_id: str, amount: int) -> LedgerEntry:
        return self._append(tenant_id, reference_id, "refund", units(amount))

    def _append(
        self, tenant_id: str, reference_id: str, kind: str, amount: int, evidence_id: str | None = None
    ) -> LedgerEntry:
        entry = LedgerEntry(tenant_id, reference_id, kind, amount, evidence_id)
        self._entries.append(entry)
        return entry


@dataclass(frozen=True)
class Approval:
    approval_id: str
    requester_id: str
    tenant_id: str
    kind: str
    amount: int
    reason: str
    reference_id: str | None = None
    status: str = "pending"


@dataclass(frozen=True)
class ReconciliationDifference:
    reference_id: str
    internal_amount: int
    external_amount: int
    difference: int


class FinanceService:
    def __init__(self, ledger: LedgerPort, audit: AuditPort) -> None:
        self._ledger = ledger
        self._audit = audit
        self._approvals: dict[str, Approval] = {}

    def request_adjustment(
        self, actor: Actor, request_id: str, tenant_id: str, amount: object, reason: str
    ) -> Approval:
        actor.require("admin.finance.manage")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
            raise ValidationError("adjustment must be a non-zero integer")
        return self._request(actor, request_id, tenant_id, "adjustment", amount, reason)

    def request_refund(
        self, actor: Actor, request_id: str, tenant_id: str, order_id: str, amount: int, reason: str
    ) -> Approval:
        actor.require("admin.finance.manage")
        return self._request(actor, request_id, tenant_id, "refund", units(amount), reason, order_id)

    def _request(
        self,
        actor: Actor,
        request_id: str,
        tenant_id: str,
        kind: str,
        amount: int,
        reason: str,
        reference_id: str | None = None,
    ) -> Approval:
        approval = Approval(request_id, actor.actor_id, tenant_id, kind, amount, reason, reference_id)
        self._approvals[request_id] = approval
        self._audit.append(
            request_id,
            actor.actor_id,
            "approval",
            request_id,
            "request",
            None,
            {"kind": kind, "amount": amount, "reason": reason},
            "pending",
        )
        return approval

    def approve_adjustment(self, actor: Actor, request_id: str, approval_id: str) -> Approval:
        return self._approve(actor, request_id, approval_id)

    def approve_refund(self, actor: Actor, request_id: str, approval_id: str) -> Approval:
        return self._approve(actor, request_id, approval_id)

    def _approve(self, actor: Actor, request_id: str, approval_id: str) -> Approval:
        actor.require("admin.finance.approve")
        approval = self._approvals[approval_id]
        if actor.actor_id == approval.requester_id:
            raise ApprovalRequired("requester cannot approve their own financial action")
        if approval.status != "pending":
            return approval
        reference = approval.reference_id or approval.approval_id
        if approval.kind == "adjustment":
            self._ledger.post_adjustment(approval.tenant_id, reference, approval.amount)
        else:
            self._ledger.post_refund(approval.tenant_id, reference, approval.amount)
        completed = replace(approval, status="approved")
        self._approvals[approval_id] = completed
        self._audit.append(
            request_id,
            actor.actor_id,
            "approval",
            approval_id,
            "approve",
            {"status": "pending"},
            {"status": "approved"},
            "ok",
        )
        return completed

    def reconcile(self, tenant_id: str, external: dict[str, int]) -> tuple[ReconciliationDifference, ...]:
        internal: dict[str, int] = {}
        entries = getattr(self._ledger, "entries")(tenant_id)
        for entry in entries:
            internal[entry.reference_id] = internal.get(entry.reference_id, 0) + entry.amount
        differences = []
        for reference in sorted(internal.keys() | external.keys()):
            ours, theirs = internal.get(reference, 0), external.get(reference, 0)
            if ours != theirs:
                differences.append(ReconciliationDifference(reference, ours, theirs, ours - theirs))
        return tuple(differences)


class PlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class EntitlementStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class Plan:
    plan_id: str
    benefits: dict[str, Any]
    status: PlanStatus = PlanStatus.DRAFT
    version: int = 1


@dataclass(frozen=True)
class Entitlement:
    tenant_id: str
    plan_id: str
    status: EntitlementStatus = EntitlementStatus.ACTIVE
    version: int = 1


class EntitlementService:
    def __init__(self, audit: AuditPort) -> None:
        self._audit = audit
        self._plans: dict[str, Plan] = {}
        self._grants: dict[str, Entitlement] = {}

    def create_plan(self, actor: Actor, request_id: str, plan_id: str, benefits: dict[str, Any]) -> Plan:
        actor.require("admin.finance.manage")
        plan = Plan(plan_id, benefits)
        self._plans[plan_id] = plan
        self._audit.append(
            request_id,
            actor.actor_id,
            "plan",
            plan_id,
            "create",
            None,
            {"status": plan.status.value, "version": plan.version},
            "ok",
        )
        return plan

    def activate_plan(self, actor: Actor, request_id: str, plan_id: str, expected_version: int) -> Plan:
        actor.require("admin.finance.manage")
        plan = self._plans[plan_id]
        if plan.version != expected_version or plan.status is not PlanStatus.DRAFT:
            raise ValidationError("VERSION_CONFLICT or invalid plan transition")
        updated = replace(plan, status=PlanStatus.ACTIVE, version=plan.version + 1)
        self._plans[plan_id] = updated
        self._audit.append(
            request_id,
            actor.actor_id,
            "plan",
            plan_id,
            "activate",
            {"status": plan.status.value},
            {"status": updated.status.value},
            "ok",
        )
        return updated

    def grant(self, actor: Actor, request_id: str, tenant_id: str, plan_id: str) -> Entitlement:
        actor.require("admin.finance.manage")
        if self._plans[plan_id].status is not PlanStatus.ACTIVE:
            raise ValidationError("only active plans can be granted")
        grant = Entitlement(tenant_id, plan_id)
        self._grants[tenant_id] = grant
        self._audit.append(
            request_id,
            actor.actor_id,
            "entitlement",
            tenant_id,
            "grant",
            None,
            {"plan": plan_id, "status": grant.status.value},
            "ok",
        )
        return grant

    def suspend(self, actor: Actor, request_id: str, tenant_id: str, expected_version: int) -> Entitlement:
        actor.require("admin.finance.manage")
        grant = self._grants[tenant_id]
        if grant.version != expected_version or grant.status is not EntitlementStatus.ACTIVE:
            raise ValidationError("VERSION_CONFLICT or invalid entitlement transition")
        updated = replace(grant, status=EntitlementStatus.SUSPENDED, version=grant.version + 1)
        self._grants[tenant_id] = updated
        self._audit.append(
            request_id,
            actor.actor_id,
            "entitlement",
            tenant_id,
            "suspend",
            {"status": grant.status.value},
            {"status": updated.status.value},
            "ok",
        )
        return updated

    def entitlement(self, tenant_id: str) -> Entitlement:
        return self._grants[tenant_id]


@dataclass(frozen=True)
class UsageEvidence:
    evidence_id: str
    tenant_id: str
    task_id: str
    provider_id: str
    model_id: str
    model_version: str
    price_snapshot_id: str
    input_units: int
    output_units: int
    provider_job_id: str

    def __post_init__(self) -> None:
        units(self.input_units)
        units(self.output_units)


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CLOSED = "closed"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"


@dataclass(frozen=True)
class Order:
    order_id: str
    tenant_id: str
    amount: int
    status: OrderStatus = OrderStatus.PENDING
    version: int = 1
    payment_reference: str | None = None


class PaymentPort(Protocol):
    """外部支付边界；本包刻意不提供真实收款适配器。"""

    def verify_callback(self, payload: bytes, signature: str) -> str: ...


class OrderService:
    def __init__(self, audit: AuditPort) -> None:
        self._audit = audit
        self._orders: dict[str, Order] = {}
        self._callbacks: dict[str, Order] = {}

    def create(self, actor: Actor, request_id: str, order_id: str, tenant_id: str, amount: int) -> Order:
        actor.require("admin.finance.manage")
        order = Order(order_id, tenant_id, units(amount))
        self._orders[order_id] = order
        self._audit.append(
            request_id,
            actor.actor_id,
            "order",
            order_id,
            "create",
            None,
            {"amount": amount, "status": order.status.value},
            "ok",
        )
        return order

    def record_verified_payment(self, request_id: str, order_id: str, payment_reference: str) -> Order:
        """只接收支付端口已验签的引用，不发起真实收款。"""
        if payment_reference in self._callbacks:
            return self._callbacks[payment_reference]
        order = self._orders[order_id]
        if order.status is not OrderStatus.PENDING:
            raise ValidationError("invalid order transition")
        paid = replace(order, status=OrderStatus.PAID, version=order.version + 1, payment_reference=payment_reference)
        self._orders[order_id] = paid
        self._callbacks[payment_reference] = paid
        self._audit.append(
            request_id,
            "payment-port",
            "order",
            order_id,
            "payment_callback",
            {"status": order.status.value},
            {"status": paid.status.value},
            "ok",
        )
        return paid

    def close(self, actor: Actor, request_id: str, order_id: str, expected_version: int) -> Order:
        actor.require("admin.finance.manage")
        order = self._orders[order_id]
        if order.version != expected_version or order.status is not OrderStatus.PENDING:
            raise ValidationError("VERSION_CONFLICT or invalid order transition")
        closed = replace(order, status=OrderStatus.CLOSED, version=order.version + 1)
        self._orders[order_id] = closed
        self._audit.append(
            request_id,
            actor.actor_id,
            "order",
            order_id,
            "close",
            {"status": order.status.value},
            {"status": closed.status.value},
            "ok",
        )
        return closed
