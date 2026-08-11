from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

from fastapi import HTTPException, status
from starlette.requests import Request

from server.xingjing_editing import AccessContext, Permission


def signature_payload(body: bytes, tenant_id: str, workspace_id: str) -> bytes:
    """Bind the provider callback body to its tenant and workspace scope."""
    return b"\n".join((tenant_id.encode(), workspace_id.encode(), body))


class HmacRenderCallbackContextResolver:
    """Accept renderer callbacks only when scope headers and body share a valid HMAC."""

    def __init__(self, *, secret: str) -> None:
        self._secret = secret

    async def __call__(self, request: Request) -> AccessContext:
        if not self._secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "RENDERER_CALLBACK_UNAVAILABLE"},
            )
        tenant_id = request.headers.get("X-Renderer-Tenant-Id", "").strip()
        workspace_id = request.headers.get("X-Renderer-Workspace-Id", "").strip()
        signature = request.headers.get("X-Renderer-Signature", "").strip()
        body = await request.body()
        expected = hmac.new(
            self._secret.encode(),
            signature_payload(body, tenant_id, workspace_id),
            hashlib.sha256,
        ).hexdigest()
        if not tenant_id or not workspace_id or not hmac.compare_digest(expected, signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_RENDERER_CALLBACK"},
            )
        return AccessContext(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id="renderer-callback",
            request_id=str(uuid4()),
            permissions=frozenset({Permission.FINAL_MANAGE}),
        )
