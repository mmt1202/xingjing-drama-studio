from collections.abc import Callable, Sequence
from threading import RLock
from typing import TypeVar, cast

from .models import AdminRole, Appeal, ApprovalRequest, AuditEvent, NotFoundError, ReviewRecord, RuleVersion

T = TypeVar("T")


class InMemoryGovernanceAdapter:
    """原子内存适配器：用于契约测试与单进程组合根。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._idempotency: dict[tuple[str, str], object] = {}
        self._rules: dict[tuple[str, str], RuleVersion] = {}
        self._current_rules: dict[str, str] = {}
        self._reviews: dict[tuple[str, str], ReviewRecord] = {}
        self._appeals: dict[tuple[str, str], Appeal] = {}
        self._approvals: dict[tuple[str, str], ApprovalRequest] = {}
        self._roles: dict[tuple[str, str], AdminRole] = {}
        self._audit: list[AuditEvent] = []

    def atomic(self, tenant_id: str, idempotency_key: str, operation: Callable[[], T]) -> T:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        key = (tenant_id, idempotency_key)
        with self._lock:
            if key in self._idempotency:
                return cast(T, self._idempotency[key])
            snapshot = (
                self._rules.copy(),
                self._current_rules.copy(),
                self._reviews.copy(),
                self._appeals.copy(),
                self._approvals.copy(),
                self._roles.copy(),
                self._audit.copy(),
            )
            try:
                result = operation()
            except Exception:
                (
                    self._rules,
                    self._current_rules,
                    self._reviews,
                    self._appeals,
                    self._approvals,
                    self._roles,
                    self._audit,
                ) = snapshot
                raise
            self._idempotency[key] = result
            return result

    def next_rule_sequence(self, tenant_id: str) -> int:
        return 1 + sum(version.tenant_id == tenant_id for version in self._rules.values())

    def save_rule_version(self, version: RuleVersion) -> None:
        key = (version.tenant_id, version.id)
        if key in self._rules:
            raise ValueError("published rule versions are immutable")
        self._rules[key] = version
        self._current_rules[version.tenant_id] = version.id

    def current_rule_version(self, tenant_id: str) -> RuleVersion | None:
        version_id = self._current_rules.get(tenant_id)
        return self._rules.get((tenant_id, version_id)) if version_id else None

    def get_rule_version(self, tenant_id: str, version_id: str) -> RuleVersion:
        return self._get(self._rules, (tenant_id, version_id), "rule version")

    def save_review(self, review: ReviewRecord) -> None:
        self._reviews[(review.tenant_id, review.id)] = review

    def get_review(self, tenant_id: str, review_id: str) -> ReviewRecord:
        return self._get(self._reviews, (tenant_id, review_id), "review")

    def list_reviews(self, tenant_id: str, *, status: str | None = None) -> Sequence[ReviewRecord]:
        return tuple(
            item
            for (item_tenant, _), item in self._reviews.items()
            if item_tenant == tenant_id and (status is None or item.status.value == status)
        )

    def save_appeal(self, appeal: Appeal) -> None:
        self._appeals[(appeal.tenant_id, appeal.id)] = appeal

    def get_appeal(self, tenant_id: str, appeal_id: str) -> Appeal:
        return self._get(self._appeals, (tenant_id, appeal_id), "appeal")

    def save_approval(self, approval: ApprovalRequest) -> None:
        self._approvals[(approval.tenant_id, approval.id)] = approval

    def get_approval(self, tenant_id: str, approval_id: str) -> ApprovalRequest:
        return self._get(self._approvals, (tenant_id, approval_id), "approval")

    def save_role(self, role: AdminRole) -> None:
        self._roles[(role.tenant_id, role.id)] = role

    def get_role(self, tenant_id: str, role_id: str) -> AdminRole:
        return self._get(self._roles, (tenant_id, role_id), "role")

    def append_audit(self, event: AuditEvent) -> None:
        self._audit.append(event)

    @staticmethod
    def _get(store: dict[tuple[str, str], T], key: tuple[str, str], label: str) -> T:
        try:
            return store[key]
        except KeyError as exc:
            raise NotFoundError(f"{label} not found") from exc

    def query_audit(
        self,
        tenant_id: str,
        *,
        actor_id: str | None = None,
        object_id: str | None = None,
        request_id: str | None = None,
    ) -> Sequence[AuditEvent]:
        return tuple(
            event
            for event in self._audit
            if event.tenant_id == tenant_id
            and (actor_id is None or event.actor_id == actor_id)
            and (object_id is None or event.object_id == object_id)
            and (request_id is None or event.request_id == request_id)
        )
