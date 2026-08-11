from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_commercial import Actor, CommercialService, MilestoneInput
from server.xingjing_commercial_persistence import (
    CommercialPersistenceBase,
    FailClosedAccountingPort,
)
from tests.xingjing_commercial.support import FakeAccountingPort, FakeDeliveryArtifactVerifier


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    CommercialPersistenceBase.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def test_runtime_requires_all_authoritative_ports(session_factory: sessionmaker[Session]) -> None:
    from server.xingjing_commercial_runtime import CommercialRuntimeConfigurationError, create_commercial_runtime

    with pytest.raises(CommercialRuntimeConfigurationError, match="session_factory"):
        create_commercial_runtime(
            session_factory=None,
            accounting_port=FakeAccountingPort(),
            delivery_artifact_port=FakeDeliveryArtifactVerifier(),
            actor_provider=lambda: _owner(),
        )

    with pytest.raises(CommercialRuntimeConfigurationError, match="accounting_port"):
        create_commercial_runtime(
            session_factory=session_factory,
            accounting_port=None,
            delivery_artifact_port=FakeDeliveryArtifactVerifier(),
            actor_provider=lambda: _owner(),
        )

    with pytest.raises(CommercialRuntimeConfigurationError, match="actor_provider"):
        create_commercial_runtime(
            session_factory=session_factory,
            accounting_port=FakeAccountingPort(),
            delivery_artifact_port=FakeDeliveryArtifactVerifier(),
            actor_provider=None,
        )

    with pytest.raises(CommercialRuntimeConfigurationError, match="delivery_artifact_port"):
        create_commercial_runtime(
            session_factory=session_factory,
            accounting_port=FakeAccountingPort(),
            delivery_artifact_port=None,
            actor_provider=lambda: _owner(),
        )


def test_runtime_composes_sql_persistence_and_explicit_ports(session_factory: sessionmaker[Session]) -> None:
    from server.xingjing_commercial_runtime import create_commercial_runtime

    accounting = FakeAccountingPort()
    runtime = create_commercial_runtime(
        session_factory=session_factory,
        accounting_port=accounting,
        delivery_artifact_port=FakeDeliveryArtifactVerifier(),
        actor_provider=lambda: _owner(),
    )

    service = runtime.service()
    assert isinstance(service, CommercialService)
    order = service.publish_order(
        actor=_owner(),
        title="品牌宣传片",
        requirements="交付可验收的成片",
        budget_minor=10_000,
        currency="CNY",
        milestones=(MilestoneInput("成片交付", 10_000, "客户书面确认"),),
        request_id="request-1",
        idempotency_key="key-1",
    )

    assert runtime.order_index().find_order_ref(order.id) is not None
    assert runtime.order_index().list_order_refs()[0].owner_workspace_id == "workspace-owner"
    assert runtime.actor() == _owner()
    assert runtime.accounting_port is accounting


def test_runtime_keeps_unconfigured_accounting_fail_closed(session_factory: sessionmaker[Session]) -> None:
    from server.xingjing_commercial_runtime import create_commercial_runtime

    runtime = create_commercial_runtime(
        session_factory=session_factory,
        accounting_port=FailClosedAccountingPort(now=lambda: datetime(2026, 7, 16, tzinfo=UTC)),
        delivery_artifact_port=FakeDeliveryArtifactVerifier(),
        actor_provider=lambda: _owner(),
    )

    assert runtime.accounting_port is not None
    assert runtime.service() is not None


def _owner() -> Actor:
    return Actor.member("owner-1", "workspace-owner", {"commercial.view", "commercial.manage"})
