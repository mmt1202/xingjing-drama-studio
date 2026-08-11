from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class InvitationState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Actor:
    id: str
    email: str


@dataclass(frozen=True)
class Invitation:
    id: str
    workspace_id: str
    email: str
    role_id: str
    state: InvitationState
    expires_at: datetime
    invited_by: str
    accepted_by: str | None = None
    version: int = 1


@dataclass(frozen=True)
class SeatUsage:
    limit: int
    occupied: int


@dataclass(frozen=True)
class RoleUpdate:
    name: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class Role:
    id: str
    workspace_id: str
    name: str
    permissions: frozenset[str]
    version: int


@dataclass(frozen=True)
class Authorization:
    workspace_id: str
    member_id: str
    permission: str
    permission_version: int


@dataclass(frozen=True)
class EnterpriseProfileUpdate:
    legal_name: str | None = None
    invoice_title: str | None = None
    data_retention_days: int | None = None
    security_policy: dict[str, Any] = field(default_factory=dict)
    dedicated_deployment: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnterpriseProfile:
    workspace_id: str
    legal_name: str | None
    invoice_title: str | None
    data_retention_days: int | None
    security_policy: dict[str, Any]
    dedicated_deployment: dict[str, Any]
    version: int
    updated_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    id: str
    workspace_id: str
    actor_id: str
    action: str
    object_type: str
    object_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str
    result: str
    occurred_at: datetime
