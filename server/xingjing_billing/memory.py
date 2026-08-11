from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import TypeVar

from .domain import CreditHold, EntitlementPlan, WorkspaceState

T = TypeVar("T")


def _clone(state: WorkspaceState) -> WorkspaceState:
    clone = WorkspaceState(
        available_minor=state.available_minor,
        held_minor=state.held_minor,
        spent_minor=state.spent_minor,
        adjusted_minor=state.adjusted_minor,
        version=state.version,
        journals=list(state.journals),
        holds=dict(state.holds),
        # Values exposed by the domain are immutable; a shallow copy isolates
        # the transaction dictionary without trying to pickle MappingProxyType.
        idempotency=dict(state.idempotency),
        callback_results=dict(state.callback_results),
        plan=None,
        seats=set(state.seats),
        quota_remaining=dict(state.quota_remaining),
        refunded_by_hold=dict(state.refunded_by_hold),
    )
    if state.plan is not None:
        clone.plan = EntitlementPlan(
            state.plan.plan_id, state.plan.seat_limit, state.plan.features, dict(state.plan.quotas)
        )
    return clone


class InMemoryBillingRepository:
    """Thread-safe reference adapter with copy-on-write rollback semantics."""

    def __init__(self) -> None:
        self._states: dict[str, WorkspaceState] = {}
        self._lock = RLock()

    def atomic(self, workspace_id: str, operation: Callable[[WorkspaceState], T]) -> T:
        with self._lock:
            working = _clone(self._states.get(workspace_id, WorkspaceState()))
            result = operation(working)
            self._states[workspace_id] = working
            return result

    def read(self, workspace_id: str, query: Callable[[WorkspaceState], T]) -> T:
        with self._lock:
            return query(_clone(self._states.get(workspace_id, WorkspaceState())))

    def find_hold(self, hold_id: str) -> CreditHold | None:
        with self._lock:
            for state in self._states.values():
                if hold := state.holds.get(hold_id):
                    return hold
        return None
