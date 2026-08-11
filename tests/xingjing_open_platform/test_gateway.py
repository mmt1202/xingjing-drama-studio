from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_open_platform.api_keys import ApiKeyService, SecretHasher
from server.xingjing_open_platform.gateway import ExternalRequestGateway
from server.xingjing_open_platform.memory import InMemoryOpenPlatformStore
from server.xingjing_open_platform.models import RequestContext
from server.xingjing_open_platform.rate_limits import RateLimitPolicy

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def _gateway(*, billable: bool = True, compliant: bool = True):
    store = InMemoryOpenPlatformStore()
    key_service = ApiKeyService(store, SecretHasher(pepper=b"pepper"), clock=lambda: NOW)
    created = key_service.create(
        context=RequestContext("operator", "workspace-a", "create-1"),
        client_id="client-1",
        scopes={"projects:read"},
        expires_at=NOW + timedelta(days=1),
    )
    gateway = ExternalRequestGateway(
        keys=key_service,
        limiter=store,
        audits=store,
        billing_guard=lambda workspace_id: billable,
        compliance_guard=lambda workspace_id, operation: compliant,
        clock=lambda: NOW,
    )
    return gateway, created.secret, store


def test_gateway_authorizes_only_after_tenant_scope_rate_billing_and_compliance_checks() -> None:
    gateway, secret, store = _gateway()
    policy = RateLimitPolicy(limit=1, window=timedelta(minutes=1))

    access = gateway.authorize(
        secret=secret,
        workspace_id="workspace-a",
        scope="projects:read",
        operation="project.list",
        request_id="request-1",
        rate_policy=policy,
    )

    assert access.workspace_id == "workspace-a"
    assert store.audit_events[-1].outcome == "allowed"
    with pytest.raises(PermissionError, match="rate limit"):
        gateway.authorize(
            secret=secret,
            workspace_id="workspace-a",
            scope="projects:read",
            operation="project.list",
            request_id="request-2",
            rate_policy=policy,
        )
    assert store.audit_events[-1].outcome == "denied:rate_limit"


def test_rate_limit_adapter_returns_reset_and_remaining_decision() -> None:
    store = InMemoryOpenPlatformStore()

    decision = store.consume_rate_limit("key-1", "project.list", now=NOW, limit=2, window=timedelta(minutes=1))

    assert decision.allowed is True
    assert decision.remaining == 1
    assert decision.retry_at == NOW + timedelta(minutes=1)


@pytest.mark.parametrize(
    ("billable", "compliant", "reason"),
    [(False, True, "billing"), (True, False, "compliance")],
)
def test_gateway_cannot_bypass_billing_or_compliance(billable: bool, compliant: bool, reason: str) -> None:
    gateway, secret, store = _gateway(billable=billable, compliant=compliant)

    with pytest.raises(PermissionError, match=reason):
        gateway.authorize(
            secret=secret,
            workspace_id="workspace-a",
            scope="projects:read",
            operation="project.list",
            request_id="request-1",
            rate_policy=RateLimitPolicy(10, timedelta(minutes=1)),
        )

    assert store.audit_events[-1].request_id == "request-1"
