from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from .models import Notification

T = TypeVar("T")


class TaskStore(Protocol):
    """Transactional persistence seam; database adapters may implement the same contract."""

    def transact(self, operation: Callable[[dict[str, Any]], T]) -> T: ...

    def snapshot(self) -> dict[str, Any]: ...


class NotificationSink(Protocol):
    """External delivery seam for in-app, email, SMS, push, or an outbox relay."""

    def deliver(self, notification: Notification) -> None: ...


class NullNotificationSink:
    def deliver(self, notification: Notification) -> None:
        del notification


class RecordingNotificationSink:
    def __init__(self) -> None:
        self.deliveries: list[Notification] = []

    def deliver(self, notification: Notification) -> None:
        self.deliveries.append(notification)
