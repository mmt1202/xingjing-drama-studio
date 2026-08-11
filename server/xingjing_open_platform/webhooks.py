from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .ports import DeliveryStore, ReplayStore


class WebhookSigner:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        tolerance: timedelta,
        replay_store: ReplayStore,
    ) -> None:
        if tolerance <= timedelta(0):
            raise ValueError("tolerance must be positive")
        self._clock = clock
        self._tolerance = tolerance
        self._replay_store = replay_store

    @staticmethod
    def _payload(event_id: str, body: bytes, timestamp: datetime) -> bytes:
        return str(int(timestamp.timestamp())).encode("ascii") + b"." + event_id.encode("utf-8") + b"." + body

    def sign(self, secret: bytes, *, event_id: str, body: bytes, timestamp: datetime) -> str:
        digest = hmac.new(secret, self._payload(event_id, body, timestamp), hashlib.sha256).hexdigest()
        return f"v1={digest}"

    def verify(
        self,
        secret: bytes,
        *,
        event_id: str,
        body: bytes,
        timestamp: datetime,
        signature: str,
    ) -> None:
        now = self._clock()
        if abs(now - timestamp) > self._tolerance:
            raise PermissionError("webhook timestamp outside tolerance")
        expected = self.sign(secret, event_id=event_id, body=body, timestamp=timestamp)
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("invalid webhook signature")
        if not self._replay_store.claim_webhook_event(event_id, now=now, expires_at=now + self._tolerance):
            raise PermissionError("webhook replay detected")


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status_code: int


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay: timedelta

    def __post_init__(self) -> None:
        if self.max_attempts <= 0 or self.base_delay <= timedelta(0):
            raise ValueError("retry policy values must be positive")


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    delivery_id: str
    endpoint_id: str
    url: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    delivery_id: str
    attempt: int
    status_code: int
    attempted_at: datetime
    next_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    delivery_id: str
    succeeded: bool
    attempts: int
    final_status_code: int


class WebhookDeliveryService:
    def __init__(
        self,
        *,
        store: DeliveryStore,
        transport: Callable[[DeliveryRequest], DeliveryResult],
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._transport = transport
        self._clock = clock

    def deliver(
        self,
        *,
        delivery_id: str,
        endpoint_id: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        policy: RetryPolicy,
    ) -> DeliveryOutcome:
        existing = self._store.get_delivery_outcome(delivery_id)
        if isinstance(existing, DeliveryOutcome):
            return existing

        def execute() -> DeliveryOutcome:
            request = DeliveryRequest(delivery_id, endpoint_id, url, headers, body)
            for attempt_number in range(1, policy.max_attempts + 1):
                result = self._transport(request)
                succeeded = 200 <= result.status_code < 300
                retryable = result.status_code == 429 or result.status_code >= 500
                will_retry = retryable and attempt_number < policy.max_attempts
                next_at = self._clock() + policy.base_delay * (2 ** (attempt_number - 1)) if will_retry else None
                self._store.append_delivery_attempt(
                    DeliveryAttempt(delivery_id, attempt_number, result.status_code, self._clock(), next_at)
                )
                if succeeded or not will_retry:
                    outcome = DeliveryOutcome(delivery_id, succeeded, attempt_number, result.status_code)
                    self._store.save_delivery_outcome(delivery_id, outcome)
                    return outcome
            raise AssertionError("unreachable")

        return self._store.run_delivery_once(delivery_id, execute)
