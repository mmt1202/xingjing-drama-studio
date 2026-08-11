from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, Self

from .errors import ContractViolation
from .models import AssetReference, Storyboard


@dataclass(frozen=True, slots=True)
class StoryboardScope:
    tenant_id: str
    workspace_id: str
    project_id: str


@dataclass(frozen=True, slots=True)
class AssetReferenceFailure:
    asset_id: str
    asset_version_id: str
    code: str


class AssetReferenceValidator(Protocol):
    """M04 资产查询/授权能力的生产接线端口。"""

    def validate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        references: tuple[AssetReference, ...],
    ) -> tuple[AssetReferenceFailure, ...]: ...


class IdentifierFactory(Protocol):
    def new(self, kind: str) -> str: ...


@dataclass(frozen=True, slots=True)
class AuditEvent:
    tenant_id: str
    workspace_id: str
    project_id: str
    request_id: str
    actor_id: str
    action: str
    object_id: str
    result: str
    occurred_at: datetime
    before: dict[str, object]
    after: dict[str, object]
    error_code: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.tenant_id,
            self.workspace_id,
            self.project_id,
            self.request_id,
            self.actor_id,
            self.action,
            self.object_id,
            self.result,
        )
        if any(not item.strip() for item in required):
            raise ContractViolation("AUDIT_FIELD_REQUIRED")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ContractViolation("TIMEZONE_REQUIRED")
        _json_copy(self.before)
        _json_copy(self.after)

    def to_dict(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "request_id": self.request_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "object_id": self.object_id,
            "result": self.result,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "before": _json_copy(self.before),
            "after": _json_copy(self.after),
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        occurred_at = datetime.fromisoformat(_text(value, "occurred_at").replace("Z", "+00:00"))
        return cls(
            tenant_id=_text(value, "tenant_id"),
            workspace_id=_text(value, "workspace_id"),
            project_id=_text(value, "project_id"),
            request_id=_text(value, "request_id"),
            actor_id=_text(value, "actor_id"),
            action=_text(value, "action"),
            object_id=_text(value, "object_id"),
            result=_text(value, "result"),
            occurred_at=occurred_at,
            before=_mapping_copy(value.get("before")),
            after=_mapping_copy(value.get("after")),
            error_code=_optional_text(value.get("error_code")),
        )


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    storyboard: Storyboard
    replayed: bool


class StoryboardRepository(Protocol):
    """可由 PostgreSQL/SQLAlchemy 实现替换的持久化事务边界。"""

    def replay(self, scope: StoryboardScope, *, idempotency_key: str, fingerprint: str) -> Storyboard | None: ...

    def replay_export(
        self, scope: StoryboardScope, *, idempotency_key: str, fingerprint: str
    ) -> dict[str, object] | None: ...

    def commit_export(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        fingerprint: str,
        result: dict[str, object],
        audit: AuditEvent,
    ) -> tuple[dict[str, object], bool]: ...

    def commit(
        self,
        scope: StoryboardScope,
        storyboard: Storyboard,
        *,
        expected_version: int | None,
        idempotency_key: str,
        fingerprint: str,
        audit: AuditEvent,
    ) -> CommitOutcome: ...

    def get(self, scope: StoryboardScope, storyboard_id: str) -> Storyboard | None: ...

    def list(self, scope: StoryboardScope) -> tuple[Storyboard, ...]: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def list_audit(self, scope: StoryboardScope, *, storyboard_id: str | None = None) -> tuple[AuditEvent, ...]: ...


def _text(value: Mapping[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise ContractViolation("INVALID_PERSISTED_STATE")
    return raw


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractViolation("INVALID_PERSISTED_STATE")
    return value


def _mapping_copy(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractViolation("INVALID_PERSISTED_STATE")
    return _json_copy({str(key): item for key, item in value.items()})


def _json_copy(value: object) -> dict[str, object]:
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise ContractViolation("NON_SERIALIZABLE_CONTRACT") from error
    if not isinstance(copied, dict):
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT")
    return copied
