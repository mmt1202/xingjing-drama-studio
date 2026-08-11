from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, TypeVar

from .models import ApiKey, AuditEvent
from .rate_limits import RateLimitDecision

if TYPE_CHECKING:
    from .webhooks import DeliveryAttempt


class ApiKeyRepository(Protocol):
    def add_api_key(self, api_key: ApiKey) -> None: ...
    def get_api_key(self, workspace_id: str, api_key_id: str) -> ApiKey | None: ...
    def replace_api_key(self, api_key: ApiKey, **changes: object) -> ApiKey: ...
    def rotate_api_key(self, old: ApiKey, new: ApiKey) -> tuple[ApiKey, ApiKey]: ...


class RateLimiter(Protocol):
    def consume_rate_limit(
        self, subject: str, operation: str, *, now: datetime, limit: int, window: timedelta
    ) -> RateLimitDecision: ...


class AuditSink(Protocol):
    def append_audit(self, event: AuditEvent) -> None: ...


class ReplayStore(Protocol):
    def claim_webhook_event(self, event_id: str, *, now: datetime, expires_at: datetime) -> bool: ...


T = TypeVar("T")


class DeliveryStore(Protocol):
    def get_delivery_outcome(self, delivery_id: str) -> object | None: ...
    def run_delivery_once(self, delivery_id: str, action: Callable[[], T]) -> T: ...
    def append_delivery_attempt(self, attempt: DeliveryAttempt) -> None: ...
    def save_delivery_outcome(self, delivery_id: str, outcome: object) -> None: ...


class SecretProvider(Protocol):
    """Webhook 秘密应保存在 KMS/Vault；领域对象仅持有 secret_ref。"""

    def get_secret(self, secret_ref: str) -> bytes: ...
