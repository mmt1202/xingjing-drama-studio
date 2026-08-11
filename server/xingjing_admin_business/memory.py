from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock

from .domain import (
    MANAGE_PERMISSION,
    VIEW_PERMISSION,
    AdminContext,
    AuditEntry,
    BusinessObject,
    BusinessQuery,
    CrossTenantApprovalRequired,
    Dashboard,
    InvalidTransition,
    ObjectNotFound,
    Page,
    PermissionDenied,
    StatusChangeCommand,
    VersionConflict,
)

_SENSITIVE_KEYS = frozenset({"api_key", "password", "secret", "token", "media_url", "raw_prompt"})
_TRANSITIONS: Mapping[str, Mapping[str, frozenset[str]]] = {
    "user": {"active": frozenset({"suspended"}), "suspended": frozenset({"active"})},
    "team": {"active": frozenset({"suspended"}), "suspended": frozenset({"active"})},
    "project": {"active": frozenset({"frozen", "archived"}), "frozen": frozenset({"active", "archived"})},
    "task": {"queued": frozenset({"cancelled"}), "running": frozenset({"cancelling"}), "failed": frozenset({"queued"})},
    "content": {"active": frozenset({"blocked"}), "blocked": frozenset({"active"})},
}


def _mask(value: object) -> str:
    text = str(value)
    if "@" in text:
        local, domain = text.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if len(text) >= 7 and text.isdigit():
        return f"{text[:3]}****{text[-4:]}"
    return "***"


def _redact(item: BusinessObject) -> BusinessObject:
    attributes = {
        key: (_mask(value) if key in _SENSITIVE_KEYS or key in {"email", "phone"} else value)
        for key, value in item.attributes.items()
    }
    return replace(item, attributes=attributes)


def _encode_cursor(item: BusinessObject) -> str:
    raw = f"{item.updated_at.isoformat()}\x1f{item.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        updated_at, identifier = raw.split("\x1f", 1)
        return datetime.fromisoformat(updated_at), identifier
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("INVALID_CURSOR") from exc


class InMemoryBusinessStore:
    """原子测试适配器；生产接线应以同一事务实现命令、幂等记录与审计落库。"""

    def __init__(self, objects: Iterable[BusinessObject] = ()) -> None:
        self._objects = {item.id: item for item in objects}
        self._audits: list[AuditEntry] = []
        self._idempotent_results: dict[tuple[str, str], BusinessObject] = {}
        self._lock = RLock()

    def query(self, query: BusinessQuery, context: AdminContext) -> Page:
        self._require(context, VIEW_PERMISSION)
        if not 1 <= query.limit <= 200:
            raise ValueError("INVALID_PAGE_LIMIT")
        requested_tenants = frozenset({query.tenant_id}) if query.tenant_id else context.tenant_ids
        cross_tenants = requested_tenants - context.tenant_ids
        if not requested_tenants <= context.tenant_ids:
            if not context.access_reason or query.accessed_at is None or context.approval is None:
                raise CrossTenantApprovalRequired("cross-tenant query reason and approval are required")
            if any(
                not context.approval.authorizes(tenant_id=tenant_id, action="query", now=query.accessed_at)
                for tenant_id in requested_tenants - context.tenant_ids
            ):
                raise CrossTenantApprovalRequired("cross-tenant query approval is invalid")
        items = [
            item for item in self._objects.values() if item.kind == query.kind and item.tenant_id in requested_tenants
        ]
        if query.statuses:
            items = [item for item in items if item.status in query.statuses]
        if query.search:
            needle = query.search.casefold()
            items = [item for item in items if needle in item.id.casefold() or needle in item.name.casefold()]
        items.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        total = len(items)
        if query.cursor:
            cursor_key = _decode_cursor(query.cursor)
            items = [item for item in items if (item.updated_at, item.id) < cursor_key]
        selected = items[: query.limit]
        next_cursor = _encode_cursor(selected[-1]) if len(items) > query.limit else None
        if cross_tenants:
            assert context.approval is not None and query.accessed_at is not None and context.access_reason is not None
            with self._lock:
                for tenant_id in sorted(cross_tenants):
                    self._audits.append(
                        AuditEntry(
                            id=f"audit-{len(self._audits) + 1}",
                            request_id=context.request_id,
                            actor_id=context.actor_id,
                            tenant_id=tenant_id,
                            object_kind=query.kind,
                            object_id="*",
                            action="cross_tenant_query",
                            reason=context.access_reason,
                            before={},
                            after={"result_count": len(selected)},
                            result="succeeded",
                            occurred_at=query.accessed_at,
                            approval_id=context.approval.approval_id,
                        )
                    )
        return Page(tuple(_redact(item) for item in selected), next_cursor, total)

    def dashboard(self, context: AdminContext) -> Dashboard:
        self._require(context, VIEW_PERMISSION)
        visible = [item for item in self._objects.values() if item.tenant_id in context.tenant_ids]
        content: dict[str, int] = {}
        for item in visible:
            if item.kind == "content":
                content[item.status] = content.get(item.status, 0) + 1
        generated_at = max((item.updated_at for item in visible), default=datetime.min.replace(tzinfo=UTC))
        return Dashboard(
            total_users=sum(item.kind == "user" for item in visible),
            active_projects=sum(item.kind == "project" and item.status == "active" for item in visible),
            running_tasks=sum(
                item.kind == "task" and item.status in {"queued", "running", "cancelling"} for item in visible
            ),
            content_by_status=content,
            generated_at=generated_at,
        )

    def change_status(self, command: StatusChangeCommand, context: AdminContext) -> BusinessObject:
        self._require(context, MANAGE_PERMISSION)
        if not command.idempotency_key.strip():
            raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
        with self._lock:
            key = (context.actor_id, command.idempotency_key)
            if key in self._idempotent_results:
                return _redact(self._idempotent_results[key])
            current = self._objects.get(command.object_id)
            if current is None:
                raise ObjectNotFound(command.object_id)
            approval_id = self._authorize_write(current, command, context)
            if current.version != command.expected_version:
                self._audit_rejection(current, command, context, approval_id, "version_conflict")
                raise VersionConflict(command.object_id)
            allowed = _TRANSITIONS.get(current.kind, {}).get(current.status, frozenset())
            if command.target_status not in allowed:
                self._audit_rejection(current, command, context, approval_id, "invalid_transition")
                raise InvalidTransition(f"{current.status} -> {command.target_status}")
            changed = current.with_status(command.target_status, command.requested_at)
            audit = AuditEntry(
                id=f"audit-{len(self._audits) + 1}",
                request_id=context.request_id,
                actor_id=context.actor_id,
                tenant_id=current.tenant_id,
                object_kind=current.kind,
                object_id=current.id,
                action="change_status",
                reason=command.reason,
                before={"status": current.status, "version": current.version},
                after={"status": changed.status, "version": changed.version},
                result="succeeded",
                occurred_at=command.requested_at,
                approval_id=approval_id,
            )
            self._objects[current.id] = changed
            self._audits.append(audit)
            self._idempotent_results[key] = changed
            return _redact(changed)

    def audit_entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._audits)

    def _audit_rejection(
        self,
        current: BusinessObject,
        command: StatusChangeCommand,
        context: AdminContext,
        approval_id: str | None,
        result: str,
    ) -> None:
        self._audits.append(
            AuditEntry(
                id=f"audit-{len(self._audits) + 1}",
                request_id=context.request_id,
                actor_id=context.actor_id,
                tenant_id=current.tenant_id,
                object_kind=current.kind,
                object_id=current.id,
                action="change_status",
                reason=command.reason,
                before={"status": current.status, "version": current.version},
                after={"requested_status": command.target_status, "expected_version": command.expected_version},
                result=result,
                occurred_at=command.requested_at,
                approval_id=approval_id,
            )
        )

    @staticmethod
    def _require(context: AdminContext, permission: str) -> None:
        if permission not in context.permissions:
            raise PermissionDenied(permission)

    @staticmethod
    def _authorize_write(current: BusinessObject, command: StatusChangeCommand, context: AdminContext) -> str | None:
        if current.tenant_id in context.tenant_ids:
            return None
        if not context.access_reason or not context.access_reason.strip():
            raise CrossTenantApprovalRequired("cross-tenant reason is required")
        approval = context.approval
        if approval is None or not approval.authorizes(
            tenant_id=current.tenant_id, action="change_status", now=command.requested_at
        ):
            raise CrossTenantApprovalRequired("valid approval context is required")
        return approval.approval_id
