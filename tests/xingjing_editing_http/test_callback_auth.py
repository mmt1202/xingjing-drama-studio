from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.xingjing_editing import Permission
from server.xingjing_editing_http.callback_auth import HmacRenderCallbackContextResolver, signature_payload


def _request(*, body: bytes, signature: str) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/render-tasks/task-1/callbacks",
            "headers": [
                (b"x-renderer-signature", signature.encode()),
                (b"x-renderer-tenant-id", b"tenant-1"),
                (b"x-renderer-workspace-id", b"workspace-1"),
                (b"x-request-id", b"renderer-request-1"),
            ],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_signed_callback_derives_scope_from_signed_provider_headers() -> None:
    body = b'{"event_id":"event-1"}'
    secret = "renderer-callback-secret"
    signature = hmac.new(secret.encode(), signature_payload(body, "tenant-1", "workspace-1"), hashlib.sha256).hexdigest()

    context = await HmacRenderCallbackContextResolver(secret=secret)(_request(body=body, signature=signature))

    assert (context.tenant_id, context.workspace_id, context.actor_id, context.request_id) == (
        "tenant-1",
        "workspace-1",
        "renderer-callback",
        "renderer-request-1",
    )
    assert context.permissions == frozenset({Permission.FINAL_MANAGE})


@pytest.mark.asyncio
async def test_callback_rejects_tampered_payload() -> None:
    resolver = HmacRenderCallbackContextResolver(secret="renderer-callback-secret")

    with pytest.raises(HTTPException) as error:
        await resolver(_request(body=b'{"event_id":"tampered"}', signature="0" * 64))

    assert error.value.status_code == 401
    assert error.value.detail == {"code": "INVALID_RENDERER_CALLBACK"}
