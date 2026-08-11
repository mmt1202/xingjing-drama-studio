from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.xingjing_identity_context import PlatformSessionGateway, TrustedWorkspaceContextResolver


def request_with_headers(**headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/templates",
            "raw_path": b"/api/v1/templates",
            "query_string": b"",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


@pytest.mark.asyncio
async def test_context_is_derived_from_platform_session_and_ignores_forged_client_identity_headers() -> None:
    def platform_response(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://identity.local/api/v1/session/context")
        assert request.headers["Authorization"] == "Bearer real-session-token"
        assert request.headers["X-Request-Id"] == "request-123"
        return httpx.Response(
            200,
            json={
                "data": {
                    "user": {"id": "user-trusted", "email": "owner@example.com", "displayName": "Owner"},
                    "currentWorkspace": {
                        "id": "workspace-trusted",
                        "name": "可信工作区",
                        "slug": "trusted",
                        "status": "ACTIVE",
                        "role": "OWNER",
                        "version": 4,
                    },
                    "workspaces": [],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(platform_response)) as client:
        resolver = TrustedWorkspaceContextResolver(
            PlatformSessionGateway(base_url="http://identity.local", client=client)
        )
        resolved = await resolver(
            request_with_headers(
                Authorization="Bearer real-session-token",
                **{
                    "X-Request-Id": "request-123",
                    "X-Actor-Id": "attacker",
                    "X-Workspace-Id": "workspace-attacker",
                    "X-Permissions": "admin.business.manage,template.manage",
                },
            )
        )

    assert resolved.actor_id == "user-trusted"
    assert resolved.workspace_id == "workspace-trusted"
    assert resolved.tenant_id == "workspace-trusted"
    assert "template.manage" in resolved.permissions
    assert "admin.business.manage" not in resolved.permissions


@pytest.mark.asyncio
async def test_context_rejects_session_without_active_workspace() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": {
                        "user": {"id": "user-1", "email": "member@example.com", "displayName": "Member"},
                        "currentWorkspace": None,
                        "workspaces": [],
                    }
                },
            )
        )
    ) as client:
        resolver = TrustedWorkspaceContextResolver(
            PlatformSessionGateway(base_url="http://identity.local", client=client)
        )
        with pytest.raises(HTTPException) as error:
            await resolver(request_with_headers(Authorization="Bearer valid-session"))

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "WORKSPACE_CONTEXT_REQUIRED"
