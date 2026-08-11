from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .errors import IdempotencyConflict, NotFound, VersionConflict
from .models import (
    AuditRecord,
    CompensationRequest,
    ConfigurationVersion,
    DeliveryLog,
    Incident,
    MessageTemplate,
    NotificationRule,
    OperationsSnapshot,
    SupportTicket,
)


class AuditTrail:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self._records.append(record)

    def query(
        self,
        *,
        tenant_id: str | None = None,
        request_id: str | None = None,
        actor_id: str | None = None,
        object_id: str | None = None,
    ) -> tuple[AuditRecord, ...]:
        return tuple(
            record
            for record in self._records
            if (tenant_id is None or record.tenant_id == tenant_id)
            and (request_id is None or record.request_id == request_id)
            and (actor_id is None or record.actor_id == actor_id)
            and (object_id is None or record.object_id == object_id)
        )


class InMemoryRepository[T]:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], T] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[Any, T]] = {}

    def get(self, tenant_id: str, object_id: str) -> T:
        try:
            return self._items[(tenant_id, object_id)]
        except KeyError as exc:
            raise NotFound(object_id) from exc

    def put(self, tenant_id: str, object_id: str, value: T) -> T:
        self._items[(tenant_id, object_id)] = value
        return value

    def put_versioned(self, tenant_id: str, object_id: str, expected_version: int, value: T) -> T:
        current = self.get(tenant_id, object_id)
        if getattr(current, "version") != expected_version:
            raise VersionConflict(object_id)
        return self.put(tenant_id, object_id, value)

    def replay(self, tenant_id: str, operation: str, key: str, fingerprint: Any) -> T | None:
        stored = self._idempotency.get((tenant_id, operation, key))
        if stored is None:
            return None
        stored_fingerprint, result = stored
        if stored_fingerprint != fingerprint:
            raise IdempotencyConflict(key)
        return result

    def remember(self, tenant_id: str, operation: str, key: str, fingerprint: Any, result: T) -> T:
        self._idempotency[(tenant_id, operation, key)] = (fingerprint, result)
        return result

    def values(self, tenant_id: str) -> tuple[T, ...]:
        return tuple(item for (scope, _), item in self._items.items() if scope == tenant_id)


class InMemoryOperationsRepository(InMemoryRepository[Incident]):
    def __init__(self) -> None:
        super().__init__()
        self.snapshots: list[OperationsSnapshot] = []


class InMemorySupportRepository(InMemoryRepository[SupportTicket]):
    def __init__(self) -> None:
        super().__init__()
        self.compensations: InMemoryRepository[CompensationRequest] = InMemoryRepository()


class InMemoryNotificationRepository(InMemoryRepository[NotificationRule]):
    def __init__(self) -> None:
        super().__init__()
        self.templates: dict[tuple[str, str, int], MessageTemplate] = {}
        self.deliveries: InMemoryRepository[DeliveryLog] = InMemoryRepository()


class InMemoryConfigurationRepository(InMemoryRepository[ConfigurationVersion]):
    def by_key(self, tenant_id: str, key: str) -> tuple[ConfigurationVersion, ...]:
        return tuple(
            sorted((item for item in self.values(tenant_id) if item.key == key), key=lambda item: item.config_version)
        )


def summary(value: object | None) -> dict[str, Any] | None:
    return None if value is None else asdict(value)  # type: ignore[arg-type]
