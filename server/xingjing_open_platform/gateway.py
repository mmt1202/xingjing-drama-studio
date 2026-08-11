from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .api_keys import ApiKeyService
from .models import ApiPrincipal, AuditEvent
from .ports import AuditSink, RateLimiter
from .rate_limits import RateLimitPolicy


class ExternalRequestGateway:
    """外部调用的强制入口；业务 handler 只接收通过此处产生的主体。"""

    def __init__(
        self,
        *,
        keys: ApiKeyService,
        limiter: RateLimiter,
        audits: AuditSink,
        billing_guard: Callable[[str], bool],
        compliance_guard: Callable[[str, str], bool],
        clock: Callable[[], datetime],
    ) -> None:
        self._keys = keys
        self._limiter = limiter
        self._audits = audits
        self._billing_guard = billing_guard
        self._compliance_guard = compliance_guard
        self._clock = clock

    def authorize(
        self,
        *,
        secret: str,
        workspace_id: str,
        scope: str,
        operation: str,
        request_id: str,
        rate_policy: RateLimitPolicy,
    ) -> ApiPrincipal:
        now = self._clock()
        try:
            principal = self._keys.authenticate(secret, workspace_id=workspace_id, required_scope=scope)
            decision = self._limiter.consume_rate_limit(
                principal.api_key_id,
                operation,
                now=now,
                limit=rate_policy.limit,
                window=rate_policy.window,
            )
            if not decision.allowed:
                raise PermissionError("rate limit exceeded")
            if not self._billing_guard(workspace_id):
                raise PermissionError("billing is not eligible")
            if not self._compliance_guard(workspace_id, operation):
                raise PermissionError("compliance gate denied")
        except PermissionError as exc:
            reason = (
                "rate_limit"
                if "rate limit" in str(exc)
                else "billing"
                if "billing" in str(exc)
                else "compliance"
                if "compliance" in str(exc)
                else "authentication"
            )
            self._audit("unknown", workspace_id, request_id, f"denied:{reason}", now)
            raise
        self._audit(principal.client_id, workspace_id, request_id, "allowed", now)
        return principal

    def _audit(self, actor_id: str, workspace_id: str, request_id: str, outcome: str, now: datetime) -> None:
        self._audits.append_audit(
            AuditEvent("external_api.authorize", actor_id, workspace_id, "external-api", request_id, outcome, now)
        )
