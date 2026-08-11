from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, cast

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


def _json_value(value: object) -> JsonValue:
    if value is None or type(value) in (str, int, bool):
        return cast(JsonScalar, value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, frozenset)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


class SerializableModel:
    def to_dict(self) -> JsonObject:
        value = _json_value(asdict(self))  # type: ignore[arg-type]
        if not isinstance(value, dict):
            raise TypeError("serialized model must be a JSON object")
        return value


class OrderStatus(StrEnum):
    PUBLISHED = "published"
    AWARDED = "awarded"
    CONTRACTED = "contracted"
    IN_DELIVERY = "in_delivery"
    ACCEPTED = "accepted"
    SETTLED = "settled"


class QuoteStatus(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class MilestoneStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    CHANGES_REQUESTED = "changes_requested"
    ACCEPTED = "accepted"
    SETTLED = "settled"


class DeliveryStatus(StrEnum):
    SUBMITTED = "submitted"
    RETURNED = "returned"
    ACCEPTED = "accepted"


class AcceptanceDecision(StrEnum):
    ACCEPTED = "accepted"
    CHANGES_REQUESTED = "changes_requested"


class SettlementStatus(StrEnum):
    READY = "ready"
    FROZEN = "frozen"
    PAID = "paid"


class DisputeStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    workspace_id: str
    permissions: frozenset[str]
    data_scope_workspace_ids: frozenset[str] = frozenset()

    @classmethod
    def member(cls, actor_id: str, workspace_id: str, permissions: set[str]) -> Actor:
        return cls(actor_id.strip(), workspace_id.strip(), frozenset(permissions))

    @classmethod
    def admin(cls, actor_id: str, permissions: set[str], data_scope_workspace_ids: set[str]) -> Actor:
        return cls(actor_id.strip(), "platform", frozenset(permissions), frozenset(data_scope_workspace_ids))


@dataclass(frozen=True, slots=True)
class MilestoneInput(SerializableModel):
    title: str
    amount_minor: int
    acceptance_criteria: str
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CommercialMilestone(SerializableModel):
    id: str
    title: str
    amount_minor: int
    acceptance_criteria: str
    due_at: datetime | None
    status: MilestoneStatus = MilestoneStatus.PENDING
    version: int = 1

    @classmethod
    def from_dict(cls, value: JsonObject) -> CommercialMilestone:
        due_at = value.get("due_at")
        return cls(
            id=str(value["id"]),
            title=str(value["title"]),
            amount_minor=int(cast(int, value["amount_minor"])),
            acceptance_criteria=str(value["acceptance_criteria"]),
            due_at=datetime.fromisoformat(due_at) if isinstance(due_at, str) else None,
            status=MilestoneStatus(str(value["status"])),
            version=int(cast(int, value["version"])),
        )


@dataclass(frozen=True, slots=True)
class CommercialQuote(SerializableModel):
    id: str
    contractor_workspace_id: str
    submitted_by: str
    amount_minor: int
    currency: str
    proposal: str
    valid_until: datetime
    status: QuoteStatus
    version: int
    created_at: datetime

    @classmethod
    def from_dict(cls, value: JsonObject) -> CommercialQuote:
        return cls(
            id=str(value["id"]),
            contractor_workspace_id=str(value["contractor_workspace_id"]),
            submitted_by=str(value["submitted_by"]),
            amount_minor=int(cast(int, value["amount_minor"])),
            currency=str(value["currency"]),
            proposal=str(value["proposal"]),
            valid_until=datetime.fromisoformat(str(value["valid_until"])),
            status=QuoteStatus(str(value["status"])),
            version=int(cast(int, value["version"])),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )


@dataclass(frozen=True, slots=True)
class ContractVersion(SerializableModel):
    id: str
    sequence: int
    quote_id: str
    content_ref: str
    content_digest: str
    amount_minor: int
    currency: str
    created_by: str
    created_at: datetime

    @classmethod
    def from_dict(cls, value: JsonObject) -> ContractVersion:
        return cls(
            id=str(value["id"]),
            sequence=int(cast(int, value["sequence"])),
            quote_id=str(value["quote_id"]),
            content_ref=str(value["content_ref"]),
            content_digest=str(value["content_digest"]),
            amount_minor=int(cast(int, value["amount_minor"])),
            currency=str(value["currency"]),
            created_by=str(value["created_by"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )


@dataclass(frozen=True, slots=True)
class CommercialDelivery(SerializableModel):
    id: str
    milestone_id: str
    revision: int
    contract_version_id: str
    artifact_version_id: str
    artifact_digest: str
    note: str | None
    submitted_by: str
    status: DeliveryStatus
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, value: JsonObject) -> CommercialDelivery:
        return cls(
            id=str(value["id"]),
            milestone_id=str(value["milestone_id"]),
            revision=int(cast(int, value["revision"])),
            contract_version_id=str(value["contract_version_id"]),
            artifact_version_id=str(value["artifact_version_id"]),
            artifact_digest=str(value["artifact_digest"]),
            note=str(value["note"]) if value.get("note") is not None else None,
            submitted_by=str(value["submitted_by"]),
            status=DeliveryStatus(str(value["status"])),
            version=int(cast(int, value["version"])),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
        )


@dataclass(frozen=True, slots=True)
class AcceptanceRecord(SerializableModel):
    id: str
    milestone_id: str
    delivery_id: str
    delivery_revision: int
    decision: AcceptanceDecision
    reason: str | None
    evidence_ref: str | None
    decided_by: str
    created_at: datetime

    @classmethod
    def from_dict(cls, value: JsonObject) -> AcceptanceRecord:
        return cls(
            id=str(value["id"]),
            milestone_id=str(value["milestone_id"]),
            delivery_id=str(value["delivery_id"]),
            delivery_revision=int(cast(int, value["delivery_revision"])),
            decision=AcceptanceDecision(str(value["decision"])),
            reason=str(value["reason"]) if value.get("reason") is not None else None,
            evidence_ref=str(value["evidence_ref"]) if value.get("evidence_ref") is not None else None,
            decided_by=str(value["decided_by"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )


@dataclass(frozen=True, slots=True)
class Settlement(SerializableModel):
    id: str
    milestone_id: str
    payee_workspace_id: str
    amount_minor: int
    currency: str
    status: SettlementStatus
    version: int
    accounting_operation_id: str | None
    accounting_receipt_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, value: JsonObject) -> Settlement:
        return cls(
            id=str(value["id"]),
            milestone_id=str(value["milestone_id"]),
            payee_workspace_id=str(value["payee_workspace_id"]),
            amount_minor=int(cast(int, value["amount_minor"])),
            currency=str(value["currency"]),
            status=SettlementStatus(str(value["status"])),
            version=int(cast(int, value["version"])),
            accounting_operation_id=(
                str(value["accounting_operation_id"]) if value.get("accounting_operation_id") is not None else None
            ),
            accounting_receipt_id=(
                str(value["accounting_receipt_id"]) if value.get("accounting_receipt_id") is not None else None
            ),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
        )


@dataclass(frozen=True, slots=True)
class CommercialDispute(SerializableModel):
    id: str
    milestone_id: str
    kind: str
    reason: str
    opened_by: str
    opened_by_workspace_id: str
    status: DisputeStatus
    resolution: str | None
    resolved_by: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, value: JsonObject) -> CommercialDispute:
        return cls(
            id=str(value["id"]),
            milestone_id=str(value["milestone_id"]),
            kind=str(value["kind"]),
            reason=str(value["reason"]),
            opened_by=str(value["opened_by"]),
            opened_by_workspace_id=str(value["opened_by_workspace_id"]),
            status=DisputeStatus(str(value["status"])),
            resolution=str(value["resolution"]) if value.get("resolution") is not None else None,
            resolved_by=str(value["resolved_by"]) if value.get("resolved_by") is not None else None,
            version=int(cast(int, value["version"])),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
        )


type AccountingAction = Literal["freeze", "resume", "pay"]


@dataclass(frozen=True, slots=True)
class AccountingRequest(SerializableModel):
    action: AccountingAction
    operation_id: str
    owner_workspace_id: str
    payee_workspace_id: str
    order_id: str
    milestone_id: str
    settlement_id: str
    amount_minor: int
    currency: str
    requested_by: str
    request_id: str


@dataclass(frozen=True, slots=True)
class AccountingReceipt(SerializableModel):
    operation_id: str
    receipt_id: str
    succeeded: bool
    occurred_at: datetime
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class CommercialOrder(SerializableModel):
    id: str
    owner_workspace_id: str
    title: str
    requirements: str
    budget_minor: int
    currency: str
    milestones: tuple[CommercialMilestone, ...]
    status: OrderStatus
    version: int
    created_at: datetime
    updated_at: datetime
    contractor_workspace_id: str | None = None
    accepted_quote_id: str | None = None
    active_contract_version_id: str | None = None
    quotes: tuple[CommercialQuote, ...] = ()
    contract_versions: tuple[ContractVersion, ...] = ()
    deliveries: tuple[CommercialDelivery, ...] = ()
    acceptance_records: tuple[AcceptanceRecord, ...] = ()
    settlements: tuple[Settlement, ...] = ()
    disputes: tuple[CommercialDispute, ...] = ()

    @classmethod
    def from_dict(cls, value: JsonObject) -> CommercialOrder:
        milestones = value["milestones"]
        if not isinstance(milestones, list):
            raise TypeError("milestones must be a list")
        quotes = value.get("quotes", [])
        contract_versions = value.get("contract_versions", [])
        deliveries = value.get("deliveries", [])
        acceptance_records = value.get("acceptance_records", [])
        settlements = value.get("settlements", [])
        disputes = value.get("disputes", [])
        if not all(
            isinstance(items, list)
            for items in (quotes, contract_versions, deliveries, acceptance_records, settlements, disputes)
        ):
            raise TypeError("aggregate collections must be lists")
        quote_items = cast(list[JsonValue], quotes)
        contract_items = cast(list[JsonValue], contract_versions)
        delivery_items = cast(list[JsonValue], deliveries)
        acceptance_items = cast(list[JsonValue], acceptance_records)
        settlement_items = cast(list[JsonValue], settlements)
        dispute_items = cast(list[JsonValue], disputes)
        return cls(
            id=str(value["id"]),
            owner_workspace_id=str(value["owner_workspace_id"]),
            title=str(value["title"]),
            requirements=str(value["requirements"]),
            budget_minor=int(cast(int, value["budget_minor"])),
            currency=str(value["currency"]),
            milestones=tuple(CommercialMilestone.from_dict(cast(JsonObject, item)) for item in milestones),
            status=OrderStatus(str(value["status"])),
            version=int(cast(int, value["version"])),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
            contractor_workspace_id=(
                str(value["contractor_workspace_id"]) if value.get("contractor_workspace_id") is not None else None
            ),
            accepted_quote_id=str(value["accepted_quote_id"]) if value.get("accepted_quote_id") is not None else None,
            active_contract_version_id=(
                str(value["active_contract_version_id"])
                if value.get("active_contract_version_id") is not None
                else None
            ),
            quotes=tuple(CommercialQuote.from_dict(cast(JsonObject, item)) for item in quote_items),
            contract_versions=tuple(ContractVersion.from_dict(cast(JsonObject, item)) for item in contract_items),
            deliveries=tuple(CommercialDelivery.from_dict(cast(JsonObject, item)) for item in delivery_items),
            acceptance_records=tuple(AcceptanceRecord.from_dict(cast(JsonObject, item)) for item in acceptance_items),
            settlements=tuple(Settlement.from_dict(cast(JsonObject, item)) for item in settlement_items),
            disputes=tuple(CommercialDispute.from_dict(cast(JsonObject, item)) for item in dispute_items),
        )


@dataclass(frozen=True, slots=True)
class AuditEvent(SerializableModel):
    event_id: str
    event_type: str
    occurred_at: datetime
    actor_id: str
    workspace_id: str
    object_type: str
    object_id: str
    request_id: str
    before: JsonObject | None
    after: JsonObject | None
    result: str = "succeeded"


@dataclass(frozen=True, slots=True)
class CommandRecord:
    workspace_id: str
    scope: str
    idempotency_key: str
    fingerprint: str
    result_json: str
    created_at: datetime
