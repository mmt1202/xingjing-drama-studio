from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ApiKeyStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    ROTATED = "rotated"


@dataclass(frozen=True, slots=True)
class RequestContext:
    actor_id: str
    workspace_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ApiKey:
    id: str
    client_id: str
    workspace_id: str
    scopes: frozenset[str]
    status: ApiKeyStatus
    expires_at: datetime | None
    created_at: datetime
    secret_salt: str = field(repr=False)
    secret_digest: str = field(repr=False)
    display_secret: None = field(default=None, init=False, repr=False)
    rotated_to_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedApiKey:
    api_key: ApiKey
    secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ApiPrincipal:
    api_key_id: str
    client_id: str
    workspace_id: str
    scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    actor_id: str
    workspace_id: str
    object_id: str
    request_id: str
    outcome: str
    occurred_at: datetime
    before_summary: str | None = None
    after_summary: str | None = None
