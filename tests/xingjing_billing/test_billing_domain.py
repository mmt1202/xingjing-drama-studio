from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from server.xingjing_billing import (
    BillingService,
    CostAttribution,
    EntitlementPlan,
    IdempotencyConflict,
    InMemoryBillingRepository,
    InsufficientCredits,
    InvalidAmount,
    QuotaExceeded,
    ReconciliationLine,
    SeatLimitExceeded,
)


def service() -> BillingService:
    return BillingService(InMemoryBillingRepository())


def attribution(*, project_id: str = "project-1") -> CostAttribution:
    return CostAttribution(
        workspace_id="team-1",
        project_id=project_id,
        episode_id="episode-1",
        shot_id="shot-1",
        task_id="task-1",
        member_id="member-1",
        provider_id="provider-1",
        model_id="model-1",
    )


def test_freeze_settle_and_release_are_balanced_and_conserved() -> None:
    billing = service()
    billing.grant("team-1", 1_000, idempotency_key="grant-1", reference="order-1")

    hold = billing.freeze(
        "team-1",
        600,
        idempotency_key="freeze-1",
        price_snapshot_id="price-v1",
        attribution=attribution(),
    )
    settled = billing.settle(hold.id, 450, callback_id="provider-callback-1")

    assert settled.held_minor == 600
    assert settled.settled_minor == 450
    assert settled.released_minor == 150
    assert billing.account("team-1").available_minor == 550
    assert billing.account("team-1").held_minor == 0
    assert all(sum(posting.delta_minor for posting in journal.postings) == 0 for journal in billing.journals("team-1"))


def test_failure_release_is_idempotent_and_late_success_cannot_charge() -> None:
    billing = service()
    billing.grant("team-1", 500, idempotency_key="grant-1", reference="order-1")
    hold = billing.freeze(
        "team-1",
        300,
        idempotency_key="freeze-1",
        price_snapshot_id="price-v1",
        attribution=attribution(),
    )

    first = billing.release(hold.id, callback_id="failed-callback")
    duplicate = billing.release(hold.id, callback_id="failed-callback")

    assert duplicate == first
    assert billing.account("team-1").available_minor == 500
    with pytest.raises(IdempotencyConflict):
        billing.settle(hold.id, 200, callback_id="late-success")


def test_partial_settlement_cannot_exceed_frozen_amount() -> None:
    billing = service()
    billing.grant("team-1", 500, idempotency_key="grant-1", reference="order-1")
    hold = billing.freeze(
        "team-1",
        300,
        idempotency_key="freeze-1",
        price_snapshot_id="price-v1",
        attribution=attribution(),
    )

    with pytest.raises(InvalidAmount):
        billing.settle(hold.id, 301, callback_id="callback-1")

    assert billing.hold(hold.id).settled_minor == 0
    assert billing.account("team-1").held_minor == 300


def test_insufficient_balance_does_not_create_a_hold() -> None:
    billing = service()
    billing.grant("team-1", 10, idempotency_key="grant-1", reference="order-1")

    with pytest.raises(InsufficientCredits):
        billing.freeze(
            "team-1",
            11,
            idempotency_key="freeze-1",
            price_snapshot_id="price-v1",
            attribution=attribution(),
        )

    assert billing.holds("team-1") == ()
    assert billing.account("team-1").available_minor == 10


def test_idempotency_key_replays_same_result_and_rejects_changed_payload() -> None:
    billing = service()

    first = billing.grant("team-1", 200, idempotency_key="payment-callback-1", reference="order-1")
    replay = billing.grant("team-1", 200, idempotency_key="payment-callback-1", reference="order-1")

    assert replay == first
    assert billing.account("team-1").available_minor == 200
    with pytest.raises(IdempotencyConflict):
        billing.grant("team-1", 201, idempotency_key="payment-callback-1", reference="order-1")


def test_refund_appends_reversal_instead_of_mutating_history() -> None:
    billing = service()
    billing.grant("team-1", 500, idempotency_key="grant-1", reference="order-1")
    hold = billing.freeze(
        "team-1",
        300,
        idempotency_key="freeze-1",
        price_snapshot_id="price-v1",
        attribution=attribution(),
    )
    billing.settle(hold.id, 300, callback_id="success-1")
    before = billing.journals("team-1")

    refund = billing.refund(
        "team-1", 120, idempotency_key="refund-1", original_hold_id=hold.id, reference="refund-order-1"
    )

    assert billing.journals("team-1")[: len(before)] == before
    assert (
        refund.reversal_of == hold.settlement_journal_id
        or refund.reversal_of == billing.hold(hold.id).settlement_journal_id
    )
    assert billing.account("team-1").available_minor == 320
    assert billing.cost_summary("team-1").total_minor == 180
    with pytest.raises(FrozenInstanceError):
        refund.reference = "changed"  # type: ignore[misc]

    with pytest.raises(InvalidAmount):
        billing.refund(
            "team-1",
            181,
            idempotency_key="refund-2",
            original_hold_id=hold.id,
            reference="refund-order-2",
        )


def test_cost_rollups_preserve_project_and_team_totals() -> None:
    billing = service()
    billing.grant("team-1", 1_000, idempotency_key="grant-1", reference="order-1")
    for index, (project_id, actual) in enumerate((("project-1", 120), ("project-1", 80), ("project-2", 50))):
        hold = billing.freeze(
            "team-1",
            200,
            idempotency_key=f"freeze-{index}",
            price_snapshot_id="price-v1",
            attribution=attribution(project_id=project_id),
        )
        billing.settle(hold.id, actual, callback_id=f"callback-{index}")

    report = billing.cost_summary("team-1")

    assert report.total_minor == 250
    assert report.by_project_minor == {"project-1": 200, "project-2": 50}
    assert sum(report.by_project_minor.values()) == report.total_minor


def test_entitlements_and_seats_change_effective_access_atomically() -> None:
    billing = service()
    plan = EntitlementPlan(
        plan_id="studio",
        seat_limit=2,
        features=frozenset({"billing.export", "video.hd"}),
        quotas={"formal_exports": 3},
    )
    billing.activate_plan("team-1", plan, idempotency_key="plan-1")

    billing.assign_seat("team-1", "member-1", idempotency_key="seat-1")
    billing.assign_seat("team-1", "member-2", idempotency_key="seat-2")

    assert billing.has_entitlement("team-1", "member-1", "video.hd")
    with pytest.raises(SeatLimitExceeded):
        billing.assign_seat("team-1", "member-3", idempotency_key="seat-3")
    billing.release_seat("team-1", "member-1", idempotency_key="seat-release-1")
    assert not billing.has_entitlement("team-1", "member-1", "video.hd")

    assert billing.consume_quota("team-1", "formal_exports", 2, idempotency_key="export-1") == 1
    assert billing.consume_quota("team-1", "formal_exports", 2, idempotency_key="export-1") == 1
    with pytest.raises(QuotaExceeded):
        billing.consume_quota("team-1", "formal_exports", 2, idempotency_key="export-2")


def test_reconciliation_detects_missing_mismatched_and_unexpected_records() -> None:
    billing = service()
    billing.grant("team-1", 500, idempotency_key="grant-1", reference="order-1")
    hold = billing.freeze(
        "team-1",
        300,
        idempotency_key="freeze-1",
        price_snapshot_id="price-v1",
        attribution=attribution(),
    )
    billing.settle(hold.id, 200, callback_id="provider-bill-1")

    result = billing.reconcile(
        "team-1",
        (
            ReconciliationLine(external_id="provider-bill-1", amount_minor=190),
            ReconciliationLine(external_id="unknown-bill", amount_minor=40),
        ),
    )

    assert [(difference.kind, difference.external_id) for difference in result.differences] == [
        ("amount_mismatch", "provider-bill-1"),
        ("unexpected_external", "unknown-bill"),
    ]
    assert result.review_required


def test_compensation_is_a_new_balanced_journal_and_is_idempotent() -> None:
    billing = service()
    billing.grant("team-1", 100, idempotency_key="grant-1", reference="order-1")

    first = billing.compensate(
        "team-1",
        25,
        idempotency_key="incident-1",
        incident_id="incident-42",
        reason="manual_review",
    )
    replay = billing.compensate(
        "team-1",
        25,
        idempotency_key="incident-1",
        incident_id="incident-42",
        reason="manual_review",
    )

    assert replay == first
    assert sum(posting.delta_minor for posting in first.postings) == 0
    assert billing.account("team-1").available_minor == 125


def test_concurrent_freezes_are_atomic_and_never_overdraw() -> None:
    billing = service()
    billing.grant("team-1", 100, idempotency_key="grant-1", reference="order-1")

    def attempt(index: int) -> bool:
        try:
            billing.freeze(
                "team-1",
                60,
                idempotency_key=f"freeze-{index}",
                price_snapshot_id="price-v1",
                attribution=attribution(),
            )
        except InsufficientCredits:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, range(2)))

    assert sorted(outcomes) == [False, True]
    assert billing.account("team-1").available_minor == 40
    assert billing.account("team-1").held_minor == 60


@pytest.mark.parametrize("bad_amount", [0, -1, 1.5, True])
def test_amounts_must_be_positive_integer_minor_units(bad_amount: object) -> None:
    billing = service()

    with pytest.raises(InvalidAmount):
        billing.grant("team-1", bad_amount, idempotency_key="grant-1", reference="order-1")  # type: ignore[arg-type]
