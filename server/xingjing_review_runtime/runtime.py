"""M12 审片领域、PostgreSQL 仓储与可信身份上下文的生产组合。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import mimetypes
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import Request
from sqlalchemy import create_engine, or_, select, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_editing_persistence.models import FinalVideoVersionRow
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import ProjectRow
from server.xingjing_review import (
    AccessPolicy,
    Actor,
    ApprovalDecision,
    IssueStatus,
    LinkUnavailable,
    ReviewComment,
    ReviewLink,
    ReviewService,
)
from server.xingjing_review.models import DeliveryRecord, ReviewContext, ReviewSession
from server.xingjing_review_persistence import ReviewPersistenceScope, SqlAlchemyReviewStore
from server.xingjing_review_persistence.models import (
    ReviewAuditRow,
    ReviewCommentRow,
    ReviewDeliveryRow,
    ReviewLinkRow,
    ReviewScreenshotRow,
    ReviewSessionRow,
)

type SessionFactory = Callable[[], Session]
type TrustedContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]


class ReviewRuntimeConfigurationError(RuntimeError):
    """M12 未配置生产依赖或遇到持久化故障时的稳定失败。"""


class ReviewProjectScopeDenied(LookupError):
    """项目不属于当前可信工作区；不得泄露是否真实存在。"""


class ReviewPermissionDenied(PermissionError):
    """可信员工会话缺少审片域权限。"""


@dataclass(frozen=True, slots=True)
class StaffReviewContext:
    trusted: TrustedWorkspaceContext
    actor: Actor
    scope: ReviewPersistenceScope


@dataclass(frozen=True, slots=True)
class ReviewMedia:
    path: Path
    media_type: str
    filename: str
    allow_download: bool


class ReviewRuntime:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        engine: Engine,
        secret_key: bytes,
        context_resolver: TrustedContextResolver,
        media_root: Path,
        screenshot_root: Path,
    ) -> None:
        self._session_factory = session_factory
        self._engine: Engine | None = engine
        self._secret_key = secret_key
        self._context_resolver = context_resolver
        self._media_root = media_root.resolve()
        self._screenshot_root = screenshot_root.resolve()

    async def staff_context(self, request: Request, *, project_id: str | None, permission: str) -> StaffReviewContext:
        resolved = self._context_resolver(request)
        trusted = await resolved if inspect.isawaitable(resolved) else resolved
        if permission not in trusted.permissions:
            raise ReviewPermissionDenied(permission)
        if project_id is not None and not await asyncio.to_thread(self._project_in_scope, trusted, project_id):
            raise ReviewProjectScopeDenied()
        return StaffReviewContext(
            trusted=trusted,
            actor=Actor.staff(trusted.actor_id, trusted.workspace_id, set(trusted.permissions)),
            scope=ReviewPersistenceScope(trusted.tenant_id, trusted.workspace_id),
        )

    async def list_reviews(self, context: StaffReviewContext, *, project_id: str | None = None, query: str | None = None,
                           page_size: int = 20, page_token: str | None = None) -> dict[str, object]:
        return await asyncio.to_thread(self._list_reviews_sync, context.scope, project_id, query, page_size, page_token)

    async def create_link(
        self,
        context: StaffReviewContext,
        *,
        project_id: str,
        final_video_version_id: str,
        expires_at: datetime | None,
        policy: AccessPolicy,
        watermark_text: str | None,
        access_secret: str | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._create_link_sync,
            context,
            project_id,
            final_video_version_id,
            expires_at,
            policy,
            watermark_text,
            access_secret,
            idempotency_key,
        )

    async def revoke_link(self, context: StaffReviewContext, *, link_id: str, expected_version: int, idempotency_key: str) -> dict[str, object]:
        return await asyncio.to_thread(self._revoke_link_sync, context, link_id, expected_version, idempotency_key)

    async def staff_review_detail(self, context: StaffReviewContext, *, link_id: str) -> dict[str, object]:
        return await asyncio.to_thread(self._staff_review_detail_sync, context, link_id)

    async def change_comment_status(self, context: StaffReviewContext, *, comment_id: str, issue_status: IssueStatus,
                                    expected_version: int, idempotency_key: str) -> dict[str, object]:
        return await asyncio.to_thread(self._change_comment_status_sync, context, comment_id, issue_status,
                                       expected_version, idempotency_key)

    async def open_public_context(self, *, token: str, access_secret: str | None) -> dict[str, object]:
        return await asyncio.to_thread(self._open_public_context_sync, token, access_secret)

    async def public_action(
        self,
        *,
        token: str,
        session_id: str,
        action: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> dict[str, object]:
        return await asyncio.to_thread(self._public_action_sync, token, session_id, action, dict(payload), idempotency_key)

    async def public_media(self, *, token: str, session_id: str, ticket: str, download: bool) -> ReviewMedia:
        return await asyncio.to_thread(self._public_media_sync, token, session_id, ticket, download)

    async def public_screenshot(self, *, token: str, session_id: str, ticket: str, screenshot_id: str) -> ReviewMedia:
        return await asyncio.to_thread(self._public_screenshot_sync, token, session_id, ticket, screenshot_id)

    async def upload_screenshot(self, *, token: str, session_id: str, content: bytes, media_type: str,
                                timecode_ms: int, request_id: str) -> dict[str, object]:
        return await asyncio.to_thread(self._upload_screenshot_sync, token, session_id, content, media_type, timecode_ms, request_id)

    async def logout_public_session(self, *, token: str, session_id: str, request_id: str) -> None:
        await asyncio.to_thread(self._logout_public_session_sync, token, session_id, request_id)

    async def close(self) -> None:
        engine = self._engine
        if engine is not None:
            self._engine = None
            await asyncio.to_thread(engine.dispose)

    def _service(self, scope: ReviewPersistenceScope) -> ReviewService:
        return ReviewService(SqlAlchemyReviewStore(self._session_factory, scope=scope), self._secret_key)

    def _project_in_scope(self, trusted: TrustedWorkspaceContext, project_id: str) -> bool:
        try:
            with self._session_factory() as session:
                return session.scalar(
                    select(ProjectRow.id).where(
                        ProjectRow.id == project_id,
                        ProjectRow.tenant_id == trusted.tenant_id,
                        ProjectRow.workspace_id == trusted.workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                ) is not None
        except SQLAlchemyError as error:
            raise ReviewRuntimeConfigurationError("REVIEW_PROJECT_SCOPE_UNAVAILABLE") from error

    def _list_reviews_sync(self, scope: ReviewPersistenceScope, project_id: str | None, search: str | None,
                           page_size: int, page_token: str | None) -> dict[str, object]:
        try:
            with self._session_factory() as session:
                query = select(ReviewLinkRow).where(
                    ReviewLinkRow.tenant_id == scope.tenant_id,
                    ReviewLinkRow.workspace_id == scope.workspace_id,
                )
                if project_id is not None:
                    query = query.where(ReviewLinkRow.project_id == project_id)
                if search and search.strip():
                    pattern = f"%{search.strip()}%"
                    query = query.where(or_(ReviewLinkRow.id.ilike(pattern), ReviewLinkRow.project_id.ilike(pattern),
                                            ReviewLinkRow.final_video_version_id.ilike(pattern), ReviewLinkRow.state.ilike(pattern)))
                cursor = _decode_cursor(page_token) if page_token else None
                if cursor is not None:
                    created_at, row_id = cursor
                    query = query.where(or_(ReviewLinkRow.created_at < created_at,
                                            (ReviewLinkRow.created_at == created_at) & (ReviewLinkRow.id > row_id)))
                size = min(max(page_size, 1), 100)
                rows = session.scalars(query.order_by(ReviewLinkRow.created_at.desc(), ReviewLinkRow.id).limit(size + 1)).all()
                next_token = _encode_cursor(rows[size - 1].created_at, rows[size - 1].id) if len(rows) > size else None
                visible = rows[:size]
                return {"reviewLinks": [self._link_payload(row, self._delivery_for(session, scope, row.id)) for row in visible],
                        "nextToken": next_token}
        except SQLAlchemyError as error:
            raise ReviewRuntimeConfigurationError("REVIEW_LINK_READ_UNAVAILABLE") from error

    def _create_link_sync(
        self,
        context: StaffReviewContext,
        project_id: str,
        final_video_version_id: str,
        expires_at: datetime | None,
        policy: AccessPolicy,
        watermark_text: str | None,
        access_secret: str | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        self._verified_media(context.scope, project_id, final_video_version_id, verify_digest=True)
        issued = self._service(context.scope).create_link(
            actor=context.actor,
            project_id=project_id,
            final_video_version_id=final_video_version_id,
            expires_at=expires_at,
            policy=policy,
            watermark_text=watermark_text,
            access_secret=access_secret,
            request_id=context.trusted.request_id,
            idempotency_key=idempotency_key,
        )
        payload = self._link_payload_from_domain(issued.link, None)
        # Raw credentials are only returned for the first create call. Replay is deliberately redacted by the store.
        if issued.token:
            payload["token"] = issued.token
        if issued.access_secret:
            payload["accessSecret"] = issued.access_secret
        return payload

    def _revoke_link_sync(self, context: StaffReviewContext, link_id: str, expected_version: int, idempotency_key: str) -> dict[str, object]:
        link = self._service(context.scope).revoke_link(
            actor=context.actor,
            link_id=link_id,
            expected_version=expected_version,
            request_id=context.trusted.request_id,
            idempotency_key=idempotency_key,
        )
        return self._link_payload_from_domain(link, None)

    def _staff_review_detail_sync(self, context: StaffReviewContext, link_id: str) -> dict[str, object]:
        try:
            with self._session_factory() as session:
                link = session.scalar(select(ReviewLinkRow).where(ReviewLinkRow.tenant_id == context.scope.tenant_id,
                    ReviewLinkRow.workspace_id == context.scope.workspace_id, ReviewLinkRow.id == link_id))
                if link is None:
                    raise LinkUnavailable()
                comments = session.scalars(select(ReviewCommentRow).where(
                    ReviewCommentRow.tenant_id == context.scope.tenant_id,
                    ReviewCommentRow.workspace_id == context.scope.workspace_id,
                    ReviewCommentRow.review_link_id == link_id,
                ).order_by(ReviewCommentRow.timecode_ms, ReviewCommentRow.created_at, ReviewCommentRow.id)).all()
                return {"reviewLink": self._link_payload(link, self._delivery_for(session, context.scope, link_id)),
                        "comments": [_comment_row_payload(item) for item in comments]}
        except SQLAlchemyError as error:
            raise ReviewRuntimeConfigurationError("REVIEW_DETAIL_READ_UNAVAILABLE") from error

    def _change_comment_status_sync(self, context: StaffReviewContext, comment_id: str, issue_status: IssueStatus,
                                    expected_version: int, idempotency_key: str) -> dict[str, object]:
        result = self._service(context.scope).change_issue_status(actor=context.actor, comment_id=comment_id,
            status=issue_status, expected_version=expected_version, request_id=context.trusted.request_id,
            idempotency_key=idempotency_key)
        return _comment_payload(result)

    def _open_public_context_sync(self, token: str, access_secret: str | None) -> dict[str, object]:
        scope = self._scope_for_token(token)
        context = self._service(scope).verify_access(token=token, access_secret=access_secret)
        return self._public_context_payload(scope, context)

    def _public_action_sync(
        self, token: str, session_id: str, action: str, payload: Mapping[str, object], idempotency_key: str
    ) -> dict[str, object]:
        scope = self._scope_for_token(token)
        context, actor = self._public_session(scope, token, session_id)
        service = self._service(scope)
        if action == "comment":
            screenshot_id = _optional_text(payload.get("screenshotAssetId"))
            if screenshot_id is not None:
                with self._session_factory() as session:
                    screenshot = session.scalar(select(ReviewScreenshotRow).where(
                        ReviewScreenshotRow.tenant_id == scope.tenant_id,
                        ReviewScreenshotRow.workspace_id == scope.workspace_id,
                        ReviewScreenshotRow.id == screenshot_id,
                        ReviewScreenshotRow.review_link_id == context.link_id,
                        ReviewScreenshotRow.final_video_version_id == context.final_video_version_id,
                    ))
                    if screenshot is None:
                        raise ValueError("REVIEW_SCREENSHOT_SCOPE_INVALID")
            result = service.add_comment(
                actor=actor,
                link_id=context.link_id,
                timecode_ms=_integer(payload.get("timecodeMs"), "TIMECODE_REQUIRED", minimum=0),
                body=_text(payload.get("body"), "COMMENT_BODY_REQUIRED"),
                severity=_text(payload.get("severity"), "SEVERITY_REQUIRED"),
                screenshot_asset_id=screenshot_id,
                parent_comment_id=_optional_text(payload.get("parentCommentId")),
                request_id=session_id,
                idempotency_key=idempotency_key,
            )
            return {"comment": _comment_payload(result)}
        if action == "decision":
            decision = ApprovalDecision(_text(payload.get("decision"), "DECISION_REQUIRED"))
            result = service.decide(
                actor=actor,
                link_id=context.link_id,
                decision=decision,
                note=_optional_text(payload.get("note")),
                expected_version=_integer(payload.get("version"), "VERSION_REQUIRED", minimum=1),
                request_id=session_id,
                idempotency_key=idempotency_key,
            )
            return {"delivery": _delivery_payload(result)}
        if action == "confirm_delivery":
            result = service.confirm_delivery(
                actor=actor,
                link_id=context.link_id,
                expected_version=_integer(payload.get("version"), "VERSION_REQUIRED", minimum=1),
                request_id=session_id,
                idempotency_key=idempotency_key,
            )
            return {"delivery": _delivery_payload(result)}
        raise ValueError("UNSUPPORTED_REVIEW_ACTION")

    def _scope_for_token(self, token: str) -> ReviewPersistenceScope:
        digest = hmac.new(self._secret_key, f"token:{token}".encode(), hashlib.sha256).hexdigest()
        try:
            with self._session_factory() as session:
                row = session.scalar(select(ReviewLinkRow).where(ReviewLinkRow.token_digest == digest))
                if row is None:
                    raise LinkUnavailable()
                return ReviewPersistenceScope(row.tenant_id, row.workspace_id)
        except SQLAlchemyError as error:
            raise ReviewRuntimeConfigurationError("REVIEW_LINK_LOOKUP_UNAVAILABLE") from error

    def _public_session(self, scope: ReviewPersistenceScope, token: str, session_id: str) -> tuple[ReviewContext, Actor]:
        digest = hmac.new(self._secret_key, f"token:{token}".encode(), hashlib.sha256).hexdigest()
        try:
            with self._session_factory() as session:
                link = session.scalar(
                    select(ReviewLinkRow).where(
                        ReviewLinkRow.tenant_id == scope.tenant_id,
                        ReviewLinkRow.workspace_id == scope.workspace_id,
                        ReviewLinkRow.token_digest == digest,
                    )
                )
                if link is None or link.state != "active" or (link.expires_at is not None and link.expires_at <= datetime.now(link.expires_at.tzinfo)):
                    raise LinkUnavailable()
                row = session.scalar(
                    select(ReviewSessionRow).where(
                        ReviewSessionRow.tenant_id == scope.tenant_id,
                        ReviewSessionRow.workspace_id == scope.workspace_id,
                        ReviewSessionRow.id == session_id,
                        ReviewSessionRow.review_link_id == link.id,
                    )
                )
                if row is None or row.revoked_at is not None:
                    raise LinkUnavailable()
                row.last_seen_at = datetime.now(UTC)
                session.commit()
                actor = Actor.client(row.id, set(row.permissions))
                context = ReviewContext(
                    link_id=link.id,
                    workspace_id=link.workspace_id,
                    project_id=link.project_id,
                    final_video_version_id=link.final_video_version_id,
                    expires_at=link.expires_at,
                    permissions=frozenset(row.permissions),
                    session=ReviewSession(
                        id=row.id,
                        review_link_id=link.id,
                        permissions=frozenset(row.permissions),
                        created_at=row.created_at,
                    ),
                )
                return context, actor
        except SQLAlchemyError as error:
            raise ReviewRuntimeConfigurationError("REVIEW_SESSION_LOOKUP_UNAVAILABLE") from error

    def _public_context_payload(self, scope: ReviewPersistenceScope, context: ReviewContext) -> dict[str, object]:
        try:
            with self._session_factory() as session:
                link = session.scalar(
                    select(ReviewLinkRow).where(
                        ReviewLinkRow.tenant_id == scope.tenant_id,
                        ReviewLinkRow.workspace_id == scope.workspace_id,
                        ReviewLinkRow.id == context.link_id,
                    )
                )
                if link is None:
                    raise LinkUnavailable()
                comments = session.scalars(
                    select(ReviewCommentRow)
                    .where(
                        ReviewCommentRow.tenant_id == scope.tenant_id,
                        ReviewCommentRow.workspace_id == scope.workspace_id,
                        ReviewCommentRow.review_link_id == context.link_id,
                    )
                    .order_by(ReviewCommentRow.timecode_ms, ReviewCommentRow.created_at, ReviewCommentRow.id)
                ).all()
                media = self._verified_media(scope, context.project_id, context.final_video_version_id, verify_digest=False)
                ticket = self._media_ticket(context.link_id, context.session.id)
                return {
                    "review": self._link_payload(link, self._delivery_for(session, scope, context.link_id)),
                    "session": {"id": context.session.id, "permissions": sorted(context.permissions), "ticket": ticket},
                    "media": {"available": True, "mediaType": media.media_type, "filename": media.filename,
                              "downloadAllowed": link.download_allowed},
                    "comments": [_comment_row_payload(item) for item in comments],
                }
        except SQLAlchemyError as error:
            raise ReviewRuntimeConfigurationError("REVIEW_CONTEXT_READ_UNAVAILABLE") from error

    @staticmethod
    def _delivery_for(session: Session, scope: ReviewPersistenceScope, link_id: str) -> ReviewDeliveryRow | None:
        return session.scalar(
            select(ReviewDeliveryRow).where(
                ReviewDeliveryRow.tenant_id == scope.tenant_id,
                ReviewDeliveryRow.workspace_id == scope.workspace_id,
                ReviewDeliveryRow.review_link_id == link_id,
            )
        )

    def _verified_media(self, scope: ReviewPersistenceScope, project_id: str, version_id: str, *, verify_digest: bool) -> ReviewMedia:
        with self._session_factory() as session:
            row = session.scalar(select(FinalVideoVersionRow).where(
                FinalVideoVersionRow.tenant_id == scope.tenant_id, FinalVideoVersionRow.workspace_id == scope.workspace_id,
                FinalVideoVersionRow.project_id == project_id, FinalVideoVersionRow.version_id == version_id,
            ))
            if row is None:
                raise LinkUnavailable()
            output = row.snapshot.get("output")
            if not isinstance(output, Mapping):
                raise ReviewRuntimeConfigurationError("REVIEW_MEDIA_METADATA_INVALID")
            object_key = output.get("object_key")
            if not isinstance(object_key, str):
                raise ReviewRuntimeConfigurationError("REVIEW_MEDIA_METADATA_INVALID")
            path = self._scoped_path(self._media_root, scope, object_key)
            if not path.is_file():
                raise ReviewRuntimeConfigurationError("REVIEW_MEDIA_NOT_AVAILABLE")
            expected_size = output.get("size_bytes")
            if isinstance(expected_size, int) and path.stat().st_size != expected_size:
                raise ReviewRuntimeConfigurationError("REVIEW_MEDIA_SIZE_MISMATCH")
            if verify_digest:
                expected_hash = output.get("content_sha256")
                if isinstance(expected_hash, str) and _sha256_file(path) != expected_hash:
                    raise ReviewRuntimeConfigurationError("REVIEW_MEDIA_DIGEST_MISMATCH")
            return ReviewMedia(path, mimetypes.guess_type(path.name)[0] or "video/mp4", path.name, False)

    def _public_media_sync(self, token: str, session_id: str, ticket: str, download: bool) -> ReviewMedia:
        scope = self._scope_for_token(token)
        context, _ = self._public_session(scope, token, session_id)
        self._verify_media_ticket(context.link_id, session_id, ticket)
        with self._session_factory() as session:
            link = session.scalar(select(ReviewLinkRow).where(ReviewLinkRow.tenant_id == scope.tenant_id,
                ReviewLinkRow.workspace_id == scope.workspace_id, ReviewLinkRow.id == context.link_id))
            if link is None or (download and not link.download_allowed):
                raise ReviewPermissionDenied("review.download")
        media = self._verified_media(scope, context.project_id, context.final_video_version_id, verify_digest=False)
        return ReviewMedia(media.path, media.media_type, media.filename, download)

    def _upload_screenshot_sync(self, token: str, session_id: str, content: bytes, media_type: str,
                                timecode_ms: int, request_id: str) -> dict[str, object]:
        if not _valid_image_bytes(media_type, content) or len(content) > 10 * 1024 * 1024:
            raise ValueError("REVIEW_SCREENSHOT_INVALID")
        scope = self._scope_for_token(token)
        context, actor = self._public_session(scope, token, session_id)
        if "review.comment" not in actor.permissions or timecode_ms < 0:
            raise ReviewPermissionDenied("review.comment")
        screenshot_id = str(uuid4())
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[media_type]
        relative = f"{scope.tenant_id}/{scope.workspace_id}/{context.link_id}/{screenshot_id}{extension}"
        target = self._scoped_path(self._screenshot_root, scope, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        now = datetime.now(UTC)
        digest = hashlib.sha256(content).hexdigest()
        try:
            with self._session_factory() as session, session.begin():
                session.add(ReviewScreenshotRow(tenant_id=scope.tenant_id, workspace_id=scope.workspace_id,
                    id=screenshot_id, review_link_id=context.link_id, final_video_version_id=context.final_video_version_id,
                    uploader_session_id=session_id, object_key=relative, content_sha256=digest, size_bytes=len(content),
                    media_type=media_type, timecode_ms=timecode_ms, created_at=now))
                session.add(ReviewAuditRow(tenant_id=scope.tenant_id, workspace_id=scope.workspace_id,
                    event_id=str(uuid4()), event_type="review.screenshot.uploaded", occurred_at=now,
                    actor_id=session_id, object_type="review_screenshot", object_id=screenshot_id, request_id=request_id,
                    before=None, after={"reviewLinkId": context.link_id, "timecodeMs": timecode_ms,
                    "contentSha256": digest, "sizeBytes": len(content)}, result="succeeded"))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return {"id": screenshot_id, "timecodeMs": timecode_ms, "mediaType": media_type,
                "sizeBytes": len(content), "createdAt": now.isoformat()}

    def _public_screenshot_sync(self, token: str, session_id: str, ticket: str, screenshot_id: str) -> ReviewMedia:
        scope = self._scope_for_token(token)
        context, _ = self._public_session(scope, token, session_id)
        self._verify_media_ticket(context.link_id, session_id, ticket)
        with self._session_factory() as session:
            row = session.scalar(select(ReviewScreenshotRow).where(ReviewScreenshotRow.tenant_id == scope.tenant_id,
                ReviewScreenshotRow.workspace_id == scope.workspace_id, ReviewScreenshotRow.id == screenshot_id,
                ReviewScreenshotRow.review_link_id == context.link_id))
            if row is None:
                raise LinkUnavailable()
            path = self._scoped_path(self._screenshot_root, scope, row.object_key)
            if not path.is_file() or path.stat().st_size != row.size_bytes:
                raise ReviewRuntimeConfigurationError("REVIEW_SCREENSHOT_NOT_AVAILABLE")
            return ReviewMedia(path, row.media_type, path.name, False)

    def _logout_public_session_sync(self, token: str, session_id: str, request_id: str) -> None:
        scope = self._scope_for_token(token)
        context, _ = self._public_session(scope, token, session_id)
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            result = session.execute(update(ReviewSessionRow).where(ReviewSessionRow.tenant_id == scope.tenant_id,
                ReviewSessionRow.workspace_id == scope.workspace_id, ReviewSessionRow.id == session_id,
                ReviewSessionRow.revoked_at.is_(None)).values(revoked_at=now, last_seen_at=now))
            if cast(object, getattr(result, "rowcount", None)) != 1:
                raise LinkUnavailable()
            session.add(ReviewAuditRow(tenant_id=scope.tenant_id, workspace_id=scope.workspace_id,
                event_id=str(uuid4()), event_type="review.session.revoked", occurred_at=now, actor_id=session_id,
                object_type="review_session", object_id=session_id, request_id=request_id, before=None,
                after={"reviewLinkId": context.link_id, "revokedAt": now.isoformat()}, result="succeeded"))

    def _media_ticket(self, link_id: str, session_id: str) -> str:
        expires = int((datetime.now(UTC) + timedelta(minutes=15)).timestamp())
        payload = f"{link_id}:{session_id}:{expires}"
        signature = hmac.new(self._secret_key, f"media:{payload}".encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def _verify_media_ticket(self, link_id: str, session_id: str, ticket: str) -> None:
        try:
            raw_expiry, signature = ticket.split(".", 1)
            expiry = int(raw_expiry)
        except (TypeError, ValueError) as error:
            raise LinkUnavailable() from error
        payload = f"{link_id}:{session_id}:{expiry}"
        expected = hmac.new(self._secret_key, f"media:{payload}".encode(), hashlib.sha256).hexdigest()
        if expiry < int(datetime.now(UTC).timestamp()) or not hmac.compare_digest(signature, expected):
            raise LinkUnavailable()

    @staticmethod
    def _scoped_path(root: Path, scope: ReviewPersistenceScope, object_key: str) -> Path:
        if not object_key or "\\" in object_key or Path(object_key).is_absolute():
            raise ReviewRuntimeConfigurationError("REVIEW_OBJECT_KEY_INVALID")
        parts = object_key.split("/")
        if len(parts) < 3 or parts[:2] != [scope.tenant_id, scope.workspace_id] or any(part in {"", ".", ".."} for part in parts):
            raise ReviewRuntimeConfigurationError("REVIEW_OBJECT_SCOPE_INVALID")
        path = root.joinpath(*parts).resolve()
        scoped_root = (root / scope.tenant_id / scope.workspace_id).resolve()
        if not path.is_relative_to(scoped_root):
            raise ReviewRuntimeConfigurationError("REVIEW_OBJECT_SCOPE_INVALID")
        return path

    @staticmethod
    def _link_payload(row: ReviewLinkRow, delivery: ReviewDeliveryRow | None) -> dict[str, object]:
        return {
            "id": row.id,
            "workspaceId": row.workspace_id,
            "projectId": row.project_id,
            "finalVideoVersionId": row.final_video_version_id,
            "policy": {"comment": row.comment_allowed, "approve": row.approve_allowed, "download": row.download_allowed},
            "watermarkText": row.watermark_text,
            "expiresAt": None if row.expires_at is None else row.expires_at.isoformat(),
            "state": row.state,
            "version": row.version,
            "createdAt": row.created_at.isoformat(),
            "revokedAt": None if row.revoked_at is None else row.revoked_at.isoformat(),
            "delivery": None if delivery is None else _delivery_row_payload(delivery),
        }

    @staticmethod
    def _link_payload_from_domain(link: ReviewLink, delivery: DeliveryRecord | None) -> dict[str, object]:
        data = asdict(link)
        data.pop("token_digest", None)
        data.pop("access_secret_digest", None)
        data["policy"] = asdict(getattr(link, "policy"))
        data["id"] = data.pop("id")
        return _camel_payload(data, delivery)


def create_production_review_runtime(
    *, database_url: str | None = None, secret: str | None = None, context_resolver: TrustedContextResolver | None = None,
    media_root: str | Path | None = None, screenshot_root: str | Path | None = None,
) -> ReviewRuntime:
    url = (database_url or os.environ.get("XINGJING_REVIEW_DATABASE_URL", "")).strip()
    key = (secret or os.environ.get("XINGJING_REVIEW_HMAC_SECRET", "")).strip()
    if not url:
        raise ReviewRuntimeConfigurationError("XINGJING_REVIEW_DATABASE_URL_REQUIRED")
    if len(key) < 32:
        raise ReviewRuntimeConfigurationError("XINGJING_REVIEW_HMAC_SECRET_MINIMUM_32_REQUIRED")
    media_path = Path(media_root or os.environ.get("XINGJING_EDITING_OBJECT_STORAGE_ROOT", "")).expanduser()
    screenshot_path = Path(screenshot_root or os.environ.get("XINGJING_REVIEW_SCREENSHOT_ROOT", "")).expanduser()
    if not media_path.is_dir():
        raise ReviewRuntimeConfigurationError("XINGJING_EDITING_OBJECT_STORAGE_ROOT_REQUIRED")
    if not str(screenshot_path).strip() or str(screenshot_path) == ".":
        raise ReviewRuntimeConfigurationError("XINGJING_REVIEW_SCREENSHOT_ROOT_REQUIRED")
    try:
        screenshot_path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReviewRuntimeConfigurationError("XINGJING_REVIEW_SCREENSHOT_ROOT_UNAVAILABLE") from error
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError) as error:
        raise ReviewRuntimeConfigurationError("XINGJING_REVIEW_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC") from error
    if parsed.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise ReviewRuntimeConfigurationError("XINGJING_REVIEW_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    try:
        engine = create_engine(url, pool_pre_ping=True)
    except (ModuleNotFoundError, SQLAlchemyError, ValueError) as error:
        raise ReviewRuntimeConfigurationError("REVIEW_RUNTIME_DATABASE_UNAVAILABLE") from error
    return ReviewRuntime(
        session_factory=sessionmaker(engine, expire_on_commit=False),
        engine=engine,
        secret_key=key.encode(),
        context_resolver=context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        media_root=media_path,
        screenshot_root=screenshot_path,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_image_bytes(media_type: str, content: bytes) -> bool:
    if not content:
        return False
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
    if media_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or len(parsed) != 2 or not all(isinstance(item, str) for item in parsed):
            raise ValueError
        timestamp = datetime.fromisoformat(parsed[0])
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp, parsed[1]
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("INVALID_PAGE_TOKEN") from error


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value, "INVALID_TEXT")


def _integer(value: object, code: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(code)
    return value


def _comment_payload(value: ReviewComment) -> dict[str, object]:
    return _camel_payload(asdict(value), None)


def _comment_row_payload(value: ReviewCommentRow) -> dict[str, object]:
    return {
        "id": value.id,
        "reviewLinkId": value.review_link_id,
        "finalVideoVersionId": value.final_video_version_id,
        "authorId": value.author_id,
        "timecodeMs": value.timecode_ms,
        "body": value.body,
        "severity": value.severity,
        "screenshotAssetId": value.screenshot_asset_id,
        "parentCommentId": value.parent_comment_id,
        "status": value.status,
        "version": value.version,
        "createdAt": value.created_at.isoformat(),
        "updatedAt": value.updated_at.isoformat(),
    }


def _delivery_payload(value: DeliveryRecord) -> dict[str, object]:
    return _camel_payload(asdict(value), None)


def _delivery_row_payload(value: ReviewDeliveryRow) -> dict[str, object]:
    return {
        "reviewLinkId": value.review_link_id,
        "finalVideoVersionId": value.final_video_version_id,
        "status": value.status,
        "version": value.version,
        "confirmedBy": value.confirmed_by,
        "confirmedAt": None if value.confirmed_at is None else value.confirmed_at.isoformat(),
    }


def _camel_payload(data: Mapping[str, object], delivery: DeliveryRecord | None) -> dict[str, object]:
    names = {
        "review_link_id": "reviewLinkId", "final_video_version_id": "finalVideoVersionId", "timecode_ms": "timecodeMs",
        "screenshot_asset_id": "screenshotAssetId", "author_id": "authorId", "created_at": "createdAt",
        "updated_at": "updatedAt", "confirmed_by": "confirmedBy", "confirmed_at": "confirmedAt", "workspace_id": "workspaceId",
        "project_id": "projectId", "expires_at": "expiresAt", "revoked_at": "revokedAt", "access_secret": "accessSecret",
        "watermark_text": "watermarkText", "parent_comment_id": "parentCommentId",
    }
    payload = {names.get(key, key): _serialize(value) for key, value in data.items()}
    if delivery is not None:
        payload["delivery"] = _delivery_payload(delivery)
    return payload


def _serialize(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    if isinstance(value, Mapping):
        return {key: _serialize(item) for key, item in value.items()}
    return value
