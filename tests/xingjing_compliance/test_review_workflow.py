from datetime import UTC, datetime

import pytest

from server.xingjing_compliance import (
    AuditContext,
    ManualReviewStatus,
    ReviewAction,
    ReviewRecord,
    apply_review_action,
)


def test_human_review_records_actor_reason_and_request_context() -> None:
    record = ReviewRecord(review_id="review-1", status=ManualReviewStatus.PENDING, version=1, history=())
    context = AuditContext(
        actor_id="legal-7",
        tenant_id="tenant-1",
        request_id="request-9",
        source="api",
        occurred_at=datetime(2026, 7, 15, 9, tzinfo=UTC),
    )

    updated = apply_review_action(
        record,
        action=ReviewAction.REJECT,
        reason="肖像授权范围不包含商业发布",
        expected_version=1,
        audit=context,
    )

    assert updated.status is ManualReviewStatus.REJECTED
    assert updated.version == 2
    assert updated.history[0].audit == context
    assert updated.history[0].reason == "肖像授权范围不包含商业发布"


def test_rejected_review_can_be_appealed_but_cannot_be_directly_approved() -> None:
    record = ReviewRecord(review_id="review-1", status=ManualReviewStatus.REJECTED, version=2, history=())
    context = AuditContext(
        actor_id="owner-2",
        tenant_id="tenant-1",
        request_id="request-10",
        source="ui",
        occurred_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
    )

    appealed = apply_review_action(
        record,
        action=ReviewAction.APPEAL,
        reason="已补充商业肖像授权",
        expected_version=2,
        audit=context,
    )
    assert appealed.status is ManualReviewStatus.APPEALED

    with pytest.raises(ValueError, match="invalid review transition"):
        apply_review_action(
            record,
            action=ReviewAction.APPROVE,
            reason="绕过申诉",
            expected_version=2,
            audit=context,
        )


def test_review_uses_optimistic_version_guard() -> None:
    record = ReviewRecord(review_id="review-1", status=ManualReviewStatus.PENDING, version=3, history=())
    context = AuditContext(
        actor_id="legal-7",
        tenant_id="tenant-1",
        request_id="request-11",
        source="api",
        occurred_at=datetime(2026, 7, 15, 11, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="version conflict"):
        apply_review_action(
            record,
            action=ReviewAction.APPROVE,
            reason="复核通过",
            expected_version=2,
            audit=context,
        )
