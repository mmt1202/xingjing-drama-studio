"""`xingjing_review` 同步端口的生产 PostgreSQL SQLAlchemy 适配器。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import TracebackType
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.xingjing_review.errors import VersionConflict
from server.xingjing_review.models import (
    AccessPolicy,
    ApprovalRecord,
    AuditEvent,
    DeliveryRecord,
    DeliveryStatus,
    IssuedReviewLink,
    IssueStatus,
    LinkState,
    ReviewComment,
    ReviewLink,
    ReviewSession,
)
from server.xingjing_review.store import ReviewUnitOfWork

from .models import (
    ReviewApprovalRow,
    ReviewAuditRow,
    ReviewCommentRow,
    ReviewDeliveryRow,
    ReviewIdempotencyRow,
    ReviewLinkRow,
    ReviewSessionRow,
)

type SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class ReviewPersistenceScope:
    """可信运行时注入的租户和工作区范围，绝不可取自外链客户端输入。"""

    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.workspace_id.strip():
            raise ValueError("tenant_id and workspace_id are required")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("review persistence timestamps must include a timezone")
    return value.astimezone(UTC)


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_json(item) for item in value]
    return value


class _SqlAlchemyReviewUnitOfWork:
    def __init__(self, session: Session, scope: ReviewPersistenceScope) -> None:
        self._session, self._scope = session, scope

    def get_link(self, link_id: str) -> ReviewLink | None:
        row = self._session.scalar(self._link_query(link_id))
        return None if row is None else self._link(row)

    def find_link_by_token_digest(self, digest: str) -> ReviewLink | None:
        # 外链令牌本身是认证材料；只有摘要命中后才暴露其范围。服务层统一把未命中/过期/撤销归为 LinkUnavailable。
        row = self._session.scalar(select(ReviewLinkRow).where(ReviewLinkRow.token_digest == digest))
        return None if row is None else self._link(row)

    def put_link(self, link: ReviewLink) -> None:
        self._assert_workspace(link.workspace_id)
        existing = self._session.scalar(self._link_query(link.id))
        if existing is None:
            if link.version != 1:
                raise VersionConflict()
            self._session.add(self._link_row(link))
            self._flush_conflict()
            return
        self._cas(
            ReviewLinkRow, {"id": link.id, "version": link.version - 1},
            self._link_values(link), link.version,
        )

    def get_comment(self, comment_id: str) -> ReviewComment | None:
        row = self._session.scalar(
            select(ReviewCommentRow).where(
                ReviewCommentRow.tenant_id == self._scope.tenant_id,
                ReviewCommentRow.workspace_id == self._scope.workspace_id,
                ReviewCommentRow.id == comment_id,
            )
        )
        return None if row is None else self._comment(row)

    def put_comment(self, comment: ReviewComment) -> None:
        link = self.get_link(comment.review_link_id)
        if link is None or link.final_video_version_id != comment.final_video_version_id:
            raise ValueError("comment must bind a review link and its final video version in the current scope")
        existing = self.get_comment(comment.id)
        if existing is None:
            if comment.version != 1:
                raise VersionConflict()
            self._session.add(self._comment_row(comment))
            self._flush_conflict()
            return
        self._cas(
            ReviewCommentRow, {"id": comment.id, "version": comment.version - 1},
            self._comment_values(comment), comment.version,
        )

    def get_delivery(self, link_id: str) -> DeliveryRecord | None:
        row = self._session.scalar(
            select(ReviewDeliveryRow).where(
                ReviewDeliveryRow.tenant_id == self._scope.tenant_id,
                ReviewDeliveryRow.workspace_id == self._scope.workspace_id,
                ReviewDeliveryRow.review_link_id == link_id,
            )
        )
        return None if row is None else self._delivery(row)

    def put_delivery(self, delivery: DeliveryRecord) -> None:
        link = self.get_link(delivery.review_link_id)
        if link is None or link.final_video_version_id != delivery.final_video_version_id:
            raise ValueError("delivery must bind a review link and its final video version in the current scope")
        existing = self.get_delivery(delivery.review_link_id)
        if existing is None:
            if delivery.version != 1:
                raise VersionConflict()
            self._session.add(self._delivery_row(delivery))
            self._flush_conflict()
            return
        self._cas(
            ReviewDeliveryRow, {"review_link_id": delivery.review_link_id, "version": delivery.version - 1},
            self._delivery_values(delivery), delivery.version,
        )

    def put_session(self, session: ReviewSession) -> None:
        if self.get_link(session.review_link_id) is None:
            raise ValueError("session review link is outside the current scope")
        self._session.add(ReviewSessionRow(
            tenant_id=self._scope.tenant_id, workspace_id=self._scope.workspace_id, id=session.id,
            review_link_id=session.review_link_id, permissions=sorted(session.permissions), created_at=_utc(session.created_at),
            last_seen_at=_utc(session.created_at), revoked_at=None,
        ))
        self._flush_conflict()

    def append_approval(self, approval: ApprovalRecord) -> None:
        link = self.get_link(approval.review_link_id)
        if link is None or link.final_video_version_id != approval.final_video_version_id:
            raise ValueError("approval must bind a review link and its final video version in the current scope")
        self._session.add(ReviewApprovalRow(
            tenant_id=self._scope.tenant_id, workspace_id=self._scope.workspace_id, id=approval.id,
            review_link_id=approval.review_link_id, final_video_version_id=approval.final_video_version_id,
            actor_id=approval.actor_id, decision=approval.decision.value, note=approval.note, created_at=_utc(approval.created_at),
        ))
        self._flush_conflict()

    def append_audit(self, event: AuditEvent) -> None:
        self._assert_workspace(event.workspace_id)
        self._session.add(ReviewAuditRow(
            tenant_id=self._scope.tenant_id, workspace_id=self._scope.workspace_id, event_id=event.event_id,
            event_type=event.event_type, occurred_at=_utc(event.occurred_at), actor_id=event.actor_id,
            object_type=event.object_type, object_id=event.object_id, request_id=event.request_id,
            before=cast(dict[str, Any] | None, _json(event.before)), after=cast(dict[str, Any] | None, _json(event.after)),
            result=event.result,
        ))
        self._flush_conflict()

    def get_idempotent(self, scope: str, key: str) -> object | None:
        row = self._session.scalar(select(ReviewIdempotencyRow).where(
            ReviewIdempotencyRow.tenant_id == self._scope.tenant_id, ReviewIdempotencyRow.workspace_id == self._scope.workspace_id,
            ReviewIdempotencyRow.scope == scope, ReviewIdempotencyRow.idempotency_key == key,
        ))
        return None if row is None else self._idempotent_result(row)

    def put_idempotent(self, scope: str, key: str, value: object) -> None:
        kind, result_id = self._idempotent_reference(value)
        self._session.add(ReviewIdempotencyRow(
            tenant_id=self._scope.tenant_id, workspace_id=self._scope.workspace_id, scope=scope, idempotency_key=key,
            result_kind=kind, result_id=result_id, created_at=datetime.now(UTC),
        ))
        self._flush_conflict()

    def _cas(self, model: Any, identity: dict[str, object], values: dict[str, object], version: int) -> None:
        if version < 2:
            raise VersionConflict()
        predicates = [model.tenant_id == self._scope.tenant_id, model.workspace_id == self._scope.workspace_id]
        predicates.extend(getattr(model, field) == value for field, value in identity.items())
        result = cast(CursorResult[tuple[object, ...]], self._session.execute(
            update(model).where(*predicates).values(**values).execution_options(synchronize_session=False)
        ))
        if result.rowcount != 1:
            raise VersionConflict()

    def _flush_conflict(self) -> None:
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise VersionConflict() from exc

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self._scope.workspace_id:
            raise ValueError("review object is outside the current workspace")

    def _link_query(self, link_id: str):
        return select(ReviewLinkRow).where(
            ReviewLinkRow.tenant_id == self._scope.tenant_id,
            ReviewLinkRow.workspace_id == self._scope.workspace_id,
            ReviewLinkRow.id == link_id,
        )

    def _idempotent_reference(self, value: object) -> tuple[str, str]:
        if isinstance(value, IssuedReviewLink):
            return "issued_link", value.link.id
        if isinstance(value, ReviewLink):
            return "link", value.id
        if isinstance(value, ReviewComment):
            return "comment", value.id
        if isinstance(value, DeliveryRecord):
            return "delivery", value.review_link_id
        raise TypeError("unsupported review idempotency result")

    def _idempotent_result(self, row: ReviewIdempotencyRow) -> object | None:
        if row.result_kind == "issued_link":
            link = self.get_link(row.result_id)
            # 令牌和访问口令永不落库；重放只返回已创建的安全元数据，首次响应才会携带凭证。
            return None if link is None else IssuedReviewLink(link, "", None)
        if row.result_kind == "link":
            return self.get_link(row.result_id)
        if row.result_kind == "comment":
            return self.get_comment(row.result_id)
        if row.result_kind == "delivery":
            return self.get_delivery(row.result_id)
        raise RuntimeError("unknown review idempotency result kind")

    @staticmethod
    def _link(row: ReviewLinkRow) -> ReviewLink:
        return ReviewLink(row.id, row.workspace_id, row.project_id, row.final_video_version_id, row.token_digest,
            row.access_secret_digest, AccessPolicy(row.comment_allowed, row.approve_allowed, row.download_allowed),
            row.watermark_text, row.expires_at, LinkState(row.state), row.version, row.created_at, row.revoked_at)

    def _link_row(self, link: ReviewLink) -> ReviewLinkRow:
        return ReviewLinkRow(tenant_id=self._scope.tenant_id, workspace_id=self._scope.workspace_id, id=link.id,
            **self._link_values(link))

    @staticmethod
    def _link_values(link: ReviewLink) -> dict[str, object]:
        if link.created_at is None:
            raise ValueError("review link created_at is required")
        return {"project_id": link.project_id, "final_video_version_id": link.final_video_version_id,
            "token_digest": link.token_digest, "access_secret_digest": link.access_secret_digest,
            "comment_allowed": link.policy.comment, "approve_allowed": link.policy.approve, "download_allowed": link.policy.download,
            "watermark_text": link.watermark_text,
            "expires_at": None if link.expires_at is None else _utc(link.expires_at), "state": link.state.value,
            "version": link.version, "created_at": _utc(link.created_at),
            "revoked_at": None if link.revoked_at is None else _utc(link.revoked_at)}

    @staticmethod
    def _comment(row: ReviewCommentRow) -> ReviewComment:
        return ReviewComment(row.id, row.review_link_id, row.final_video_version_id, row.author_id, row.timecode_ms, row.body,
            row.severity, row.screenshot_asset_id, IssueStatus(row.status), row.version, row.created_at, row.updated_at,
            row.parent_comment_id)

    def _comment_row(self, value: ReviewComment) -> ReviewCommentRow:
        return ReviewCommentRow(tenant_id=self._scope.tenant_id, workspace_id=self._scope.workspace_id, id=value.id,
            **self._comment_values(value))

    @staticmethod
    def _comment_values(value: ReviewComment) -> dict[str, object]:
        return {"review_link_id": value.review_link_id, "final_video_version_id": value.final_video_version_id,
            "author_id": value.author_id, "timecode_ms": value.timecode_ms, "body": value.body, "severity": value.severity,
            "screenshot_asset_id": value.screenshot_asset_id, "parent_comment_id": value.parent_comment_id,
            "status": value.status.value, "version": value.version,
            "created_at": _utc(value.created_at), "updated_at": _utc(value.updated_at)}

    @staticmethod
    def _delivery(row: ReviewDeliveryRow) -> DeliveryRecord:
        return DeliveryRecord(row.review_link_id, row.final_video_version_id, DeliveryStatus(row.status), row.version,
            row.confirmed_by, row.confirmed_at)

    def _delivery_row(self, value: DeliveryRecord) -> ReviewDeliveryRow:
        return ReviewDeliveryRow(tenant_id=self._scope.tenant_id, workspace_id=self._scope.workspace_id,
            review_link_id=value.review_link_id, **self._delivery_values(value))

    @staticmethod
    def _delivery_values(value: DeliveryRecord) -> dict[str, object]:
        return {"final_video_version_id": value.final_video_version_id, "status": value.status.value, "version": value.version,
            "confirmed_by": value.confirmed_by, "confirmed_at": None if value.confirmed_at is None else _utc(value.confirmed_at)}


class _Atomic(AbstractContextManager[ReviewUnitOfWork]):
    def __init__(self, session_factory: SessionFactory, scope: ReviewPersistenceScope) -> None:
        self._session_factory, self._scope, self._session = session_factory, scope, None

    def __enter__(self) -> ReviewUnitOfWork:
        session = self._session_factory()
        self._session = session
        try:
            session.begin()
        except BaseException:
            session.close()
            self._session = None
            raise
        return _SqlAlchemyReviewUnitOfWork(session, self._scope)

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> bool:
        session = self._session
        if session is None:
            return False
        try:
            if exc_type is None:
                session.commit()
            else:
                session.rollback()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
        return False


class SqlAlchemyReviewStore:
    """M12 的真实 PostgreSQL 事务仓储；不会创建表或回退到 SQLite/内存实现。"""

    def __init__(self, session_factory: SessionFactory, *, scope: ReviewPersistenceScope) -> None:
        self._session_factory, self._scope = session_factory, scope

    def atomic(self) -> AbstractContextManager[ReviewUnitOfWork]:
        return _Atomic(self._session_factory, self._scope)
