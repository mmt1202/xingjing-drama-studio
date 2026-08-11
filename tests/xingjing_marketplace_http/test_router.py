from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server.xingjing_marketplace import MarketplaceService, RequestContext
from server.xingjing_marketplace_http import create_dependencies, create_router
from tests.xingjing_marketplace.fakes import FakeMarketplaceRepository


def build_client() -> TestClient:
    app = FastAPI()
    def test_context(request: Request) -> RequestContext:
        return RequestContext(
            actor_id=request.headers.get("X-Actor-Id", "creator-1"),
            workspace_id=request.headers.get("X-Workspace-Id", "workspace-a"),
            permissions=frozenset(
                part.strip() for part in request.headers.get("X-Permissions", "").split(",") if part.strip()
            ),
            request_id=request.headers.get("X-Request-Id", "request-1"),
        )
    app.include_router(
        create_router(
            create_dependencies(
                MarketplaceService(FakeMarketplaceRepository(), clock=lambda: datetime(2026, 7, 16, tzinfo=UTC)),
                context_resolver=test_context,
            )
        )
    )
    return TestClient(app)


def headers(
    *permissions: str, workspace: str = "workspace-a", actor: str = "creator-1", request: str = "request-1"
) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace,
        "X-Actor-Id": actor,
        "X-Request-Id": request,
        "X-Permissions": ",".join(permissions),
        "Idempotency-Key": "create-template-1",
    }


def template_payload() -> dict[str, object]:
    return {
        "title": "都市悬疑项目模板",
        "kind": "project",
        "content": {"project_type": "drama"},
        "tags": ["悬疑", "都市"],
        "rights": {
            "scope": "public",
            "commercial_use": True,
            "attribution_required": True,
            "inheritable_scopes": ["storyboards"],
        },
        "revenue_shares": [
            {"beneficiary_role": "template_author", "basis_points": 8000},
            {"beneficiary_role": "platform", "basis_points": 2000},
        ],
        "price_minor": 1200,
    }


def test_create_template_and_read_back_uses_tenant_identity_permissions_and_idempotency() -> None:
    client = build_client()

    created = client.post("/api/v1/templates", headers=headers("template.manage"), json=template_payload())

    assert created.status_code == 201
    receipt = created.json()["data"]
    assert receipt["resource_type"] == "template"
    read_back = client.get(
        f"/api/v1/templates/{receipt['resource_id']}",
        headers=headers("template.view"),
    )
    assert read_back.status_code == 200
    assert read_back.json()["data"]["title"] == "都市悬疑项目模板"
    assert read_back.json()["data"]["workspace_id"] == "workspace-a"


def create_template(client: TestClient, *, key: str = "create-template") -> str:
    response = client.post(
        "/api/v1/templates",
        headers=headers("template.manage", request=key) | {"Idempotency-Key": key},
        json=template_payload(),
    )
    assert response.status_code == 201
    return response.json()["data"]["resource_id"]


def test_edit_review_decision_and_withdraw_require_preconditions_and_read_back_final_state() -> None:
    client = build_client()
    template_id = create_template(client)
    edited = client.put(
        f"/api/v1/templates/{template_id}/versions",
        headers=headers("template.manage") | {"If-Match": "1", "Idempotency-Key": "edit-1"},
        json={
            "content": {"project_type": "drama", "palette": "amber"},
            "rights": template_payload()["rights"],
            "revenue_shares": template_payload()["revenue_shares"],
            "price_minor": 1200,
        },
    )
    assert edited.status_code == 201
    stale = client.put(
        f"/api/v1/templates/{template_id}/versions",
        headers=headers("template.manage") | {"If-Match": "1", "Idempotency-Key": "edit-stale"},
        json={
            "content": {"project_type": "drama"},
            "rights": template_payload()["rights"],
            "revenue_shares": template_payload()["revenue_shares"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    submitted = client.post(
        f"/api/v1/templates/{template_id}/reviews",
        headers=headers("template.manage") | {"If-Match": "2", "Idempotency-Key": "submit-1"},
        json={"statement": "版权和素材来源已核验"},
    )
    assert submitted.status_code == 201
    decision = client.post(
        f"/api/v1/template-reviews/{submitted.json()['data']['resource_id']}/decision",
        headers=headers("admin.business.manage", actor="operator-1")
        | {"If-Match": "3", "Idempotency-Key": "approve-1"},
        json={"decision": "approve", "reason": "审核证据完整"},
    )
    assert decision.status_code == 200
    withdrawn = client.post(
        f"/api/v1/templates/{template_id}/withdraw",
        headers=headers("admin.business.manage", actor="operator-1")
        | {"If-Match": "4", "Idempotency-Key": "withdraw-1"},
        json={"reason": "授权策略调整"},
    )
    assert withdrawn.status_code == 200
    final = client.get(f"/api/v1/templates/{template_id}", headers=headers("template.view"))
    assert final.json()["data"]["publication_state"] == "withdrawn"


def test_market_filters_pagination_and_fork_community_lineage_use_real_persisted_records() -> None:
    client = build_client()
    template_id = create_template(client, key="source-create")
    submitted = client.post(
        f"/api/v1/templates/{template_id}/reviews",
        headers=headers("template.manage") | {"If-Match": "1", "Idempotency-Key": "source-submit"},
        json={"statement": "rights checked"},
    )
    client.post(
        f"/api/v1/template-reviews/{submitted.json()['data']['resource_id']}/decision",
        headers=headers("admin.business.manage", actor="operator-1")
        | {"If-Match": "2", "Idempotency-Key": "source-approve"},
        json={"decision": "approve", "reason": "approved"},
    )
    consumer = headers("community.view", "community.manage", workspace="workspace-b", actor="consumer-b")
    market = client.get(
        "/api/v1/market/items?search=%E9%83%BD%E5%B8%82&tag=%E6%82%AC%E7%96%91&page_size=1", headers=consumer
    )
    assert market.status_code == 200
    item = market.json()["data"]["items"][0]
    fork = client.post(
        "/api/v1/forks",
        headers=consumer | {"Idempotency-Key": "fork-1"},
        json={
            "market_item_id": item["id"],
            "expected_market_revision": item["revision"],
            "expected_source_version_id": item["source_version_id"],
            "project_name": "第一代二创",
            "intended_commercial_use": True,
            "requested_inheritable_scopes": ["storyboards"],
        },
    )
    assert fork.status_code == 201
    fork_id = fork.json()["data"]["resource_id"]
    detail = client.get(f"/api/v1/forks/{fork_id}", headers=consumer)
    assert detail.status_code == 200
    project_id = detail.json()["data"]["target_project_id"]
    assert client.get(f"/api/v1/fork-projects/{project_id}", headers=consumer).status_code == 200
    republished = client.post(
        "/api/v1/community-projects",
        headers=consumer | {"If-Match": "1", "Idempotency-Key": "community-1"},
        json={
            "fork_id": fork_id,
            "title": "社区二创",
            "tags": ["社区"],
            "rights": template_payload()["rights"],
            "revenue_shares": template_payload()["revenue_shares"],
            "price_minor": 300,
        },
    )
    assert republished.status_code == 201
    assert client.get(f"/api/v1/forks/{fork_id}/lineage", headers=consumer).json()["data"] == []


def test_errors_are_enveloped_and_cross_tenant_template_reads_do_not_leak() -> None:
    client = build_client()
    template_id = create_template(client)
    denied = client.get(f"/api/v1/templates/{template_id}", headers=headers("community.view"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    hidden = client.get(
        f"/api/v1/templates/{template_id}",
        headers=headers("template.view", workspace="workspace-b", actor="reader-b"),
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"


def test_idempotency_replay_and_conflict_and_missing_write_headers_are_explicit() -> None:
    client = build_client()
    request_headers = headers("template.manage") | {"Idempotency-Key": "same-create"}
    first = client.post("/api/v1/templates", headers=request_headers, json=template_payload())
    replay = client.post("/api/v1/templates", headers=request_headers, json=template_payload())
    conflict = client.post(
        "/api/v1/templates",
        headers=request_headers,
        json=template_payload() | {"title": "另一份模板"},
    )
    missing_key_headers = headers("template.manage")
    del missing_key_headers["Idempotency-Key"]
    missing_key = client.post("/api/v1/templates", headers=missing_key_headers, json=template_payload())

    assert replay.status_code == 201
    assert replay.json()["data"] == first.json()["data"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "INVALID_INPUT"


def test_router_uses_injected_trusted_context_not_client_supplied_identity_headers() -> None:
    app = FastAPI()
    trusted = RequestContext(
        actor_id="trusted-owner",
        workspace_id="trusted-workspace",
        permissions=frozenset({"template.manage", "template.view"}),
        request_id="trusted-request",
    )
    app.include_router(
        create_router(
            create_dependencies(
                MarketplaceService(FakeMarketplaceRepository(), clock=lambda: datetime(2026, 7, 16, tzinfo=UTC)),
                context_resolver=lambda request: trusted,
            )
        )
    )
    client = TestClient(app)

    created = client.post(
        "/api/v1/templates",
        headers={
            "X-Actor-Id": "attacker",
            "X-Workspace-Id": "attacker-workspace",
            "X-Permissions": "admin.business.manage",
            "Idempotency-Key": "trusted-create",
        },
        json=template_payload(),
    )

    assert created.status_code == 201
    template_id = created.json()["data"]["resource_id"]
    detail = client.get(
        f"/api/v1/templates/{template_id}",
        headers={"X-Actor-Id": "attacker", "X-Workspace-Id": "attacker-workspace", "X-Permissions": ""},
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["workspace_id"] == "trusted-workspace"
