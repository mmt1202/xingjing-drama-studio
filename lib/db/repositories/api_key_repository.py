"""Async repository for API Key management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from lib.db.base import DEFAULT_USER_ID, utc_now
from lib.db.models.api_key import ApiKey, ApiKeyAudit
from lib.db.repositories.base import BaseRepository, rowcount


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


def _row_to_dict(row: ApiKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "workspace_id": row.workspace_id,
        "scopes": list(row.scopes),
        "created_at": _to_iso(row.created_at),
        "expires_at": _to_iso(row.expires_at),
        "last_used_at": _to_iso(row.last_used_at),
        "revoked_at": _to_iso(row.revoked_at),
        "version": row.version,
    }


class ApiKeyRepository(BaseRepository):
    async def create(
        self,
        *,
        name: str,
        key_hash: str,
        key_prefix: str,
        workspace_id: str,
        scopes: list[str],
        expires_at: datetime | None = None,
        user_id: str = DEFAULT_USER_ID,
        rotated_from_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a new API key record."""
        row = ApiKey(
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            workspace_id=workspace_id,
            scopes=scopes,
            created_at=utc_now(),
            expires_at=expires_at,
            user_id=user_id,
            rotated_from_id=rotated_from_id,
            version=1,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _row_to_dict(row)

    async def list_all(self, *, user_id: str, workspace_id: str) -> list[dict[str, Any]]:
        """Return all API keys (metadata only, no hashes)."""
        stmt = (
            select(ApiKey)
            .where(
                ApiKey.user_id == user_id,
                ApiKey.workspace_id == workspace_id,
                ApiKey.revoked_at.is_(None),
            )
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        )
        stmt = self._scope_query(stmt, ApiKey)
        result = await self.session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars()]

    async def get_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Look up a key by its SHA-256 hash. Returns full row including hash."""
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
        stmt = self._scope_query(stmt, ApiKey)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "key_hash": row.key_hash,
            "key_prefix": row.key_prefix,
            "user_id": row.user_id,
            "workspace_id": row.workspace_id,
            "scopes": list(row.scopes),
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "last_used_at": row.last_used_at,
            "revoked_at": row.revoked_at,
            "version": row.version,
        }

    async def get_by_id(self, key_id: int, *, user_id: str, workspace_id: str) -> dict[str, Any] | None:
        """Look up a key by its primary key ID. Includes key_hash for cache invalidation."""
        stmt = select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.user_id == user_id,
            ApiKey.workspace_id == workspace_id,
        )
        stmt = self._scope_query(stmt, ApiKey)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["key_hash"] = row.key_hash
        return d

    async def revoke(self, key_id: int, *, user_id: str, workspace_id: str, expected_version: int) -> bool:
        """Revoke a scoped key with optimistic concurrency."""
        result = await self.session.execute(
            update(ApiKey)
            .where(
                ApiKey.id == key_id,
                ApiKey.user_id == user_id,
                ApiKey.workspace_id == workspace_id,
                ApiKey.version == expected_version,
                ApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now(), version=ApiKey.version + 1)
        )
        return rowcount(result) > 0

    async def touch_last_used(self, key_hash: str) -> None:
        """Update last_used_at for the given key hash."""
        await self.session.execute(update(ApiKey).where(ApiKey.key_hash == key_hash).values(last_used_at=utc_now()))

    async def append_audit(
        self,
        *,
        event_id: str,
        request_id: str,
        user_id: str,
        workspace_id: str,
        action: str,
        api_key_id: int | None,
        before_payload: dict[str, object] | None,
        after_payload: dict[str, object] | None,
    ) -> None:
        self.session.add(
            ApiKeyAudit(
                event_id=event_id,
                request_id=request_id,
                user_id=user_id,
                workspace_id=workspace_id,
                action=action,
                api_key_id=api_key_id,
                result="SUCCESS",
                before_payload=before_payload,
                after_payload=after_payload,
                occurred_at=utc_now(),
            )
        )

    async def list_audit(
        self,
        *,
        user_id: str,
        workspace_id: str,
        request_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        stmt = select(ApiKeyAudit).where(
            ApiKeyAudit.user_id == user_id,
            ApiKeyAudit.workspace_id == workspace_id,
        )
        if request_id:
            stmt = stmt.where(ApiKeyAudit.request_id == request_id)
        rows = (
            await self.session.execute(
                stmt.order_by(ApiKeyAudit.occurred_at.desc(), ApiKeyAudit.event_id.desc()).limit(limit)
            )
        ).scalars()
        return [
            {
                "event_id": row.event_id,
                "request_id": row.request_id,
                "action": row.action,
                "api_key_id": row.api_key_id,
                "result": row.result,
                "before": row.before_payload,
                "after": row.after_payload,
                "occurred_at": _to_iso(row.occurred_at),
            }
            for row in rows
        ]
