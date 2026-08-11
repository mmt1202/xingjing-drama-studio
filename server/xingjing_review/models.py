from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class LinkState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class IssueStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    REOPENED = "reopened"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    comment: bool = False
    approve: bool = False
    download: bool = False


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    workspace_id: str | None
    permissions: frozenset[str]
    customer: bool = False

    @classmethod
    def staff(cls, actor_id: str, workspace_id: str, permissions: set[str]) -> Actor:
        return cls(actor_id, workspace_id, frozenset(permissions), False)

    @classmethod
    def client(cls, session_id: str, permissions: set[str]) -> Actor:
        forbidden = {p for p in permissions if p.startswith("production.") or p.endswith(".edit")}
        if forbidden:
            raise ValueError("客户主体不能获得生产编辑权限")
        return cls(session_id, None, frozenset(permissions), True)


@dataclass(frozen=True, slots=True)
class ReviewLink:
    id: str
    workspace_id: str
    project_id: str
    final_video_version_id: str
    token_digest: str = field(repr=False)
    access_secret_digest: str | None = field(default=None, repr=False)
    policy: AccessPolicy = AccessPolicy()
    watermark_text: str | None = None
    expires_at: datetime | None = None
    state: LinkState = LinkState.ACTIVE
    version: int = 1
    created_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IssuedReviewLink:
    link: ReviewLink
    token: str
    access_secret: str | None


@dataclass(frozen=True, slots=True)
class ReviewSession:
    id: str
    review_link_id: str
    permissions: frozenset[str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewContext:
    link_id: str
    workspace_id: str
    project_id: str
    final_video_version_id: str
    expires_at: datetime | None
    permissions: frozenset[str]
    session: ReviewSession


@dataclass(frozen=True, slots=True)
class ReviewComment:
    id: str
    review_link_id: str
    final_video_version_id: str
    author_id: str
    timecode_ms: int
    body: str
    severity: str
    screenshot_asset_id: str | None
    status: IssueStatus
    version: int
    created_at: datetime
    updated_at: datetime
    parent_comment_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: str
    review_link_id: str
    final_video_version_id: str
    actor_id: str
    decision: ApprovalDecision
    note: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    review_link_id: str
    final_video_version_id: str
    status: DeliveryStatus
    version: int
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    actor_id: str
    workspace_id: str
    object_type: str
    object_id: str
    request_id: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    result: str = "succeeded"
