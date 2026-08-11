from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal


class BillingError(Exception):
    """Base class for stable billing-domain failures."""


class InvalidAmount(BillingError):
    pass


class InsufficientCredits(BillingError):
    pass


class IdempotencyConflict(BillingError):
    pass


class HoldNotFound(BillingError):
    pass


class SeatLimitExceeded(BillingError):
    pass


class PlanNotActive(BillingError):
    pass


class QuotaExceeded(BillingError):
    pass


def positive_minor(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise InvalidAmount("amount must be a positive integer in minor units")
    return value


@dataclass(frozen=True, slots=True)
class CostAttribution:
    workspace_id: str
    project_id: str
    task_id: str
    provider_id: str
    model_id: str
    episode_id: str | None = None
    shot_id: str | None = None
    member_id: str | None = None


@dataclass(frozen=True, slots=True)
class Posting:
    account: str
    delta_minor: int


JournalKind = Literal["grant", "freeze", "settle", "release", "refund", "compensation"]


@dataclass(frozen=True, slots=True)
class Journal:
    id: str
    workspace_id: str
    kind: JournalKind
    postings: tuple[Posting, ...]
    reference: str
    created_at: datetime
    attribution: CostAttribution | None = None
    reversal_of: str | None = None

    def __post_init__(self) -> None:
        if not self.postings or sum(item.delta_minor for item in self.postings) != 0:
            raise ValueError("journal postings must be non-empty and balanced")


HoldStatus = Literal["active", "settled", "released"]


@dataclass(frozen=True, slots=True)
class CreditHold:
    id: str
    workspace_id: str
    amount_minor: int
    settled_minor: int
    released_minor: int
    status: HoldStatus
    price_snapshot_id: str
    attribution: CostAttribution
    freeze_journal_id: str
    settlement_journal_id: str | None = None

    @property
    def held_minor(self) -> int:
        """Original immutable frozen amount (kept for billing snapshot readers)."""
        return self.amount_minor


@dataclass(frozen=True, slots=True)
class CreditAccount:
    workspace_id: str
    available_minor: int
    held_minor: int
    spent_minor: int
    adjusted_minor: int
    version: int


@dataclass(frozen=True, slots=True, init=False)
class EntitlementPlan:
    plan_id: str
    seat_limit: int
    features: frozenset[str]
    quotas: Mapping[str, int]

    def __init__(self, plan_id: str, seat_limit: int, features: frozenset[str], quotas: Mapping[str, int]) -> None:
        if type(seat_limit) is not int or seat_limit < 0:
            raise ValueError("seat_limit must be a non-negative integer")
        normalized = dict(quotas)
        if any(type(value) is not int or value < 0 for value in normalized.values()):
            raise ValueError("quota values must be non-negative integers")
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "seat_limit", seat_limit)
        object.__setattr__(self, "features", frozenset(features))
        object.__setattr__(self, "quotas", MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class CostSummary:
    workspace_id: str
    total_minor: int
    by_project_minor: Mapping[str, int]
    by_member_minor: Mapping[str, int]
    by_provider_minor: Mapping[str, int]
    by_model_minor: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ReconciliationLine:
    external_id: str
    amount_minor: int

    def __post_init__(self) -> None:
        positive_minor(self.amount_minor)


DifferenceKind = Literal["missing_external", "unexpected_external", "amount_mismatch"]


@dataclass(frozen=True, slots=True)
class ReconciliationDifference:
    kind: DifferenceKind
    external_id: str
    internal_minor: int | None
    external_minor: int | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    workspace_id: str
    differences: tuple[ReconciliationDifference, ...]

    @property
    def review_required(self) -> bool:
        return bool(self.differences)


@dataclass(slots=True)
class WorkspaceState:
    available_minor: int = 0
    held_minor: int = 0
    spent_minor: int = 0
    adjusted_minor: int = 0
    version: int = 0
    journals: list[Journal] = field(default_factory=list)
    holds: dict[str, CreditHold] = field(default_factory=dict)
    idempotency: dict[str, tuple[tuple[object, ...], object]] = field(default_factory=dict)
    callback_results: dict[str, tuple[tuple[object, ...], CreditHold]] = field(default_factory=dict)
    plan: EntitlementPlan | None = None
    seats: set[str] = field(default_factory=set)
    quota_remaining: dict[str, int] = field(default_factory=dict)
    refunded_by_hold: dict[str, int] = field(default_factory=dict)


def utc_now() -> datetime:
    return datetime.now(UTC)
