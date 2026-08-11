from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_marketplace import MarketplaceService
from server.xingjing_marketplace_http.runtime import MarketplaceRuntime
from tests.xingjing_marketplace.fakes import FakeMarketplaceRepository


def test_runtime_converts_trusted_workspace_context_before_mounting_marketplace_router() -> None:
    async def resolve_context(request) -> TrustedWorkspaceContext:
        return TrustedWorkspaceContext(
            tenant_id="workspace-1",
            workspace_id="workspace-1",
            actor_id="owner-1",
            request_id="request-1",
            permissions=frozenset({"template.manage", "template.view"}),
            role="OWNER",
        )

    runtime = MarketplaceRuntime(
        MarketplaceService(FakeMarketplaceRepository(), clock=lambda: datetime(2026, 7, 16, tzinfo=UTC)),
        context_resolver=resolve_context,
    )
    app = FastAPI()
    app.include_router(runtime.router())
    client = TestClient(app)

    response = client.post(
        "/api/v1/templates",
        headers={"X-Actor-Id": "attacker", "X-Permissions": "admin.business.manage", "Idempotency-Key": "create-1"},
        json={
            "title": "可信上下文模板",
            "kind": "project",
            "content": {"type": "drama"},
            "tags": ["测试"],
            "rights": {
                "scope": "public",
                "commercial_use": True,
                "attribution_required": False,
                "inheritable_scopes": ["storyboards"],
            },
            "revenue_shares": [{"beneficiary_role": "author", "basis_points": 10000}],
        },
    )

    assert response.status_code == 201
    template_id = response.json()["data"]["resource_id"]
    detail = client.get(f"/api/v1/templates/{template_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["workspace_id"] == "workspace-1"
