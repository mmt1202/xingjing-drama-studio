from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from .domain import CreditHold, WorkspaceState

T = TypeVar("T")


class BillingRepository(Protocol):
    """Persistence port. Implementations must commit each mutation atomically or not at all."""

    def atomic(self, workspace_id: str, operation: Callable[[WorkspaceState], T]) -> T: ...

    def read(self, workspace_id: str, query: Callable[[WorkspaceState], T]) -> T: ...

    def find_hold(self, hold_id: str) -> CreditHold | None: ...
