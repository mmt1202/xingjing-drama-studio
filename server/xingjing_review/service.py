from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .errors import InvalidTransition, LinkUnavailable, PermissionDenied, ValidationError, VersionConflict
from .models import (
    AccessPolicy,
    Actor,
    ApprovalDecision,
    ApprovalRecord,
    AuditEvent,
    DeliveryRecord,
    DeliveryStatus,
    IssuedReviewLink,
    IssueStatus,
    LinkState,
    ReviewComment,
    ReviewContext,
    ReviewLink,
    ReviewSession,
)
from .store import ReviewStore, ReviewUnitOfWork


class ReviewService:
    def __init__(self, store: ReviewStore, secret_key: bytes, now: Callable[[], datetime] | None = None) -> None:
        if not secret_key:
            raise ValueError("secret_key 不能为空")
        self.store = store
        self.secret_key = secret_key
        self.now = now or (lambda: datetime.now(UTC))

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(self.secret_key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _require(actor: Actor, permission: str, workspace_id: str | None = None) -> None:
        if permission not in actor.permissions or (workspace_id is not None and actor.workspace_id != workspace_id):
            raise PermissionDenied()

    def _audit(
        self,
        uow: ReviewUnitOfWork,
        event_type: str,
        actor: Actor,
        workspace_id: str,
        object_type: str,
        object_id: str,
        request_id: str,
        before: Any,
        after: Any,
    ) -> None:
        uow.append_audit(
            AuditEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                occurred_at=self.now(),
                actor_id=actor.actor_id,
                workspace_id=workspace_id,
                object_type=object_type,
                object_id=object_id,
                request_id=request_id,
                before=asdict(before) if before is not None else None,
                after=asdict(after) if after is not None else None,
            )
        )

    def create_link(
        self,
        *,
        actor: Actor,
        project_id: str,
        final_video_version_id: str,
        expires_at: datetime | None,
        policy: AccessPolicy,
        watermark_text: str | None = None,
        access_secret: str | None,
        request_id: str,
        idempotency_key: str,
    ) -> IssuedReviewLink:
        self._require(actor, "review.manage")
        assert actor.workspace_id is not None
        if expires_at is not None and expires_at <= self.now():
            raise ValidationError("有效期必须晚于当前时间")
        if watermark_text is not None and len(watermark_text.strip()) > 255:
            raise ValidationError("水印文字不能超过 255 个字符")
        scope = f"create-link:{actor.workspace_id}:{project_id}"
        with self.store.atomic() as uow:
            cached = uow.get_idempotent(scope, idempotency_key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            token = secrets.token_urlsafe(32)
            link = ReviewLink(
                id=str(uuid4()),
                workspace_id=actor.workspace_id,
                project_id=project_id,
                final_video_version_id=final_video_version_id,
                token_digest=self._digest("token", token),
                access_secret_digest=self._digest("access", access_secret) if access_secret else None,
                policy=policy,
                watermark_text=watermark_text.strip() if watermark_text and watermark_text.strip() else None,
                expires_at=expires_at,
                created_at=self.now(),
            )
            issued = IssuedReviewLink(link, token, access_secret)
            uow.put_link(link)
            uow.put_delivery(DeliveryRecord(link.id, final_video_version_id, DeliveryStatus.PENDING, 1))
            uow.put_idempotent(scope, idempotency_key, issued)
            self._audit(
                uow, "review.link.created", actor, actor.workspace_id, "review_link", link.id, request_id, None, link
            )
            return issued

    def revoke_link(
        self, *, actor: Actor, link_id: str, expected_version: int, request_id: str, idempotency_key: str
    ) -> ReviewLink:
        with self.store.atomic() as uow:
            link = uow.get_link(link_id)
            if link is None:
                raise LinkUnavailable()
            self._require(actor, "review.manage", link.workspace_id)
            cached = uow.get_idempotent(f"revoke:{link_id}", idempotency_key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            if link.version != expected_version:
                raise VersionConflict()
            updated = replace(link, state=LinkState.REVOKED, revoked_at=self.now(), version=link.version + 1)
            uow.put_link(updated)
            uow.put_idempotent(f"revoke:{link_id}", idempotency_key, updated)
            self._audit(
                uow, "review.link.revoked", actor, link.workspace_id, "review_link", link.id, request_id, link, updated
            )
            return updated

    def verify_access(self, *, token: str, access_secret: str | None = None) -> ReviewContext:
        with self.store.atomic() as uow:
            link = uow.find_link_by_token_digest(self._digest("token", token))
            if (
                link is None
                or link.state is not LinkState.ACTIVE
                or (link.expires_at is not None and link.expires_at <= self.now())
            ):
                raise LinkUnavailable()
            if link.access_secret_digest is not None and (
                access_secret is None
                or not hmac.compare_digest(link.access_secret_digest, self._digest("access", access_secret))
            ):
                raise LinkUnavailable()
            permissions = {"review.view"}
            if link.policy.comment:
                permissions.add("review.comment")
            if link.policy.approve:
                permissions.add("review.approve")
            if link.policy.download:
                permissions.add("review.download")
            session = ReviewSession(str(uuid4()), link.id, frozenset(permissions), self.now())
            uow.put_session(session)
            return ReviewContext(
                link.id,
                link.workspace_id,
                link.project_id,
                link.final_video_version_id,
                link.expires_at,
                frozenset(permissions),
                session,
            )

    def add_comment(
        self,
        *,
        actor: Actor,
        link_id: str,
        timecode_ms: int,
        body: str,
        severity: str = "normal",
        screenshot_asset_id: str | None = None,
        parent_comment_id: str | None = None,
        request_id: str,
        idempotency_key: str,
    ) -> ReviewComment:
        self._require(actor, "review.comment")
        if timecode_ms < 0 or not body.strip():
            raise ValidationError("时间码和批注内容无效")
        with self.store.atomic() as uow:
            link = self._usable_link(uow, link_id)
            if parent_comment_id is not None:
                parent = uow.get_comment(parent_comment_id)
                if parent is None or parent.review_link_id != link_id or parent.final_video_version_id != link.final_video_version_id:
                    raise ValidationError("回复目标不属于当前审片版本")
            cached = uow.get_idempotent(f"comment:{link_id}:{actor.actor_id}", idempotency_key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            comment = ReviewComment(
                str(uuid4()),
                link.id,
                link.final_video_version_id,
                actor.actor_id,
                timecode_ms,
                body.strip(),
                severity,
                screenshot_asset_id,
                IssueStatus.OPEN,
                1,
                self.now(),
                self.now(),
                parent_comment_id,
            )
            uow.put_comment(comment)
            uow.put_idempotent(f"comment:{link_id}:{actor.actor_id}", idempotency_key, comment)
            self._audit(
                uow,
                "review.comment.created",
                actor,
                link.workspace_id,
                "review_comment",
                comment.id,
                request_id,
                None,
                comment,
            )
            return comment

    def change_issue_status(
        self,
        *,
        actor: Actor,
        comment_id: str,
        status: IssueStatus,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> ReviewComment:
        self._require(actor, "review.manage")
        allowed = {
            IssueStatus.OPEN: {IssueStatus.IN_PROGRESS},
            IssueStatus.IN_PROGRESS: {IssueStatus.RESOLVED},
            IssueStatus.RESOLVED: {IssueStatus.REOPENED},
            IssueStatus.REOPENED: {IssueStatus.IN_PROGRESS, IssueStatus.RESOLVED},
        }
        with self.store.atomic() as uow:
            comment = uow.get_comment(comment_id)
            if comment is None:
                raise ValidationError("批注不存在")
            link = self._usable_link(uow, comment.review_link_id, allow_revoked=True)
            self._require(actor, "review.manage", link.workspace_id)
            cached = uow.get_idempotent(f"issue:{comment_id}", idempotency_key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            if comment.version != expected_version:
                raise VersionConflict()
            if status not in allowed[comment.status]:
                raise InvalidTransition()
            updated = replace(comment, status=status, version=comment.version + 1, updated_at=self.now())
            uow.put_comment(updated)
            uow.put_idempotent(f"issue:{comment_id}", idempotency_key, updated)
            self._audit(
                uow,
                "review.issue.status_changed",
                actor,
                link.workspace_id,
                "review_comment",
                comment.id,
                request_id,
                comment,
                updated,
            )
            return updated

    def decide(
        self,
        *,
        actor: Actor,
        link_id: str,
        decision: ApprovalDecision,
        note: str | None,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> DeliveryRecord:
        self._require(actor, "review.approve")
        with self.store.atomic() as uow:
            link = self._usable_link(uow, link_id)
            cached = uow.get_idempotent(f"decision:{link_id}:{actor.actor_id}", idempotency_key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            delivery = uow.get_delivery(link_id)
            if delivery is None or delivery.version != expected_version:
                raise VersionConflict()
            status = (
                DeliveryStatus.APPROVED if decision is ApprovalDecision.APPROVED else DeliveryStatus.CHANGES_REQUESTED
            )
            updated = replace(delivery, status=status, version=delivery.version + 1)
            approval = ApprovalRecord(
                str(uuid4()), link.id, link.final_video_version_id, actor.actor_id, decision, note, self.now()
            )
            uow.put_delivery(updated)
            uow.append_approval(approval)
            uow.put_idempotent(f"decision:{link_id}:{actor.actor_id}", idempotency_key, updated)
            self._audit(
                uow,
                f"review.{decision.value}",
                actor,
                link.workspace_id,
                "delivery",
                link.id,
                request_id,
                delivery,
                updated,
            )
            return updated

    def confirm_delivery(
        self, *, actor: Actor, link_id: str, expected_version: int, request_id: str, idempotency_key: str
    ) -> DeliveryRecord:
        self._require(actor, "review.approve")
        with self.store.atomic() as uow:
            link = self._usable_link(uow, link_id)
            cached = uow.get_idempotent(f"confirm:{link_id}", idempotency_key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            delivery = uow.get_delivery(link_id)
            if delivery is None or delivery.version != expected_version:
                raise VersionConflict()
            if delivery.status is not DeliveryStatus.APPROVED:
                raise InvalidTransition("只有已通过的绑定版本可确认交付")
            updated = replace(
                delivery,
                status=DeliveryStatus.CONFIRMED,
                version=delivery.version + 1,
                confirmed_by=actor.actor_id,
                confirmed_at=self.now(),
            )
            uow.put_delivery(updated)
            uow.put_idempotent(f"confirm:{link_id}", idempotency_key, updated)
            self._audit(
                uow,
                "review.delivery.confirmed",
                actor,
                link.workspace_id,
                "delivery",
                link.id,
                request_id,
                delivery,
                updated,
            )
            return updated

    def _usable_link(self, uow: ReviewUnitOfWork, link_id: str, allow_revoked: bool = False) -> ReviewLink:
        link = uow.get_link(link_id)
        if (
            link is None
            or (not allow_revoked and link.state is not LinkState.ACTIVE)
            or (link.expires_at is not None and link.expires_at <= self.now() and not allow_revoked)
        ):
            raise LinkUnavailable()
        return link
