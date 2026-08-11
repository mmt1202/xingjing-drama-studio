"""
认证 API 路由

提供 OAuth2 登录和 token 验证接口。
"""

import logging
import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from lib.httpx_shared import get_http_client
from lib.i18n import Translator
from server.auth import (
    CurrentUser,
    check_credentials,
    create_token,
    is_auth_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Public endpoints must remain reachable before a caller has an access token.
public_router = APIRouter()

_IDENTITY_FORWARDED_HEADERS = (
    "authorization",
    "content-type",
    "idempotency-key",
    "x-request-id",
    "user-agent",
    "x-step-up-token",
)


async def _proxy_identity(request: Request, path: str) -> Response:
    base_url = os.environ.get("PLATFORM_IDENTITY_URL", "").rstrip("/")
    if not base_url:
        raise HTTPException(status_code=503, detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"})
    headers = {name: value for name in _IDENTITY_FORWARDED_HEADERS if (value := request.headers.get(name)) is not None}
    try:
        upstream = await get_http_client().request(
            request.method,
            f"{base_url}{path}",
            headers=headers,
            content=await request.body(),
            timeout=15,
        )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"}) from error
    response_headers: dict[str, str] = {}
    if request_id := upstream.headers.get("X-Request-Id"):
        response_headers["X-Request-Id"] = request_id
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", "application/json").split(";", 1)[0],
        headers=response_headers,
    )


# ==================== 响应模型 ====================


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class VerifyResponse(BaseModel):
    valid: bool
    username: str


class AuthStatusResponse(BaseModel):
    enabled: bool


# ==================== 路由 ====================


@public_router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status():
    """暴露 ``AUTH_ENABLED`` 状态供前端 bootstrap 判断是否需要登录拦截。

    前端 ``auth-store.initialize()`` 在 localStorage 无 token 时调用本接口：
    ``enabled=false`` 时跳过登录页直接进主界面；``enabled=true`` 时保留原
    登录链路。本接口本身**不要求认证**——一个 boolean 比 401 探针更直观，
    且实际"是否需要登录"通过 401/200 也能从外部观察到，因此不增量泄露。
    """
    return AuthStatusResponse(enabled=is_auth_enabled())


@public_router.post("/auth/register")
async def platform_register(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/auth/register")


@public_router.post("/auth/login")
async def platform_login(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/auth/login")


@public_router.post("/auth/refresh")
async def platform_refresh(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/auth/refresh")


@public_router.post("/auth/oidc")
async def platform_oidc_login(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/auth/oidc")


@router.post("/auth/logout")
async def platform_logout(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/auth/logout")


@public_router.post("/auth/password-reset/request")
async def platform_password_reset_request(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/auth/password-reset/request")


@public_router.post("/auth/password-reset/confirm")
async def platform_password_reset_confirm(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/auth/password-reset/confirm")


@router.get("/account/profile")
async def platform_profile(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/profile")


@router.get("/account/export")
async def platform_export_account(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/export")


@router.get("/account/sessions")
async def platform_sessions(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/sessions")


@router.get("/account/security-events")
async def platform_security_events(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/security-events")


@router.post("/account/cancel")
async def platform_cancel_account(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/cancel")


@router.get("/account/cancellation-impact")
async def platform_account_cancellation_impact(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/cancellation-impact")


@router.get("/account/oidc-bindings")
async def platform_oidc_bindings(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/oidc-bindings")


@router.post("/account/oidc-bindings")
async def platform_bind_oidc(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/oidc-bindings")


@router.delete("/account/oidc-bindings/{binding_id}")
async def platform_revoke_oidc(request: Request, binding_id: str) -> Response:
    return await _proxy_identity(request, f"/api/v1/account/oidc-bindings/{binding_id}")


@router.post("/account/step-up")
async def platform_step_up(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/step-up")


@router.post("/account/step-up/consume")
async def platform_consume_step_up(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/step-up/consume")


@router.delete("/account/sessions/{session_id}")
async def platform_revoke_session(request: Request, session_id: str) -> Response:
    return await _proxy_identity(request, f"/api/v1/account/sessions/{session_id}")


@router.post("/account/sessions/revoke-others")
async def platform_revoke_other_sessions(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/account/sessions/revoke-others")


@router.get("/session/context")
async def platform_session_context(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/session/context")


@router.put("/session/context/workspace")
async def platform_select_workspace(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/session/context/workspace")


@router.post("/workspaces")
async def platform_create_workspace(request: Request) -> Response:
    return await _proxy_identity(request, "/api/v1/workspaces")


@router.post("/workspaces/{workspace_id}/archive")
async def platform_archive_workspace(request: Request, workspace_id: str) -> Response:
    return await _proxy_identity(request, f"/api/v1/workspaces/{workspace_id}/archive")


@router.post("/workspaces/{workspace_id}/transfer-ownership")
async def platform_transfer_workspace_ownership(request: Request, workspace_id: str) -> Response:
    return await _proxy_identity(request, f"/api/v1/workspaces/{workspace_id}/transfer-ownership")


@public_router.post("/auth/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    _t: Translator,
):
    """用户登录

    使用 OAuth2 标准表单格式验证凭据，成功返回 access_token。
    ``AUTH_ENABLED=false`` 时跳过凭据校验，直接签发 token，让前端
    LoginPage 即便被打开也能正常跳转主界面。
    """
    if is_auth_enabled() and not check_credentials(form_data.username, form_data.password):
        logger.warning("登录失败: 用户名或密码错误 (用户: %s)", form_data.username)
        raise HTTPException(
            status_code=401,
            detail=_t("unauthorized"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_token(form_data.username)
    logger.info("用户登录成功: %s", form_data.username)
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/auth/verify", response_model=VerifyResponse)
async def verify(
    current_user: CurrentUser,
):
    """验证 token 有效性

    使用 OAuth2 Bearer token 依赖自动提取和验证 token。
    """
    return VerifyResponse(valid=True, username=current_user.sub)
