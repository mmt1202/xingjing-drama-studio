from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.xingjing_commercial import Actor, CommercialOrder, CommercialService, MilestoneInput
from server.xingjing_commercial_http import (
    CommercialOrderRef,
    create_commercial_dependencies,
    create_commercial_router,
)
from tests.xingjing_commercial.support import (
    FakeAccountingPort,
    FakeDeliveryArtifactVerifier,
    InMemoryCommercialRepository,
)

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


class MutableActorProvider:
    def __init__(self, actor: Actor) -> None:
        self.actor = actor

    def __call__(self) -> Actor:
        return self.actor


class InMemoryOrderIndex:
    def __init__(self) -> None:
        self._refs: dict[str, CommercialOrderRef] = {}

    def add(self, order: CommercialOrder) -> None:
        self._refs[order.id] = CommercialOrderRef(order.owner_workspace_id, order.id)

    def list_order_refs(self) -> Sequence[CommercialOrderRef]:
        return tuple(self._refs.values())

    def find_order_ref(self, order_id: str) -> CommercialOrderRef | None:
        return self._refs.get(order_id)


@dataclass(slots=True)
class HttpHarness:
    repository: InMemoryCommercialRepository
    accounting: FakeAccountingPort
    service: CommercialService
    actors: MutableActorProvider
    index: InMemoryOrderIndex
    client: TestClient

    def use_actor(self, actor: Actor) -> None:
        self.actors.actor = actor

    def publish(self, actor: Actor, *, title: str = "品牌宣传片") -> CommercialOrder:
        order = self.service.publish_order(
            actor=actor,
            title=title,
            requirements="交付可验收的成片",
            budget_minor=10_000,
            currency="CNY",
            milestones=(MilestoneInput("成片交付", 10_000, "客户书面确认"),),
            request_id=f"publish-{title}",
            idempotency_key=f"publish-{title}",
        )
        self.index.add(order)
        return order

    def contracted(self, owner: Actor, contractor: Actor) -> CommercialOrder:
        order = self.publish(owner)
        quoted = self.service.submit_quote(
            actor=contractor,
            owner_workspace_id=owner.workspace_id,
            order_id=order.id,
            amount_minor=10_000,
            currency="CNY",
            proposal="按合同交付",
            valid_until=NOW + timedelta(days=2),
            expected_version=order.version,
            request_id="seed-quote",
            idempotency_key="seed-quote",
        )
        awarded = self.service.accept_quote(
            actor=owner,
            owner_workspace_id=owner.workspace_id,
            order_id=order.id,
            quote_id=quoted.quotes[0].id,
            expected_version=quoted.version,
            request_id="seed-award",
            idempotency_key="seed-award",
        )
        return self.service.record_contract_version(
            actor=owner,
            owner_workspace_id=owner.workspace_id,
            order_id=order.id,
            content_ref="object://contracts/v1.pdf",
            content_digest="sha256:contract-v1",
            amount_minor=10_000,
            expected_version=awarded.version,
            request_id="seed-contract",
            idempotency_key="seed-contract",
        )

    def accepted(self, owner: Actor, contractor: Actor) -> CommercialOrder:
        contracted = self.contracted(owner, contractor)
        delivered = self.service.submit_delivery(
            actor=contractor,
            owner_workspace_id=owner.workspace_id,
            order_id=contracted.id,
            milestone_id=contracted.milestones[0].id,
            artifact_version_id="video-v1",
            artifact_digest="sha256:video-v1",
            note="交付",
            expected_version=contracted.version,
            request_id="seed-delivery",
            idempotency_key="seed-delivery",
        )
        from server.xingjing_commercial import AcceptanceDecision

        return self.service.decide_delivery(
            actor=owner,
            owner_workspace_id=owner.workspace_id,
            order_id=contracted.id,
            delivery_id=delivered.deliveries[0].id,
            decision=AcceptanceDecision.ACCEPTED,
            reason=None,
            evidence_ref="object://acceptance/signed.json",
            expected_version=delivered.version,
            request_id="seed-acceptance",
            idempotency_key="seed-acceptance",
        )


def make_harness(actor: Actor) -> HttpHarness:
    repository = InMemoryCommercialRepository()
    accounting = FakeAccountingPort()
    service = CommercialService(
        repository,
        accounting,
        delivery_artifacts=FakeDeliveryArtifactVerifier(),
        now=lambda: NOW,
    )
    actors = MutableActorProvider(actor)
    index = InMemoryOrderIndex()
    dependencies = create_commercial_dependencies(
        service=lambda: service,
        actor=actors,
        order_index=lambda: index,
    )
    app = FastAPI()
    app.include_router(create_commercial_router(dependencies), prefix="/api/v1")
    return HttpHarness(repository, accounting, service, actors, index, TestClient(app))


def command_headers(version: int, key: str) -> dict[str, str]:
    return {
        "If-Match": f'"{version}"',
        "Idempotency-Key": key,
        "X-Request-ID": f"request-{key}",
    }
