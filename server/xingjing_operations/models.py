from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class Actor:
    tenant_id: str
    actor_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class AuditRecord:
    tenant_id: str
    actor_id: str
    request_id: str
    action: str
    object_type: str
    object_id: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    result: Literal["success", "failure"]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProbeReading:
    name: str
    status: str
    metrics: Mapping[str, int | float | str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceState:
    status: Literal["available", "unavailable", "not_configured"]
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class OperationsSnapshot:
    tenant_id: str
    observed_at: datetime
    services: tuple[ProbeReading, ...]
    queues: tuple[ProbeReading, ...]
    storage: tuple[ProbeReading, ...]
    service_source: SourceState
    queue_source: SourceState
    storage_source: SourceState


@dataclass(frozen=True, slots=True)
class Incident:
    id: str
    tenant_id: str
    source_type: str
    source_id: str
    severity: str
    summary: str
    status: Literal["open", "acknowledged", "resolved"]
    owner_id: str | None
    resolution: str | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TicketMessage:
    author_id: str
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SupportTicket:
    id: str
    tenant_id: str
    category: str
    related_object_id: str
    priority: str
    status: Literal["open", "assigned", "escalated", "closed"]
    assignee_id: str | None
    escalation_target: str | None
    close_reason: str | None
    messages: tuple[TicketMessage, ...]
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CompensationRequest:
    id: str
    tenant_id: str
    ticket_id: str
    kind: str
    amount: Decimal
    reason: str
    requested_by: str
    approved_by: str | None
    status: Literal["pending_approval", "approved", "completed", "failed"]
    external_reference: str | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MessageTemplate:
    id: str
    tenant_id: str
    name: str
    channel: str
    body: str
    template_version: int
    status: Literal["draft", "published"]
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationRule:
    id: str
    tenant_id: str
    event_type: str
    template_id: str
    template_version: int
    audience: tuple[str, ...]
    enabled: bool
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    status: Literal["accepted", "delivered", "failed"]
    provider_reference: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    delivery_id: str
    tenant_id: str
    channel: str
    recipient_id: str
    body: str


@dataclass(frozen=True, slots=True)
class DeliveryLog:
    id: str
    tenant_id: str
    rule_id: str
    recipient_id: str
    status: Literal["pending", "accepted", "delivered", "failed"]
    provider_reference: str | None
    detail: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConfigurationVersion:
    id: str
    tenant_id: str
    key: str
    payload: Mapping[str, Any]
    config_version: int
    status: Literal["draft", "approved", "published", "superseded"]
    authored_by: str
    approved_by: str | None
    rollback_of: int | None
    version: int
    created_at: datetime
    updated_at: datetime
