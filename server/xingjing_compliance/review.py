from __future__ import annotations

from .models import AuditContext, ManualReviewStatus, ReviewAction, ReviewEvent, ReviewRecord

_TRANSITIONS = {
    (ManualReviewStatus.PENDING, ReviewAction.APPROVE): ManualReviewStatus.APPROVED,
    (ManualReviewStatus.PENDING, ReviewAction.REJECT): ManualReviewStatus.REJECTED,
    (ManualReviewStatus.APPEALED, ReviewAction.APPROVE): ManualReviewStatus.APPROVED,
    (ManualReviewStatus.APPEALED, ReviewAction.REJECT): ManualReviewStatus.REJECTED,
    (ManualReviewStatus.REJECTED, ReviewAction.APPEAL): ManualReviewStatus.APPEALED,
}


def apply_review_action(
    record: ReviewRecord,
    *,
    action: ReviewAction,
    reason: str,
    expected_version: int,
    audit: AuditContext,
) -> ReviewRecord:
    if record.version != expected_version:
        raise ValueError("version conflict")
    if not reason.strip():
        raise ValueError("review reason is required")
    next_status = _TRANSITIONS.get((record.status, action))
    if next_status is None:
        raise ValueError("invalid review transition")
    event = ReviewEvent(
        action=action,
        from_status=record.status,
        to_status=next_status,
        reason=reason,
        audit=audit,
    )
    return ReviewRecord(
        review_id=record.review_id,
        status=next_status,
        version=record.version + 1,
        history=(*record.history, event),
    )
