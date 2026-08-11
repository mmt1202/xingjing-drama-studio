from __future__ import annotations

import inspect
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import scrypt, sha256
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)

type ContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]

ALLOWED_SCOPES = frozenset(
    {
        "projects:read",
        "projects:write",
        "tasks:read",
        "tasks:write",
        "assets:read",
        "assets:write",
        "webhooks:manage",
    }
)


class Base(DeclarativeBase):
    pass


class ApiClientRow(Base):
    __tablename__ = "xingjing_open_api_clients"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rate_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiCredentialRow(Base):
    __tablename__ = "xingjing_open_api_credentials"
    __table_args__ = (
        Index(
            "ix_xj_open_credential_client",
            "tenant_id",
            "workspace_id",
            "client_id",
            "created_at",
        ),
    )
    credential_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_to_id: Mapped[str | None] = mapped_column(String(64))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEndpointRow(Base):
    __tablename__ = "xingjing_open_webhook_endpoints"
    __table_args__ = (
        Index(
            "ix_xj_open_webhook_client",
            "tenant_id",
            "workspace_id",
            "client_id",
            "updated_at",
        ),
    )
    endpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    secret_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiUsageRow(Base):
    __tablename__ = "xingjing_open_api_usage"
    __table_args__ = (
        Index(
            "ix_xj_open_usage_query",
            "tenant_id",
            "workspace_id",
            "client_id",
            "occurred_at",
        ),
    )
    usage_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    credential_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpenPlatformCommandRow(Base):
    __tablename__ = "xingjing_open_platform_commands"
    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "idempotency_key",
            name="uq_xj_open_platform_command",
        ),
    )
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpenPlatformAuditRow(Base):
    __tablename__ = "xingjing_open_platform_audit"
    __table_args__ = (
        Index(
            "ix_xj_open_platform_audit_query",
            "workspace_id",
            "request_id",
            "occurred_at",
        ),
    )
    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionPayload(BaseModel):
    action: str
    objectId: str
    version: int
    resource: str | None = None
    reason: str | None = None
    payload: dict[str, object] | None = None


@dataclass(slots=True)
class OpenPlatformRuntime:
    router: APIRouter
    engine: Engine

    def close(self) -> None:
        self.engine.dispose()


def create_unavailable_open_platform_router(code: str) -> APIRouter:
    router = APIRouter(tags=["open-platform"])

    async def unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": code})

    router.add_api_route("/api/v1/admin/api-clients", unavailable, methods=["GET"])
    router.add_api_route("/api/v1/admin/api-clients/actions", unavailable, methods=["POST"])
    router.add_api_route("/api/v1/open/v1/context", unavailable, methods=["GET"])
    return router


def create_production_open_platform_runtime(
    *,
    database_url: str | None = None,
    context_resolver: ContextResolver | None = None,
) -> OpenPlatformRuntime:
    url = (
        database_url
        or os.environ.get("XINGJING_OPEN_PLATFORM_DATABASE_URL")
        or os.environ.get("XINGJING_GOVERNANCE_DATABASE_URL", "")
    ).strip()
    if not url:
        raise RuntimeError("XINGJING_OPEN_PLATFORM_DATABASE_URL_REQUIRED")
    if make_url(url).drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }:
        raise RuntimeError("XINGJING_OPEN_PLATFORM_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    pepper = os.environ.get("XINGJING_OPEN_PLATFORM_SECRET_PEPPER", "").encode()
    if len(pepper) < 32:
        raise RuntimeError("XINGJING_OPEN_PLATFORM_SECRET_PEPPER_REQUIRED")
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as session:
            for table_name in (
                "xingjing_open_api_clients",
                "xingjing_open_api_credentials",
                "xingjing_open_webhook_endpoints",
                "xingjing_open_api_usage",
                "xingjing_open_platform_commands",
                "xingjing_open_platform_audit",
            ):
                session.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
    except SQLAlchemyError as error:
        engine.dispose()
        raise RuntimeError("XINGJING_OPEN_PLATFORM_MIGRATION_REQUIRED") from error
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    return OpenPlatformRuntime(_router(sessions, resolver, pepper), engine)


def _router(
    sessions: sessionmaker[Session],
    resolver: ContextResolver,
    pepper: bytes,
) -> APIRouter:
    router = APIRouter(tags=["open-platform"])

    async def resolve(request: Request) -> TrustedWorkspaceContext:
        value = resolver(request)
        return await value if inspect.isawaitable(value) else value

    @router.get("/api/v1/admin/api-clients")
    def list_clients(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        query: str | None = None,
        status: str | None = None,
        request_id: str | None = Query(None, alias="requestId"),
        actor_id: str | None = Query(None, alias="actorId"),
        object_id: str | None = Query(None, alias="objectId"),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        _require(trusted, "admin.api.view")
        with sessions() as session:
            if request_id or actor_id or object_id:
                statement = select(OpenPlatformAuditRow).where(
                    OpenPlatformAuditRow.tenant_id == trusted.tenant_id,
                    OpenPlatformAuditRow.workspace_id == trusted.workspace_id,
                )
                if request_id:
                    statement = statement.where(OpenPlatformAuditRow.request_id == request_id)
                if actor_id:
                    statement = statement.where(OpenPlatformAuditRow.actor_id == actor_id)
                if object_id:
                    statement = statement.where(OpenPlatformAuditRow.object_id == object_id)
                rows = list(
                    session.scalars(
                        statement.order_by(
                            OpenPlatformAuditRow.occurred_at.desc(),
                            OpenPlatformAuditRow.audit_id.desc(),
                        )
                    )
                )
                items = [_audit_record(row) for row in rows]
            else:
                statement = select(ApiClientRow).where(
                    ApiClientRow.tenant_id == trusted.tenant_id,
                    ApiClientRow.workspace_id == trusted.workspace_id,
                )
                if status:
                    statement = statement.where(ApiClientRow.status == status)
                if query:
                    statement = statement.where(
                        ApiClientRow.name.ilike(f"%{query.strip()}%")
                        | ApiClientRow.client_id.ilike(f"%{query.strip()}%")
                    )
                rows = list(
                    session.scalars(
                        statement.order_by(
                            ApiClientRow.updated_at.desc(),
                            ApiClientRow.client_id.asc(),
                        )
                    )
                )
                items = [_client_record(session, row) for row in rows]
        start = (page - 1) * page_size
        return {
            "items": items[start : start + page_size],
            "page": page,
            "pageSize": page_size,
            "total": len(items),
        }

    @router.post("/api/v1/admin/api-clients/actions")
    def act(
        payload: ActionPayload,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        _require(trusted, "admin.api.manage")
        if not idempotency_key.strip() or payload.version < 0:
            raise HTTPException(422, detail={"code": "INVALID_COMMAND"})
        fingerprint = (
            "sha256:"
            + sha256(
                json.dumps(
                    payload.model_dump(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        try:
            with sessions.begin() as session:
                previous = session.scalar(
                    select(OpenPlatformCommandRow).where(
                        OpenPlatformCommandRow.actor_id == trusted.actor_id,
                        OpenPlatformCommandRow.idempotency_key == idempotency_key,
                    )
                )
                if previous:
                    if previous.fingerprint != fingerprint:
                        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                    return cast(dict[str, object], json.loads(previous.result_json))
                result = _apply_action(session, payload, trusted, request, pepper)
                session.add(
                    OpenPlatformCommandRow(
                        command_id=str(uuid4()),
                        actor_id=trusted.actor_id,
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        result_json=json.dumps(result, ensure_ascii=False),
                        created_at=datetime.now(UTC),
                    )
                )
                return result
        except IntegrityError as error:
            raise HTTPException(409, detail={"code": "CONCURRENT_COMMAND_CONFLICT"}) from error

    @router.get("/api/v1/open/v1/context")
    def external_context(
        request: Request,
        x_api_key: str = Header(alias="X-API-Key"),
        x_workspace_id: str = Header(alias="X-Workspace-Id"),
        required_scope: str = Query("projects:read", alias="scope"),
    ) -> dict[str, object]:
        request_id = (request.headers.get("X-Request-Id") or str(uuid4())).strip()
        now = datetime.now(UTC)
        denied: HTTPException | None = None
        response: dict[str, object] | None = None
        with sessions.begin() as session:
            try:
                credential, client = _authenticate(
                    session,
                    x_api_key,
                    x_workspace_id.strip(),
                    required_scope,
                    pepper,
                    now,
                )
            except HTTPException as error:
                denied = error
                credential = None
                client = None
            if denied is None and credential is not None and client is not None:
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                    {"subject": credential.credential_id},
                )
                window_start = now - timedelta(seconds=client.rate_window_seconds)
                used = (
                    session.scalar(
                        select(func.count())
                        .select_from(ApiUsageRow)
                        .where(
                            ApiUsageRow.workspace_id == client.workspace_id,
                            ApiUsageRow.client_id == client.client_id,
                            ApiUsageRow.occurred_at >= window_start,
                        )
                    )
                    or 0
                )
                if used >= client.rate_limit:
                    _usage(
                        session,
                        client,
                        credential,
                        request_id,
                        "context.read",
                        required_scope,
                        "rate_limited",
                        429,
                        now,
                    )
                    denied = HTTPException(
                        429,
                        detail={
                            "code": "RATE_LIMITED",
                            "retryAfterSeconds": client.rate_window_seconds,
                        },
                    )
                else:
                    credential.last_used_at = now
                    _usage(
                        session,
                        client,
                        credential,
                        request_id,
                        "context.read",
                        required_scope,
                        "allowed",
                        200,
                        now,
                    )
                    response = {
                        "requestId": request_id,
                        "client": {
                            "id": client.client_id,
                            "name": client.name,
                            "workspaceId": client.workspace_id,
                            "scopes": client.scopes,
                        },
                        "rateLimit": {
                            "limit": client.rate_limit,
                            "remaining": max(client.rate_limit - used - 1, 0),
                            "windowSeconds": client.rate_window_seconds,
                        },
                    }
        if denied is not None:
            raise denied
        if response is None:
            raise HTTPException(500, detail={"code": "OPEN_PLATFORM_ERROR"})
        return response

    return router


def _apply_action(
    session: Session,
    payload: ActionPayload,
    trusted: TrustedWorkspaceContext,
    request: Request,
    pepper: bytes,
) -> dict[str, object]:
    action = payload.action.strip()
    aliases = {
        "创建 API Key": "create_key",
        "配置回调与权限范围": "configure_client",
        "轮换凭证": "rotate_key",
        "撤销凭证": "revoke_key",
    }
    action = aliases.get(action, action)
    data = payload.payload or {}
    client_id = payload.objectId.strip()
    if not client_id or client_id == "new":
        client_id = f"client_{uuid4().hex[:16]}"
    now = datetime.now(UTC)
    client = session.get(
        ApiClientRow,
        (trusted.tenant_id, trusted.workspace_id, client_id),
    )
    before = {} if client is None else _client_summary(client)
    one_time_secret: str | None = None
    one_time_secret_kind: str | None = None

    if action == "create_key":
        if client is None:
            scopes = _scopes(data.get("scopes"))
            client = ApiClientRow(
                tenant_id=trusted.tenant_id,
                workspace_id=trusted.workspace_id,
                client_id=client_id,
                name=_text(data.get("name"), "name", fallback=client_id),
                status="active",
                scopes=scopes,
                rate_limit=_positive_int(data.get("rateLimit"), 120),
                rate_window_seconds=_positive_int(data.get("rateWindowSeconds"), 60),
                version=1,
                created_by=trusted.actor_id,
                created_at=now,
                updated_by=trusted.actor_id,
                updated_at=now,
            )
            session.add(client)
        elif client.version != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        if client.status != "active":
            raise HTTPException(409, detail={"code": "CLIENT_NOT_ACTIVE"})
        credential, one_time_secret = _new_credential(trusted, client, pepper, now)
        one_time_secret_kind = "api_key"
        session.add(credential)
    else:
        if client is None:
            raise HTTPException(404, detail={"code": "API_CLIENT_NOT_FOUND"})
        if client.version != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        if action == "rotate_key":
            active = session.scalar(
                select(ApiCredentialRow)
                .where(
                    ApiCredentialRow.tenant_id == trusted.tenant_id,
                    ApiCredentialRow.workspace_id == trusted.workspace_id,
                    ApiCredentialRow.client_id == client.client_id,
                    ApiCredentialRow.status == "active",
                )
                .order_by(ApiCredentialRow.created_at.desc())
            )
            if active is None:
                raise HTTPException(409, detail={"code": "ACTIVE_CREDENTIAL_REQUIRED"})
            replacement, one_time_secret = _new_credential(trusted, client, pepper, now)
            one_time_secret_kind = "api_key"
            active.status = "rotated"
            active.rotated_to_id = replacement.credential_id
            active.revoked_at = now
            session.add(replacement)
        elif action == "revoke_key":
            credentials = session.scalars(
                select(ApiCredentialRow).where(
                    ApiCredentialRow.tenant_id == trusted.tenant_id,
                    ApiCredentialRow.workspace_id == trusted.workspace_id,
                    ApiCredentialRow.client_id == client.client_id,
                    ApiCredentialRow.status == "active",
                )
            )
            for credential in credentials:
                credential.status = "revoked"
                credential.revoked_at = now
            client.status = "revoked"
        elif action == "configure_client":
            if "name" in data:
                client.name = _text(data["name"], "name")
            if "scopes" in data:
                client.scopes = _scopes(data["scopes"])
            if "rateLimit" in data:
                client.rate_limit = _positive_int(data["rateLimit"], 120)
            if "rateWindowSeconds" in data:
                client.rate_window_seconds = _positive_int(data["rateWindowSeconds"], 60)
            if "webhook" in data:
                one_time_secret = _upsert_webhook(session, trusted, client, data["webhook"], pepper, now)
                one_time_secret_kind = "webhook_signing_secret"
        else:
            raise HTTPException(422, detail={"code": "UNKNOWN_ACTION"})
        client.version += 1
        client.updated_by = trusted.actor_id
        client.updated_at = now

    after = _client_summary(client)
    session.add(
        OpenPlatformAuditRow(
            audit_id=str(uuid4()),
            tenant_id=trusted.tenant_id,
            workspace_id=trusted.workspace_id,
            actor_id=trusted.actor_id,
            request_id=trusted.request_id,
            action=action,
            object_type="api_client",
            object_id=client.client_id,
            before_payload=before,
            after_payload=after,
            result="success",
            occurred_at=now,
        )
    )
    result: dict[str, object] = {
        "requestId": trusted.request_id,
        "status": "succeeded",
        "object": after,
    }
    if one_time_secret:
        result["oneTimeSecret"] = one_time_secret
        result["oneTimeSecretKind"] = one_time_secret_kind
    return result


def _new_credential(
    trusted: TrustedWorkspaceContext,
    client: ApiClientRow,
    pepper: bytes,
    now: datetime,
) -> tuple[ApiCredentialRow, str]:
    credential_id = uuid4().hex
    secret = f"xj_live_{credential_id}_{secrets.token_urlsafe(32)}"
    salt = secrets.token_bytes(16)
    return (
        ApiCredentialRow(
            credential_id=credential_id,
            tenant_id=trusted.tenant_id,
            workspace_id=trusted.workspace_id,
            client_id=client.client_id,
            prefix=f"xj_live_{credential_id[:8]}",
            secret_salt=salt.hex(),
            secret_digest=_digest(secret, salt, pepper),
            status="active",
            expires_at=None,
            rotated_to_id=None,
            last_used_at=None,
            created_at=now,
            revoked_at=None,
        ),
        secret,
    )


def _upsert_webhook(
    session: Session,
    trusted: TrustedWorkspaceContext,
    client: ApiClientRow,
    value: object,
    pepper: bytes,
    now: datetime,
) -> str:
    if not isinstance(value, dict):
        raise HTTPException(422, detail={"code": "INVALID_WEBHOOK"})
    url = _text(value.get("url"), "webhook.url")
    if not url.startswith("https://"):
        raise HTTPException(422, detail={"code": "WEBHOOK_HTTPS_REQUIRED"})
    events = value.get("events")
    if not isinstance(events, list) or not events or not all(isinstance(item, str) and item.strip() for item in events):
        raise HTTPException(422, detail={"code": "WEBHOOK_EVENTS_REQUIRED"})
    endpoint_id = str(value.get("id") or f"wh_{uuid4().hex[:16]}")
    current = session.get(WebhookEndpointRow, endpoint_id)
    secret = f"whsec_{secrets.token_urlsafe(40)}"
    salt = secrets.token_bytes(16)
    if current:
        if (
            current.tenant_id != trusted.tenant_id
            or current.workspace_id != trusted.workspace_id
            or current.client_id != client.client_id
        ):
            raise HTTPException(404, detail={"code": "WEBHOOK_NOT_FOUND"})
        expected_version = value.get("version")
        if expected_version != current.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        current.url = url
        current.events = [str(item).strip() for item in events]
        current.secret_salt = salt.hex()
        current.secret_digest = _digest(secret, salt, pepper)
        current.status = "active"
        current.version += 1
        current.updated_by = trusted.actor_id
        current.updated_at = now
    else:
        session.add(
            WebhookEndpointRow(
                endpoint_id=endpoint_id,
                tenant_id=trusted.tenant_id,
                workspace_id=trusted.workspace_id,
                client_id=client.client_id,
                url=url,
                events=[str(item).strip() for item in events],
                secret_salt=salt.hex(),
                secret_digest=_digest(secret, salt, pepper),
                status="active",
                version=1,
                updated_by=trusted.actor_id,
                created_at=now,
                updated_at=now,
            )
        )
    return secret


def _authenticate(
    session: Session,
    secret: str,
    workspace_id: str,
    required_scope: str,
    pepper: bytes,
    now: datetime,
) -> tuple[ApiCredentialRow, ApiClientRow]:
    parts = secret.split("_", 3)
    if len(parts) != 4 or parts[:2] != ["xj", "live"]:
        raise HTTPException(401, detail={"code": "INVALID_API_KEY"})
    credential = session.get(ApiCredentialRow, parts[2])
    valid = (
        credential is not None
        and credential.workspace_id == workspace_id
        and credential.status == "active"
        and (credential.expires_at is None or credential.expires_at > now)
        and secrets.compare_digest(
            _digest(secret, bytes.fromhex(credential.secret_salt), pepper),
            credential.secret_digest,
        )
    )
    if not valid or credential is None:
        raise HTTPException(401, detail={"code": "INVALID_API_KEY"})
    client = session.get(
        ApiClientRow,
        (
            credential.tenant_id,
            credential.workspace_id,
            credential.client_id,
        ),
    )
    if client is None or client.status != "active" or required_scope not in client.scopes:
        raise HTTPException(403, detail={"code": "SCOPE_DENIED"})
    return credential, client


def _usage(
    session: Session,
    client: ApiClientRow,
    credential: ApiCredentialRow,
    request_id: str,
    operation: str,
    scope: str,
    outcome: str,
    status_code: int,
    now: datetime,
) -> None:
    session.add(
        ApiUsageRow(
            usage_id=str(uuid4()),
            tenant_id=client.tenant_id,
            workspace_id=client.workspace_id,
            client_id=client.client_id,
            credential_id=credential.credential_id,
            request_id=request_id,
            operation=operation,
            scope=scope,
            outcome=outcome,
            status_code=status_code,
            occurred_at=now,
        )
    )


def _client_record(session: Session, row: ApiClientRow) -> dict[str, object]:
    credentials = list(
        session.scalars(
            select(ApiCredentialRow)
            .where(
                ApiCredentialRow.tenant_id == row.tenant_id,
                ApiCredentialRow.workspace_id == row.workspace_id,
                ApiCredentialRow.client_id == row.client_id,
            )
            .order_by(ApiCredentialRow.created_at.desc())
        )
    )
    webhooks = list(
        session.scalars(
            select(WebhookEndpointRow)
            .where(
                WebhookEndpointRow.tenant_id == row.tenant_id,
                WebhookEndpointRow.workspace_id == row.workspace_id,
                WebhookEndpointRow.client_id == row.client_id,
            )
            .order_by(WebhookEndpointRow.updated_at.desc())
        )
    )
    latest_usage = session.scalar(
        select(ApiUsageRow)
        .where(
            ApiUsageRow.tenant_id == row.tenant_id,
            ApiUsageRow.workspace_id == row.workspace_id,
            ApiUsageRow.client_id == row.client_id,
        )
        .order_by(ApiUsageRow.occurred_at.desc())
    )
    active = next((item for item in credentials if item.status == "active"), None)
    usage_count = (
        session.scalar(
            select(func.count())
            .select_from(ApiUsageRow)
            .where(
                ApiUsageRow.tenant_id == row.tenant_id,
                ApiUsageRow.workspace_id == row.workspace_id,
                ApiUsageRow.client_id == row.client_id,
            )
        )
        or 0
    )
    return {
        "id": row.client_id,
        "name": row.name,
        "status": row.status,
        "version": row.version,
        "updatedAt": row.updated_at.isoformat(),
        "maskedSecret": f"{active.prefix}••••" if active else "—",
        "scopes": row.scopes,
        "webhooks": [
            {
                "id": item.endpoint_id,
                "url": item.url,
                "status": item.status,
                "version": item.version,
            }
            for item in webhooks
        ],
        "details": {
            "rateLimit": row.rate_limit,
            "rateWindowSeconds": row.rate_window_seconds,
            "usageCount": usage_count,
            "lastUsedAt": (latest_usage.occurred_at.isoformat() if latest_usage else None),
            "lastOutcome": latest_usage.outcome if latest_usage else None,
        },
    }


def _audit_record(row: OpenPlatformAuditRow) -> dict[str, object]:
    return {
        "id": row.audit_id,
        "name": row.action,
        "status": row.result,
        "version": 1,
        "updatedAt": row.occurred_at.isoformat(),
        "details": {
            "requestId": row.request_id,
            "actorId": row.actor_id,
            "objectType": row.object_type,
            "objectId": row.object_id,
            "before": row.before_payload,
            "after": row.after_payload,
        },
    }


def _client_summary(client: ApiClientRow) -> dict[str, object]:
    return {
        "id": client.client_id,
        "name": client.name,
        "status": client.status,
        "scopes": client.scopes,
        "rateLimit": client.rate_limit,
        "rateWindowSeconds": client.rate_window_seconds,
        "version": client.version,
    }


def _digest(secret: str, salt: bytes, pepper: bytes) -> str:
    return scrypt(
        secret.encode(),
        salt=salt + pepper,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    ).hex()


def _scopes(value: object) -> list[str]:
    if value is None:
        return ["projects:read"]
    if not isinstance(value, list) or not value:
        raise HTTPException(422, detail={"code": "SCOPES_REQUIRED"})
    scopes = {str(item).strip() for item in value}
    if "" in scopes or not scopes.issubset(ALLOWED_SCOPES):
        raise HTTPException(422, detail={"code": "INVALID_SCOPE"})
    return sorted(scopes)


def _positive_int(value: object, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > 1_000_000:
        raise HTTPException(422, detail={"code": "INVALID_RATE_LIMIT"})
    return value


def _text(value: object, field: str, fallback: str | None = None) -> str:
    if value is None and fallback is not None:
        return fallback
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, detail={"code": "FIELD_REQUIRED", "field": field})
    return value.strip()


def _require(trusted: TrustedWorkspaceContext, permission: str) -> None:
    if permission not in trusted.permissions:
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})
