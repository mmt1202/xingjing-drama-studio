from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from types import MappingProxyType
from typing import TypeVar, cast
from uuid import uuid4

from .domain import (
    CostAttribution,
    CostSummary,
    CreditAccount,
    CreditHold,
    EntitlementPlan,
    HoldNotFound,
    IdempotencyConflict,
    InsufficientCredits,
    InvalidAmount,
    Journal,
    JournalKind,
    PlanNotActive,
    Posting,
    QuotaExceeded,
    ReconciliationDifference,
    ReconciliationLine,
    ReconciliationResult,
    SeatLimitExceeded,
    WorkspaceState,
    positive_minor,
    utc_now,
)
from .ports import BillingRepository

T = TypeVar("T")


class BillingService:
    def __init__(self, repository: BillingRepository) -> None:
        self._repository = repository

    @staticmethod
    def _journal(
        workspace_id: str,
        kind: str,
        postings: tuple[Posting, ...],
        reference: str,
        attribution: CostAttribution | None = None,
        reversal_of: str | None = None,
    ) -> Journal:
        return Journal(
            id=str(uuid4()),
            workspace_id=workspace_id,
            kind=cast("JournalKind", kind),
            postings=postings,
            reference=reference,
            created_at=utc_now(),
            attribution=attribution,
            reversal_of=reversal_of,
        )

    @staticmethod
    def _idempotent(state: WorkspaceState, key: str, fingerprint: tuple[object, ...], create: Callable[[], T]) -> T:
        if not key:
            raise IdempotencyConflict("idempotency key is required")
        previous = state.idempotency.get(key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise IdempotencyConflict("idempotency key was reused with a different request")
            return cast(T, previous[1])
        result = create()
        state.idempotency[key] = (fingerprint, result)
        return result

    def grant(self, workspace_id: str, amount_minor: int, *, idempotency_key: str, reference: str) -> Journal:
        amount = positive_minor(amount_minor)

        def operation(state: WorkspaceState) -> Journal:
            def create() -> Journal:
                journal = self._journal(
                    workspace_id,
                    "grant",
                    (Posting("team.available", amount), Posting("platform.funding", -amount)),
                    reference,
                )
                state.available_minor += amount
                state.version += 1
                state.journals.append(journal)
                return journal

            return self._idempotent(state, idempotency_key, ("grant", amount, reference), create)

        return self._repository.atomic(workspace_id, operation)

    def freeze(
        self,
        workspace_id: str,
        amount_minor: int,
        *,
        idempotency_key: str,
        price_snapshot_id: str,
        attribution: CostAttribution,
    ) -> CreditHold:
        amount = positive_minor(amount_minor)
        if attribution.workspace_id != workspace_id:
            raise ValueError("cost attribution must belong to the same workspace")

        def operation(state: WorkspaceState) -> CreditHold:
            def create() -> CreditHold:
                if state.available_minor < amount:
                    raise InsufficientCredits("available credits are lower than the requested hold")
                journal = self._journal(
                    workspace_id,
                    "freeze",
                    (Posting("team.available", -amount), Posting("team.held", amount)),
                    f"hold:{price_snapshot_id}",
                    attribution,
                )
                hold = CreditHold(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    amount_minor=amount,
                    settled_minor=0,
                    released_minor=0,
                    status="active",
                    price_snapshot_id=price_snapshot_id,
                    attribution=attribution,
                    freeze_journal_id=journal.id,
                )
                state.available_minor -= amount
                state.held_minor += amount
                state.version += 1
                state.journals.append(journal)
                state.holds[hold.id] = hold
                return hold

            fingerprint = ("freeze", amount, price_snapshot_id, attribution)
            return self._idempotent(state, idempotency_key, fingerprint, create)

        return self._repository.atomic(workspace_id, operation)

    def _workspace_for_hold(self, hold_id: str) -> str:
        hold = self._repository.find_hold(hold_id)
        if hold is None:
            raise HoldNotFound(hold_id)
        return hold.workspace_id

    def settle(self, hold_id: str, actual_minor: int, *, callback_id: str) -> CreditHold:
        actual = positive_minor(actual_minor)
        workspace_id = self._workspace_for_hold(hold_id)

        def operation(state: WorkspaceState) -> CreditHold:
            fingerprint = ("settle", hold_id, actual)
            previous = state.callback_results.get(callback_id)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise IdempotencyConflict("callback id was reused with a different result")
                return previous[1]
            hold = state.holds[hold_id]
            if hold.status != "active":
                raise IdempotencyConflict("a terminal hold cannot be settled again")
            if actual > hold.amount_minor:
                raise InvalidAmount("settlement cannot exceed the frozen amount")
            released = hold.amount_minor - actual
            postings = [Posting("team.held", -hold.amount_minor), Posting("platform.revenue", actual)]
            if released:
                postings.append(Posting("team.available", released))
            journal = self._journal(workspace_id, "settle", tuple(postings), callback_id, hold.attribution)
            updated = CreditHold(
                id=hold.id,
                workspace_id=hold.workspace_id,
                amount_minor=hold.amount_minor,
                settled_minor=actual,
                released_minor=released,
                status="settled",
                price_snapshot_id=hold.price_snapshot_id,
                attribution=hold.attribution,
                freeze_journal_id=hold.freeze_journal_id,
                settlement_journal_id=journal.id,
            )
            state.held_minor -= hold.amount_minor
            state.available_minor += released
            state.spent_minor += actual
            state.version += 1
            state.journals.append(journal)
            state.holds[hold_id] = updated
            state.callback_results[callback_id] = (fingerprint, updated)
            return updated

        return self._repository.atomic(workspace_id, operation)

    def release(self, hold_id: str, *, callback_id: str) -> CreditHold:
        workspace_id = self._workspace_for_hold(hold_id)

        def operation(state: WorkspaceState) -> CreditHold:
            fingerprint = ("release", hold_id)
            previous = state.callback_results.get(callback_id)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise IdempotencyConflict("callback id was reused with a different result")
                return previous[1]
            hold = state.holds[hold_id]
            if hold.status != "active":
                raise IdempotencyConflict("a terminal hold cannot be released again")
            journal = self._journal(
                workspace_id,
                "release",
                (Posting("team.held", -hold.amount_minor), Posting("team.available", hold.amount_minor)),
                callback_id,
                hold.attribution,
            )
            updated = CreditHold(
                id=hold.id,
                workspace_id=hold.workspace_id,
                amount_minor=hold.amount_minor,
                settled_minor=0,
                released_minor=hold.amount_minor,
                status="released",
                price_snapshot_id=hold.price_snapshot_id,
                attribution=hold.attribution,
                freeze_journal_id=hold.freeze_journal_id,
                settlement_journal_id=journal.id,
            )
            state.held_minor -= hold.amount_minor
            state.available_minor += hold.amount_minor
            state.version += 1
            state.journals.append(journal)
            state.holds[hold_id] = updated
            state.callback_results[callback_id] = (fingerprint, updated)
            return updated

        return self._repository.atomic(workspace_id, operation)

    def refund(
        self,
        workspace_id: str,
        amount_minor: int,
        *,
        idempotency_key: str,
        original_hold_id: str,
        reference: str,
    ) -> Journal:
        amount = positive_minor(amount_minor)

        def operation(state: WorkspaceState) -> Journal:
            def create() -> Journal:
                hold = state.holds.get(original_hold_id)
                already_refunded = state.refunded_by_hold.get(original_hold_id, 0)
                if hold is None or hold.status != "settled" or already_refunded + amount > hold.settled_minor:
                    raise InvalidAmount("refund must refer to a settled hold and cannot exceed its settlement")
                journal = self._journal(
                    workspace_id,
                    "refund",
                    (Posting("platform.revenue", -amount), Posting("team.available", amount)),
                    reference,
                    hold.attribution,
                    hold.settlement_journal_id,
                )
                state.available_minor += amount
                state.spent_minor -= amount
                state.refunded_by_hold[original_hold_id] = already_refunded + amount
                state.version += 1
                state.journals.append(journal)
                return journal

            return self._idempotent(state, idempotency_key, ("refund", amount, original_hold_id, reference), create)

        return self._repository.atomic(workspace_id, operation)

    def compensate(
        self,
        workspace_id: str,
        amount_minor: int,
        *,
        idempotency_key: str,
        incident_id: str,
        reason: str,
    ) -> Journal:
        amount = positive_minor(amount_minor)

        def operation(state: WorkspaceState) -> Journal:
            def create() -> Journal:
                journal = self._journal(
                    workspace_id,
                    "compensation",
                    (Posting("team.available", amount), Posting("platform.adjustments", -amount)),
                    f"{incident_id}:{reason}",
                )
                state.available_minor += amount
                state.adjusted_minor += amount
                state.version += 1
                state.journals.append(journal)
                return journal

            return self._idempotent(state, idempotency_key, ("compensation", amount, incident_id, reason), create)

        return self._repository.atomic(workspace_id, operation)

    def account(self, workspace_id: str) -> CreditAccount:
        return self._repository.read(
            workspace_id,
            lambda state: CreditAccount(
                workspace_id,
                state.available_minor,
                state.held_minor,
                state.spent_minor,
                state.adjusted_minor,
                state.version,
            ),
        )

    def hold(self, hold_id: str) -> CreditHold:
        hold = self._repository.find_hold(hold_id)
        if hold is None:
            raise HoldNotFound(hold_id)
        return hold

    def holds(self, workspace_id: str) -> tuple[CreditHold, ...]:
        return self._repository.read(workspace_id, lambda state: tuple(state.holds.values()))

    def journals(self, workspace_id: str) -> tuple[Journal, ...]:
        return self._repository.read(workspace_id, lambda state: tuple(state.journals))

    def cost_summary(self, workspace_id: str) -> CostSummary:
        def query(state: WorkspaceState) -> CostSummary:
            projects: defaultdict[str, int] = defaultdict(int)
            members: defaultdict[str, int] = defaultdict(int)
            providers: defaultdict[str, int] = defaultdict(int)
            models: defaultdict[str, int] = defaultdict(int)
            total = 0
            for journal in state.journals:
                if journal.kind not in {"settle", "refund"} or journal.attribution is None:
                    continue
                amount = sum(
                    posting.delta_minor for posting in journal.postings if posting.account == "platform.revenue"
                )
                attribution = journal.attribution
                total += amount
                projects[attribution.project_id] += amount
                providers[attribution.provider_id] += amount
                models[attribution.model_id] += amount
                if attribution.member_id is not None:
                    members[attribution.member_id] += amount
            return CostSummary(
                workspace_id,
                total,
                MappingProxyType(dict(sorted(projects.items()))),
                MappingProxyType(dict(sorted(members.items()))),
                MappingProxyType(dict(sorted(providers.items()))),
                MappingProxyType(dict(sorted(models.items()))),
            )

        return self._repository.read(workspace_id, query)

    def activate_plan(self, workspace_id: str, plan: EntitlementPlan, *, idempotency_key: str) -> EntitlementPlan:
        def operation(state: WorkspaceState) -> EntitlementPlan:
            def create() -> EntitlementPlan:
                if len(state.seats) > plan.seat_limit:
                    raise SeatLimitExceeded("the new plan cannot cover currently assigned seats")
                state.plan = plan
                state.quota_remaining = dict(plan.quotas)
                state.version += 1
                return plan

            fingerprint = (
                "activate_plan",
                plan.plan_id,
                plan.seat_limit,
                tuple(sorted(plan.features)),
                tuple(sorted(plan.quotas.items())),
            )
            return self._idempotent(state, idempotency_key, fingerprint, create)

        return self._repository.atomic(workspace_id, operation)

    def assign_seat(self, workspace_id: str, member_id: str, *, idempotency_key: str) -> frozenset[str]:
        def operation(state: WorkspaceState) -> frozenset[str]:
            def create() -> frozenset[str]:
                if state.plan is None:
                    raise PlanNotActive("a plan is required before assigning seats")
                if member_id not in state.seats and len(state.seats) >= state.plan.seat_limit:
                    raise SeatLimitExceeded("plan seat limit exceeded")
                state.seats.add(member_id)
                state.version += 1
                return frozenset(state.seats)

            return self._idempotent(state, idempotency_key, ("assign_seat", member_id), create)

        return self._repository.atomic(workspace_id, operation)

    def release_seat(self, workspace_id: str, member_id: str, *, idempotency_key: str) -> frozenset[str]:
        def operation(state: WorkspaceState) -> frozenset[str]:
            def create() -> frozenset[str]:
                state.seats.discard(member_id)
                state.version += 1
                return frozenset(state.seats)

            return self._idempotent(state, idempotency_key, ("release_seat", member_id), create)

        return self._repository.atomic(workspace_id, operation)

    def has_entitlement(self, workspace_id: str, member_id: str, feature: str) -> bool:
        return self._repository.read(
            workspace_id,
            lambda state: state.plan is not None and member_id in state.seats and feature in state.plan.features,
        )

    def consume_quota(
        self,
        workspace_id: str,
        quota: str,
        amount: int,
        *,
        idempotency_key: str,
    ) -> int:
        units = positive_minor(amount)

        def operation(state: WorkspaceState) -> int:
            def create() -> int:
                if state.plan is None:
                    raise PlanNotActive("a plan is required before consuming quota")
                remaining = state.quota_remaining.get(quota)
                if remaining is None or remaining < units:
                    raise QuotaExceeded("entitlement quota exceeded")
                remaining -= units
                state.quota_remaining[quota] = remaining
                state.version += 1
                return remaining

            return self._idempotent(state, idempotency_key, ("consume_quota", quota, units), create)

        return self._repository.atomic(workspace_id, operation)

    def reconcile(self, workspace_id: str, external_lines: tuple[ReconciliationLine, ...]) -> ReconciliationResult:
        def query(state: WorkspaceState) -> ReconciliationResult:
            internal = {
                journal.reference: sum(
                    post.delta_minor for post in journal.postings if post.account == "platform.revenue"
                )
                for journal in state.journals
                if journal.kind == "settle"
            }
            external = {line.external_id: line.amount_minor for line in external_lines}
            differences: list[ReconciliationDifference] = []
            for external_id in sorted(internal.keys() | external.keys()):
                internal_amount = internal.get(external_id)
                external_amount = external.get(external_id)
                if external_amount is None:
                    differences.append(ReconciliationDifference("missing_external", external_id, internal_amount, None))
                elif internal_amount is None:
                    differences.append(
                        ReconciliationDifference("unexpected_external", external_id, None, external_amount)
                    )
                elif internal_amount != external_amount:
                    differences.append(
                        ReconciliationDifference("amount_mismatch", external_id, internal_amount, external_amount)
                    )
            return ReconciliationResult(workspace_id, tuple(differences))

        return self._repository.read(workspace_id, query)
