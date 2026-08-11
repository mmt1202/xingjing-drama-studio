"""
API Key 管理路由

提供 API Key 的创建、列表查询和删除接口。
"""

import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lib.db import async_session_factory
from lib.db.models.user import User
from lib.db.repositories.api_key_repository import ApiKeyRepository
from lib.httpx_shared import get_http_client
from lib.i18n import Translator
from server.auth import (
    API_KEY_PREFIX,
    CurrentUser,
    CurrentUserInfo,
    _hash_api_key,
    invalidate_api_key_cache,
)
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)

router = APIRouter()
_context_resolver = TrustedWorkspaceContextResolver(PlatformSessionGateway())
_API_KEY_SCOPES = frozenset(
    {
        "script.view",
        "script.manage",
        "asset.view",
        "asset.manage",
        "generation.view",
        "generation.manage",
        "audio.view",
        "audio.manage",
        "shot.view",
        "shot.manage",
        "final.view",
        "final.manage",
        "compliance.view",
        "export.view",
    }
)


def _require_jwt_auth(user: CurrentUserInfo, _t: Callable[..., str]) -> None:
    """确保请求通过 JWT 认证（非 API Key）。API Key 管理操作不允许由 API Key 本身执行。"""
    if user.sub.startswith("apikey:"):
        raise HTTPException(status_code=403, detail=_t("jwt_auth_required"))


API_KEY_DEFAULT_EXPIRY_DAYS = 30


def _generate_api_key() -> str:
    """生成格式为 arc-<32位随机字符> 的 API Key。"""
    random_part = secrets.token_hex(16)  # 32 hex chars
    return f"{API_KEY_PREFIX}{random_part}"


def _default_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=API_KEY_DEFAULT_EXPIRY_DAYS)


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_days: int | None = Field(None, ge=0, le=3650)  # None 使用默认 30 天，0 表示不过期
    scopes: list[str] = Field(min_length=1, max_length=32)


class RotateApiKeyRequest(BaseModel):
    expected_version: int = Field(ge=1)
    expires_days: int | None = Field(None, ge=0, le=3650)
    scopes: list[str] = Field(min_length=1, max_length=32)


class CreateApiKeyResponse(BaseModel):
    id: int
    name: str
    key: str  # 完整 key，仅在创建时返回
    key_prefix: str
    created_at: str
    expires_at: str | None
    workspace_id: str
    scopes: list[str]
    version: int


class ApiKeyInfo(BaseModel):
    id: int
    name: str
    key_prefix: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None
    workspace_id: str
    scopes: list[str]
    version: int


async def _trusted_manage_context(request: Request, user: CurrentUserInfo) -> TrustedWorkspaceContext:
    context = await _context_resolver(request)
    if context.actor_id != user.id or "settings.manage" not in context.permissions:
        raise HTTPException(status_code=403, detail={"code": "API_KEY_MANAGE_FORBIDDEN"})
    return context


async def _ensure_identity_user(session, user: CurrentUserInfo) -> None:
    if await session.scalar(select(User.id).where(User.id == user.id)) is None:
        session.add(User(id=user.id, username=f"xingjing:{user.id}", role="user", is_active=True))


async def _consume_step_up(request: Request, purpose: str) -> None:
    authorization = request.headers.get("Authorization", "")
    step_up_token = request.headers.get("X-Step-Up-Token", "")
    base_url = os.environ.get("PLATFORM_IDENTITY_URL", "").rstrip("/")
    if not authorization or not step_up_token or not base_url:
        raise HTTPException(status_code=403, detail={"code": "STEP_UP_REQUIRED"})
    try:
        response = await get_http_client().post(
            f"{base_url}/api/v1/account/step-up/consume",
            headers={
                "Authorization": authorization,
                "X-Step-Up-Token": step_up_token,
                "X-Request-Id": request.headers.get("X-Request-Id", ""),
            },
            json={"purpose": purpose},
        )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"}) from error
    if response.status_code in (401, 403):
        raise HTTPException(status_code=403, detail={"code": "STEP_UP_REQUIRED"})
    if response.is_error:
        raise HTTPException(status_code=503, detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"})


def _validated_scopes(scopes: list[str], context: TrustedWorkspaceContext) -> list[str]:
    normalized = sorted({scope.strip() for scope in scopes if scope.strip()})
    if not normalized or any(scope not in _API_KEY_SCOPES for scope in normalized):
        raise HTTPException(status_code=422, detail={"code": "INVALID_API_KEY_SCOPE"})
    if any(scope not in context.permissions for scope in normalized):
        raise HTTPException(status_code=403, detail={"code": "API_KEY_SCOPE_FORBIDDEN"})
    return normalized


def _audit_payload(row: dict[str, Any]) -> dict[str, object]:
    expires_at = row["expires_at"]
    if isinstance(expires_at, datetime):
        expires_at = expires_at.isoformat()
    return {
        "id": row["id"],
        "name": row["name"],
        "key_prefix": row["key_prefix"],
        "workspace_id": row["workspace_id"],
        "scopes": row["scopes"],
        "expires_at": expires_at,
        "version": row["version"],
    }


@router.post("/api-keys", status_code=201)
async def create_api_key(
    request: Request,
    body: CreateApiKeyRequest,
    user: CurrentUser,
    _t: Translator,
) -> CreateApiKeyResponse:
    """创建新 API Key。完整 key 仅在响应中出现一次，之后无法再查看。"""
    _require_jwt_auth(user, _t)
    context = await _trusted_manage_context(request, user)
    scopes = _validated_scopes(body.scopes, context)
    await _consume_step_up(request, "api_key.create")
    key = _generate_api_key()
    key_hash = _hash_api_key(key)
    key_prefix = key[:8]  # e.g. "arc-abcd"

    if body.expires_days == 0:
        expires_at: datetime | None = None
    elif body.expires_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=body.expires_days)
    else:
        expires_at = _default_expires_at()

    try:
        async with async_session_factory() as session:
            async with session.begin():
                await _ensure_identity_user(session, user)
                repo = ApiKeyRepository(session)
                row = await repo.create(
                    name=body.name.strip(),
                    key_hash=key_hash,
                    key_prefix=key_prefix,
                    workspace_id=context.workspace_id,
                    scopes=scopes,
                    expires_at=expires_at,
                    user_id=user.id,
                )
                await repo.append_audit(
                    event_id=str(uuid4()),
                    request_id=context.request_id,
                    user_id=user.id,
                    workspace_id=context.workspace_id,
                    action="api_key.create",
                    api_key_id=row["id"],
                    before_payload=None,
                    after_payload=_audit_payload(row),
                )
    except IntegrityError:
        raise HTTPException(status_code=409, detail=_t("api_key_name_exists", name=body.name))

    return CreateApiKeyResponse(
        id=row["id"],
        name=row["name"],
        key=key,
        key_prefix=row["key_prefix"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        workspace_id=row["workspace_id"],
        scopes=row["scopes"],
        version=row["version"],
    )


@router.get("/api-keys")
async def list_api_keys(
    request: Request,
    user: CurrentUser,
    _t: Translator,
) -> list[ApiKeyInfo]:
    """查询所有 API Key 的元数据（不含完整 key）。"""
    _require_jwt_auth(user, _t)
    context = await _trusted_manage_context(request, user)
    async with async_session_factory() as session:
        async with session.begin():
            repo = ApiKeyRepository(session)
            rows = await repo.list_all(user_id=user.id, workspace_id=context.workspace_id)

    return [ApiKeyInfo(**row) for row in rows]


@router.get("/api-keys/audit")
async def list_api_key_audit(
    request: Request,
    user: CurrentUser,
    _t: Translator,
    request_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    _require_jwt_auth(user, _t)
    context = await _trusted_manage_context(request, user)
    bounded_limit = max(1, min(limit, 200))
    async with async_session_factory() as session:
        async with session.begin():
            return await ApiKeyRepository(session).list_audit(
                user_id=user.id,
                workspace_id=context.workspace_id,
                request_id=request_id,
                limit=bounded_limit,
            )


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(
    request: Request,
    key_id: int,
    expected_version: int,
    user: CurrentUser,
    _t: Translator,
) -> None:
    """删除（吊销）指定 API Key，并立即清除内存缓存。"""
    _require_jwt_auth(user, _t)
    context = await _trusted_manage_context(request, user)
    await _consume_step_up(request, "api_key.revoke")
    async with async_session_factory() as session:
        async with session.begin():
            repo = ApiKeyRepository(session)
            row = await repo.get_by_id(key_id, user_id=user.id, workspace_id=context.workspace_id)
            if row is None:
                raise HTTPException(status_code=404, detail=_t("api_key_not_found", key_id=key_id))
            key_hash = row["key_hash"]
            # 先失效缓存再删库：即使事务提交后崩溃，缓存也已清除，
            # 不会出现 DB 已删但缓存仍有效的宽限窗口。
            invalidate_api_key_cache(key_hash)
            deleted = await repo.revoke(
                key_id,
                user_id=user.id,
                workspace_id=context.workspace_id,
                expected_version=expected_version,
            )
            if deleted:
                await repo.append_audit(
                    event_id=str(uuid4()),
                    request_id=context.request_id,
                    user_id=user.id,
                    workspace_id=context.workspace_id,
                    action="api_key.revoke",
                    api_key_id=key_id,
                    before_payload=_audit_payload(row),
                    after_payload={**_audit_payload(row), "version": expected_version + 1, "revoked": True},
                )

    if not deleted:
        raise HTTPException(status_code=409, detail={"code": "VERSION_CONFLICT"})


@router.post("/api-keys/{key_id}/rotate", status_code=201)
async def rotate_api_key(
    request: Request,
    key_id: int,
    body: RotateApiKeyRequest,
    user: CurrentUser,
    _t: Translator,
) -> CreateApiKeyResponse:
    _require_jwt_auth(user, _t)
    context = await _trusted_manage_context(request, user)
    scopes = _validated_scopes(body.scopes, context)
    await _consume_step_up(request, "api_key.rotate")
    if body.expires_days == 0:
        expires_at: datetime | None = None
    elif body.expires_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=body.expires_days)
    else:
        expires_at = _default_expires_at()
    key = _generate_api_key()
    key_hash = _hash_api_key(key)
    key_prefix = key[:8]
    async with async_session_factory() as session:
        async with session.begin():
            repo = ApiKeyRepository(session)
            previous = await repo.get_by_id(key_id, user_id=user.id, workspace_id=context.workspace_id)
            if previous is None:
                raise HTTPException(status_code=404, detail=_t("api_key_not_found", key_id=key_id))
            if not await repo.revoke(
                key_id,
                user_id=user.id,
                workspace_id=context.workspace_id,
                expected_version=body.expected_version,
            ):
                raise HTTPException(status_code=409, detail={"code": "VERSION_CONFLICT"})
            row = await repo.create(
                name=previous["name"],
                key_hash=key_hash,
                key_prefix=key_prefix,
                workspace_id=context.workspace_id,
                scopes=scopes,
                expires_at=expires_at,
                user_id=user.id,
                rotated_from_id=key_id,
            )
            await repo.append_audit(
                event_id=str(uuid4()),
                request_id=context.request_id,
                user_id=user.id,
                workspace_id=context.workspace_id,
                action="api_key.rotate",
                api_key_id=row["id"],
                before_payload=_audit_payload(previous),
                after_payload=_audit_payload(row),
            )
    invalidate_api_key_cache(previous["key_hash"])
    return CreateApiKeyResponse(
        id=row["id"],
        name=row["name"],
        key=key,
        key_prefix=row["key_prefix"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        workspace_id=row["workspace_id"],
        scopes=row["scopes"],
        version=row["version"],
    )
