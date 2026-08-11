from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_review import (
    AccessPolicy,
    Actor,
    ApprovalDecision,
    DeliveryStatus,
    InMemoryReviewStore,
    InvalidTransition,
    IssueStatus,
    LinkUnavailable,
    ReviewService,
    VersionConflict,
)

NOW = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


def test_create_link_returns_secrets_once_and_persists_only_digests() -> None:
    store = InMemoryReviewStore()
    service = ReviewService(store=store, secret_key=b"test-only-pepper", now=lambda: NOW)

    issued = service.create_link(
        actor=Actor.staff("user-1", "workspace-1", {"review.manage"}),
        project_id="project-1",
        final_video_version_id="video-version-7",
        expires_at=NOW + timedelta(days=7),
        policy=AccessPolicy(comment=True, approve=True, download=False),
        access_secret="correct horse battery staple",
        request_id="req-1",
        idempotency_key="idem-1",
    )

    assert issued.token
    assert issued.access_secret == "correct horse battery staple"
    persisted = store.get_link(issued.link.id)
    assert persisted is not None
    assert persisted.token_digest != issued.token
    assert persisted.access_secret_digest != issued.access_secret
    assert "correct horse" not in repr(persisted)
    assert store.audit_events()[0].event_type == "review.link.created"


def make_link(*, expires_at: datetime | None = None, secret: str | None = None):
    store = InMemoryReviewStore()
    service = ReviewService(store, b"test-only-pepper", now=lambda: NOW)
    issued = service.create_link(
        actor=Actor.staff("manager", "ws-1", {"review.manage"}),
        project_id="project-1",
        final_video_version_id="final-v3",
        expires_at=expires_at or NOW + timedelta(days=1),
        policy=AccessPolicy(comment=True, approve=True),
        access_secret=secret,
        request_id="create-request",
        idempotency_key="create-key",
    )
    return store, service, issued


def test_access_verification_exposes_only_bound_version_and_client_permissions() -> None:
    _, service, issued = make_link(secret="let-me-in")

    context = service.verify_access(token=issued.token, access_secret="let-me-in")

    assert context.final_video_version_id == "final-v3"
    assert context.permissions == frozenset({"review.view", "review.comment", "review.approve"})
    assert all(not permission.endswith(".edit") for permission in context.permissions)
    with pytest.raises(ValueError):
        Actor.client("client-1", {"production.edit"})


def test_invalid_expired_and_revoked_links_share_non_disclosing_error() -> None:
    _, service, issued = make_link(secret="right")
    with pytest.raises(LinkUnavailable) as wrong:
        service.verify_access(token=issued.token, access_secret="wrong")
    with pytest.raises(LinkUnavailable) as unknown:
        service.verify_access(token="does-not-exist")
    assert wrong.value.code == unknown.value.code == "REVIEW_LINK_UNAVAILABLE"

    service.revoke_link(
        actor=Actor.staff("manager", "ws-1", {"review.manage"}),
        link_id=issued.link.id,
        expected_version=1,
        request_id="revoke-request",
        idempotency_key="revoke-key",
    )
    with pytest.raises(LinkUnavailable):
        service.verify_access(token=issued.token, access_secret="right")


def test_timecode_issue_follows_resolution_and_reopen_loop_with_optimistic_lock() -> None:
    store, service, issued = make_link()
    client = Actor.client("review-session-1", {"review.view", "review.comment"})
    manager = Actor.staff("manager", "ws-1", {"review.manage"})
    comment = service.add_comment(
        actor=client,
        link_id=issued.link.id,
        timecode_ms=12_340,
        body="这里需要换镜头",
        severity="major",
        screenshot_asset_id="shot-1",
        request_id="comment-request",
        idempotency_key="comment-key",
    )
    duplicate = service.add_comment(
        actor=client,
        link_id=issued.link.id,
        timecode_ms=99_999,
        body="重复请求不应覆盖",
        request_id="retry-request",
        idempotency_key="comment-key",
    )
    assert duplicate == comment
    assert comment.final_video_version_id == "final-v3"

    in_progress = service.change_issue_status(
        actor=manager,
        comment_id=comment.id,
        status=IssueStatus.IN_PROGRESS,
        expected_version=1,
        request_id="status-1",
        idempotency_key="status-key-1",
    )
    resolved = service.change_issue_status(
        actor=manager,
        comment_id=comment.id,
        status=IssueStatus.RESOLVED,
        expected_version=2,
        request_id="status-2",
        idempotency_key="status-key-2",
    )
    reopened = service.change_issue_status(
        actor=manager,
        comment_id=comment.id,
        status=IssueStatus.REOPENED,
        expected_version=3,
        request_id="status-3",
        idempotency_key="status-key-3",
    )
    assert (in_progress.status, resolved.status, reopened.status) == (
        IssueStatus.IN_PROGRESS,
        IssueStatus.RESOLVED,
        IssueStatus.REOPENED,
    )
    with pytest.raises(VersionConflict):
        service.change_issue_status(
            actor=manager,
            comment_id=comment.id,
            status=IssueStatus.RESOLVED,
            expected_version=1,
            request_id="stale",
            idempotency_key="stale-key",
        )
    assert store.get_comment(comment.id) == reopened


def test_approval_rejection_and_delivery_confirmation_are_version_bound() -> None:
    store, service, issued = make_link()
    client = Actor.client("review-session-1", {"review.view", "review.approve"})
    rejected = service.decide(
        actor=client,
        link_id=issued.link.id,
        decision=ApprovalDecision.CHANGES_REQUESTED,
        note="请按批注修改",
        expected_version=1,
        request_id="reject-request",
        idempotency_key="decision-1",
    )
    assert rejected.status is DeliveryStatus.CHANGES_REQUESTED
    with pytest.raises(InvalidTransition):
        service.confirm_delivery(
            actor=client,
            link_id=issued.link.id,
            expected_version=2,
            request_id="premature",
            idempotency_key="premature-key",
        )

    approved = service.decide(
        actor=client,
        link_id=issued.link.id,
        decision=ApprovalDecision.APPROVED,
        note="修改后通过",
        expected_version=2,
        request_id="approve-request",
        idempotency_key="decision-2",
    )
    confirmed = service.confirm_delivery(
        actor=client,
        link_id=issued.link.id,
        expected_version=approved.version,
        request_id="confirm-request",
        idempotency_key="confirm-key",
    )
    assert confirmed.status is DeliveryStatus.CONFIRMED
    assert confirmed.final_video_version_id == issued.link.final_video_version_id
    assert store.get_delivery(issued.link.id) == confirmed
    assert [event.event_type for event in store.audit_events()][-3:] == [
        "review.changes_requested",
        "review.approved",
        "review.delivery.confirmed",
    ]
