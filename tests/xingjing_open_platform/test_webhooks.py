from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_open_platform.memory import InMemoryOpenPlatformStore
from server.xingjing_open_platform.webhooks import (
    DeliveryResult,
    RetryPolicy,
    WebhookDeliveryService,
    WebhookSigner,
)

NOW = datetime(2026, 7, 15, tzinfo=UTC)
BODY = b'{"projectId":"p1"}'


def test_signature_binds_timestamp_event_and_exact_body_and_blocks_replay() -> None:
    store = InMemoryOpenPlatformStore()
    signer = WebhookSigner(clock=lambda: NOW, tolerance=timedelta(minutes=5), replay_store=store)
    signature = signer.sign(b"whsec_test", event_id="event-1", body=BODY, timestamp=NOW)

    signer.verify(b"whsec_test", event_id="event-1", body=BODY, timestamp=NOW, signature=signature)
    with pytest.raises(PermissionError, match="replay"):
        signer.verify(b"whsec_test", event_id="event-1", body=BODY, timestamp=NOW, signature=signature)
    with pytest.raises(PermissionError):
        signer.verify(b"whsec_test", event_id="event-2", body=BODY + b" ", timestamp=NOW, signature=signature)


def test_expired_or_future_signature_is_rejected() -> None:
    signer = WebhookSigner(
        clock=lambda: NOW,
        tolerance=timedelta(minutes=5),
        replay_store=InMemoryOpenPlatformStore(),
    )
    old = NOW - timedelta(minutes=6)

    with pytest.raises(PermissionError, match="timestamp"):
        signer.verify(
            b"whsec_test",
            event_id="event-1",
            body=BODY,
            timestamp=old,
            signature=signer.sign(b"whsec_test", event_id="event-1", body=BODY, timestamp=old),
        )


def test_replay_claim_is_atomic_under_concurrent_verification() -> None:
    signer = WebhookSigner(
        clock=lambda: NOW,
        tolerance=timedelta(minutes=5),
        replay_store=InMemoryOpenPlatformStore(),
    )
    signature = signer.sign(b"whsec_test", event_id="event-concurrent", body=BODY, timestamp=NOW)

    def verify_once() -> bool:
        try:
            signer.verify(
                b"whsec_test",
                event_id="event-concurrent",
                body=BODY,
                timestamp=NOW,
                signature=signature,
            )
        except PermissionError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: verify_once(), range(8)))

    assert results.count(True) == 1


def test_delivery_retries_server_failures_and_records_each_attempt_once() -> None:
    store = InMemoryOpenPlatformStore()
    responses = iter([DeliveryResult(503), DeliveryResult(429), DeliveryResult(204)])
    service = WebhookDeliveryService(store=store, transport=lambda request: next(responses), clock=lambda: NOW)

    outcome = service.deliver(
        delivery_id="delivery-1",
        endpoint_id="endpoint-1",
        url="https://hooks.example.test/events",
        headers={"X-Xingjing-Signature": "v1=abc"},
        body=BODY,
        policy=RetryPolicy(max_attempts=3, base_delay=timedelta(seconds=1)),
    )

    assert outcome.succeeded is True
    assert [attempt.status_code for attempt in store.delivery_attempts] == [503, 429, 204]
    assert [attempt.next_attempt_at for attempt in store.delivery_attempts[:2]] == [
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=2),
    ]
    assert (
        service.deliver(
            delivery_id="delivery-1",
            endpoint_id="endpoint-1",
            url="https://hooks.example.test/events",
            headers={},
            body=BODY,
            policy=RetryPolicy(3, timedelta(seconds=1)),
        )
        == outcome
    )


def test_delivery_does_not_retry_non_retryable_client_error() -> None:
    store = InMemoryOpenPlatformStore()
    service = WebhookDeliveryService(store=store, transport=lambda request: DeliveryResult(400), clock=lambda: NOW)

    outcome = service.deliver(
        delivery_id="delivery-2",
        endpoint_id="endpoint-1",
        url="https://hooks.example.test/events",
        headers={},
        body=BODY,
        policy=RetryPolicy(3, timedelta(seconds=1)),
    )

    assert outcome.succeeded is False
    assert len(store.delivery_attempts) == 1
