from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

VIEW_PERMISSION = "admin.business.view"
MANAGE_PERMISSION = "admin.business.manage"


class BusinessError(Exception):
    code = "BUSINESS_ERROR"


class PermissionDenied(BusinessError):
    code = "PERMISSION_DENIED"


class CrossTenantApprovalRequired(BusinessError):
    code = "CROSS_TENANT_APPROVAL_REQUIRED"


class VersionConflict(BusinessError):
    code = "VERSION_CONFLICT"


class InvalidTransition(BusinessError):
    code = "INVALID_STATUS_TRANSITION"


class ObjectNotFound(BusinessError):
    code = "OBJECT_NOT_FOUND"


class ObjectKind(StrEnum):
    USER = "user"
    TEAM = "team"
    PROJECT = "project"
    TASK = "task"
    CONTENT = "content"


@dataclass(frozen=True)
class ApprovalContext:
    approval_id: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    tenant_ids: frozenset[str]
    action: str

    def authorizes(self, *, tenant_id: str, action: str, now: datetime) -> bool:
        return now <= self.expires_at and tenant_id in self.tenant_ids and self.action == action


@dataclass(frozen=True)
class AdminContext:
    actor_id: str
    permissions: frozenset[str]
    tenant_ids: frozenset[str]
    request_id: str = ""
    access_reason: str | None = None
    approval: ApprovalContext | None = None


@dataclass(frozen=True)
class BusinessObject:
    id: str
    kind: str
    tenant_id: str
    name: str
    status: str
    version: int
    updated_at: datetime
    attributes: Mapping[str, object] = field(default_factory=dict)

    def with_status(self, status: str, at: datetime) -> BusinessObject:
        return replace(self, status=status, version=self.version + 1, updated_at=at)


@dataclass(frozen=True)
class BusinessQuery:
    kind: str
    tenant_id: str | None = None
    statuses: frozenset[str] = frozenset()
    search: str | None = None
    limit: int = 50
    cursor: str | None = None
    accessed_at: datetime | None = None


@dataclass(frozen=True)
class Page:
    items: tuple[BusinessObject, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True)
class StatusChangeCommand:
    object_id: str
    target_status: str
    expected_version: int
    idempotency_key: str
    reason: str
    requested_at: datetime


@dataclass(frozen=True)
class AuditEntry:
    id: str
    request_id: str
    actor_id: str
    tenant_id: str
    object_kind: str
    object_id: str
    action: str
    reason: str
    before: Mapping[str, object]
    after: Mapping[str, object]
    result: str
    occurred_at: datetime
    approval_id: str | None = None


@dataclass(frozen=True)
class Dashboard:
    total_users: int
    active_projects: int
    running_tasks: int
    content_by_status: Mapping[str, int]
    generated_at: datetime
