from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from threading import RLock
from typing import TYPE_CHECKING, TypeVar

from .models import ApiKey, ApiKeyStatus, AuditEvent
from .rate_limits import RateLimitDecision

if TYPE_CHECKING:
    from .webhooks import DeliveryAttempt

T = TypeVar("T")


class InMemoryOpenPlatformStore:
    """测试及单进程使用的原子适配器；生产应替换为事务型持久化端口。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._api_keys: dict[str, ApiKey] = {}
        self._rate_windows: dict[tuple[str, str, datetime], int] = {}
        self.audit_events: list[AuditEvent] = []
        self._webhook_replays: dict[str, datetime] = {}
        self.delivery_attempts: list[DeliveryAttempt] = []
        self._delivery_outcomes: dict[str, object] = {}

    def add_api_key(self, api_key: ApiKey) -> None:
        with self._lock:
            if api_key.id in self._api_keys:
                raise ValueError("api key already exists")
            self._api_keys[api_key.id] = api_key

    def get_api_key(self, workspace_id: str, api_key_id: str) -> ApiKey | None:
        with self._lock:
            item = self._api_keys.get(api_key_id)
            return item if item is not None and item.workspace_id == workspace_id else None

    def replace_api_key(self, api_key: ApiKey, **changes: object) -> ApiKey:
        with self._lock:
            current = self._api_keys.get(api_key.id)
            if current != api_key:
                raise ValueError("concurrent api key update")
            updated = replace(api_key, **changes)
            self._api_keys[api_key.id] = updated
            return updated

    def rotate_api_key(self, old: ApiKey, new: ApiKey) -> tuple[ApiKey, ApiKey]:
        with self._lock:
            current = self._api_keys.get(old.id)
            if current is None or current != old or current.status is not ApiKeyStatus.ACTIVE:
                raise ValueError("api key cannot be rotated")
            if new.id in self._api_keys:
                raise ValueError("replacement already exists")
            ended = replace(old, status=ApiKeyStatus.ROTATED, rotated_to_id=new.id)
            self._api_keys[old.id] = ended
            self._api_keys[new.id] = new
            return ended, new

    def consume_rate_limit(
        self, subject: str, operation: str, *, now: datetime, limit: int, window: timedelta
    ) -> RateLimitDecision:
        window_us = int(window.total_seconds() * 1_000_000)
        epoch_us = int(now.timestamp() * 1_000_000)
        start_us = epoch_us - (epoch_us % window_us)
        start = datetime.fromtimestamp(start_us / 1_000_000, tz=now.tzinfo)
        key = (subject, operation, start)
        with self._lock:
            used = self._rate_windows.get(key, 0)
            if used >= limit:
                return RateLimitDecision(False, 0, start + window)
            self._rate_windows[key] = used + 1
            return RateLimitDecision(True, limit - used - 1, start + window)

    def append_audit(self, event: AuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)

    def claim_webhook_event(self, event_id: str, *, now: datetime, expires_at: datetime) -> bool:
        with self._lock:
            existing = self._webhook_replays.get(event_id)
            if existing is not None and existing > now:
                return False
            self._webhook_replays[event_id] = expires_at
            return True

    def get_delivery_outcome(self, delivery_id: str) -> object | None:
        with self._lock:
            return self._delivery_outcomes.get(delivery_id)

    def run_delivery_once(self, delivery_id: str, action: Callable[[], T]) -> T:
        with self._lock:
            existing = self._delivery_outcomes.get(delivery_id)
            if existing is not None:
                return existing  # type: ignore[return-value]
            return action()

    def append_delivery_attempt(self, attempt: DeliveryAttempt) -> None:
        self.delivery_attempts.append(attempt)

    def save_delivery_outcome(self, delivery_id: str, outcome: object) -> None:
        self._delivery_outcomes[delivery_id] = outcome
