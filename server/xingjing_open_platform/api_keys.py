from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from .models import ApiKey, ApiKeyStatus, ApiPrincipal, AuditEvent, CreatedApiKey, RequestContext
from .ports import ApiKeyRepository, AuditSink

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


class ApiKeyNotFoundError(LookupError):
    pass


class SecretHasher:
    def __init__(self, *, pepper: bytes) -> None:
        if not pepper:
            raise ValueError("pepper is required")
        self._pepper = pepper

    def hash(self, secret: str, salt: bytes) -> str:
        return hashlib.scrypt(secret.encode("utf-8"), salt=salt + self._pepper, n=2**14, r=8, p=1, dklen=32).hex()

    def verify(self, secret: str, salt_hex: str, expected: str) -> bool:
        actual = self.hash(secret, bytes.fromhex(salt_hex))
        return hmac.compare_digest(actual, expected)


class ApiKeyService:
    def __init__(
        self,
        store: ApiKeyRepository,
        hasher: SecretHasher,
        *,
        clock: Callable[[], datetime],
        audits: AuditSink | None = None,
    ) -> None:
        self._store = store
        self._hasher = hasher
        self._clock = clock
        self._audits = audits

    def create(
        self,
        *,
        context: RequestContext,
        client_id: str,
        scopes: set[str],
        expires_at: datetime | None,
    ) -> CreatedApiKey:
        if not scopes or not scopes.issubset(ALLOWED_SCOPES):
            raise ValueError("at least one known scope is required")
        if expires_at is not None and expires_at <= self._clock():
            raise ValueError("expiry must be in the future")
        key_id = uuid4().hex
        secret = f"xj_live_{key_id}_{secrets.token_urlsafe(32)}"
        salt = secrets.token_bytes(16)
        api_key = ApiKey(
            id=key_id,
            client_id=client_id,
            workspace_id=context.workspace_id,
            scopes=frozenset(scopes),
            status=ApiKeyStatus.ACTIVE,
            expires_at=expires_at,
            created_at=self._clock(),
            secret_salt=salt.hex(),
            secret_digest=self._hasher.hash(secret, salt),
        )
        self._store.add_api_key(api_key)
        self._audit(context, api_key.id, "api_key.created", "allowed", None, self._summary(api_key))
        return CreatedApiKey(api_key=api_key, secret=secret)

    def get(self, workspace_id: str, api_key_id: str) -> ApiKey:
        api_key = self._store.get_api_key(workspace_id, api_key_id)
        if api_key is None:
            raise ApiKeyNotFoundError(api_key_id)
        return api_key

    def authenticate(self, secret: str, *, workspace_id: str, required_scope: str) -> ApiPrincipal:
        parts = secret.split("_", 3)
        if len(parts) != 4 or parts[:2] != ["xj", "live"]:
            raise PermissionError("invalid api key")
        api_key = self._store.get_api_key(workspace_id, parts[2])
        now = self._clock()
        valid = (
            api_key is not None
            and api_key.status is ApiKeyStatus.ACTIVE
            and (api_key.expires_at is None or api_key.expires_at > now)
            and required_scope in api_key.scopes
            and self._hasher.verify(secret, api_key.secret_salt, api_key.secret_digest)
        )
        if not valid or api_key is None:
            raise PermissionError("api key is not authorized")
        return ApiPrincipal(api_key.id, api_key.client_id, api_key.workspace_id, api_key.scopes)

    def revoke(self, context: RequestContext, api_key_id: str) -> ApiKey:
        api_key = self.get(context.workspace_id, api_key_id)
        if api_key.status is not ApiKeyStatus.ACTIVE:
            return api_key
        revoked = self._store.replace_api_key(api_key, status=ApiKeyStatus.REVOKED)
        self._audit(context, api_key.id, "api_key.revoked", "allowed", self._summary(api_key), self._summary(revoked))
        return revoked

    def rotate(self, context: RequestContext, api_key_id: str) -> CreatedApiKey:
        old = self.get(context.workspace_id, api_key_id)
        if old.status is not ApiKeyStatus.ACTIVE:
            raise ValueError("only active api keys can be rotated")
        key_id = uuid4().hex
        secret = f"xj_live_{key_id}_{secrets.token_urlsafe(32)}"
        salt = secrets.token_bytes(16)
        replacement = ApiKey(
            id=key_id,
            client_id=old.client_id,
            workspace_id=old.workspace_id,
            scopes=old.scopes,
            status=ApiKeyStatus.ACTIVE,
            expires_at=old.expires_at,
            created_at=self._clock(),
            secret_salt=salt.hex(),
            secret_digest=self._hasher.hash(secret, salt),
        )
        _, persisted = self._store.rotate_api_key(old, replacement)
        self._audit(
            context,
            old.id,
            "api_key.rotated",
            "allowed",
            self._summary(old),
            self._summary(self.get(context.workspace_id, old.id)),
        )
        return CreatedApiKey(persisted, secret)

    @staticmethod
    def _summary(api_key: ApiKey) -> str:
        scopes = ",".join(sorted(api_key.scopes))
        return f"client={api_key.client_id};status={api_key.status};scopes={scopes}"

    def _audit(
        self,
        context: RequestContext,
        object_id: str,
        event_type: str,
        outcome: str,
        before: str | None,
        after: str | None,
    ) -> None:
        if self._audits is None:
            return
        self._audits.append_audit(
            AuditEvent(
                event_type,
                context.actor_id,
                context.workspace_id,
                object_id,
                context.request_id,
                outcome,
                self._clock(),
                before,
                after,
            )
        )
