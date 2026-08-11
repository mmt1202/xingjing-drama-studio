from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, status

from server.xingjing_commercial import Actor, CommercialService


@dataclass(frozen=True, slots=True)
class CommercialOrderRef:
    owner_workspace_id: str
    order_id: str


class CommercialOrderIndex(Protocol):
    """Read-side index used to discover aggregates without bypassing domain authorization."""

    def list_order_refs(self) -> Sequence[CommercialOrderRef]: ...

    def find_order_ref(self, order_id: str) -> CommercialOrderRef | None: ...


type ActorDependency = Callable[..., Actor | Awaitable[Actor]]


@dataclass(frozen=True, slots=True)
class CommercialHttpDependencies:
    service: Callable[[], CommercialService]
    actor: ActorDependency
    order_index: Callable[[], CommercialOrderIndex]


def create_commercial_dependencies(
    *,
    service: Callable[[], CommercialService],
    actor: ActorDependency,
    order_index: Callable[[], CommercialOrderIndex],
) -> CommercialHttpDependencies:
    return CommercialHttpDependencies(service=service, actor=actor, order_index=order_index)


def _unconfigured_service() -> CommercialService:
    raise RuntimeError("commercial HTTP service dependency is not configured")


def _unauthenticated_actor() -> Actor:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "UNAUTHENTICATED"})


def _unconfigured_order_index() -> CommercialOrderIndex:
    raise RuntimeError("commercial HTTP order index dependency is not configured")


default_dependencies = create_commercial_dependencies(
    service=_unconfigured_service,
    actor=_unauthenticated_actor,
    order_index=_unconfigured_order_index,
)
