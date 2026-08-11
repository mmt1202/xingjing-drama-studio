import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.xingjing_billing_persistence import BillingFinanceRepository, BillingPersistenceBase
from server.xingjing_billing_runtime import BillingRuntime, BillingRuntimePermissionDenied
from server.xingjing_generation_persistence.repository import Base as GenerationBase


@pytest.mark.asyncio
async def test_payment_order_callback_and_refund_change_credits_once() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(GenerationBase.metadata.create_all)
        await connection.run_sync(BillingPersistenceBase.metadata.create_all)
    repository = BillingFinanceRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        tenant_id="tenant-1",
        workspace_id="workspace-1",
    )
    now = datetime(2026, 8, 10, tzinfo=UTC)

    order = await repository.create_payment_order(
        order_id="order-1",
        amount_minor=1_000,
        currency="CNY",
        order_type="credit_top_up",
        actor_id="owner-1",
        request_id="request-create",
        idempotency_key="create-1",
        expires_at=now + timedelta(minutes=30),
        occurred_at=now,
    )
    callback = json.dumps(
        {
            "tenantId": "tenant-1",
            "workspaceId": "workspace-1",
            "orderId": "order-1",
            "eventId": "payment-event-1",
            "externalReference": "provider-payment-1",
            "paidMinor": 1_000,
            "occurredAt": (now + timedelta(minutes=1)).isoformat(),
        },
        separators=(",", ":"),
    ).encode()
    runtime = BillingRuntime(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        engine=engine,
        context_resolver=lambda _request: None,  # type: ignore[arg-type,return-value]
        payment_callback_secret="callback-secret",
    )
    with pytest.raises(BillingRuntimePermissionDenied):
        await runtime.apply_payment_callback(raw_body=callback, signature="wrong")
    signature = hmac.new(b"callback-secret", callback, hashlib.sha256).hexdigest()
    paid = await runtime.apply_payment_callback(raw_body=callback, signature=signature)
    replay = await runtime.apply_payment_callback(raw_body=callback, signature=signature)

    assert order["status"] == "pending"
    assert paid == replay
    assert paid["status"] == "paid"
    assert await repository.available_credits() == 1_000

    refunded = await repository.refund_payment_order(
        order_id="order-1",
        amount_minor=1_000,
        actor_id="finance-1",
        request_id="request-refund",
        idempotency_key="refund-1",
        occurred_at=now + timedelta(minutes=2),
    )
    assert refunded["status"] == "refunded"
    assert await repository.available_credits() == 0
    assert len(await repository.orders()) == 1

    await repository.create_payment_order(
        order_id="order-expired",
        amount_minor=500,
        currency="CNY",
        order_type="credit_top_up",
        actor_id="owner-1",
        request_id="request-expired",
        idempotency_key="create-expired",
        expires_at=now + timedelta(seconds=1),
        occurred_at=now,
    )
    assert await repository.close_expired_payment_orders(now=now + timedelta(minutes=1), limit=10) == 1
    orders = {str(item["id"]): item for item in await repository.orders()}
    assert orders["order-expired"]["status"] == "closed"
    assert await repository.available_credits() == 0
    await engine.dispose()
