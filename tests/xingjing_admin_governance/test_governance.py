from __future__ import annotations

import pytest

from server.xingjing_admin_governance import (
    Actor,
    ApprovalDecision,
    AuthorizationError,
    GovernanceService,
    InMemoryGovernanceAdapter,
    NotFoundError,
    ReviewDecision,
    ReviewStatus,
    RightsKind,
    RiskLevel,
    RuleTerm,
    SubjectSnapshot,
)
from server.xingjing_admin_governance.runtime import _audit_csv, _filter_records


@pytest.fixture
def governance() -> tuple[GovernanceService, InMemoryGovernanceAdapter]:
    adapter = InMemoryGovernanceAdapter()
    return GovernanceService(adapter, adapter, adapter), adapter


def actor(actor_id: str, *permissions: str) -> Actor:
    return Actor("tenant-a", actor_id, frozenset(permissions), request_id=f"req-{actor_id}")


def test_published_rule_version_is_immutable_and_scan_records_exact_version(governance):
    service, adapter = governance
    manager = actor("rules", "admin.compliance.manage")
    version = service.publish_rules(
        manager,
        [RuleTerm("诈骗", RiskLevel.HIGH, "fraud"), RuleTerm("夸大", RiskLevel.MEDIUM, "claims")],
        idempotency_key="publish-1",
    )

    result = service.screen(
        actor("reviewer", "admin.compliance.manage"),
        SubjectSnapshot("project", "p-1", "sha256:v1", {"title": "这是诈骗夸大宣传"}),
        rights=(),
        idempotency_key="screen-1",
    )

    assert result.rule_version_id == version.id
    assert result.level is RiskLevel.HIGH
    assert {finding.code for finding in result.findings} == {"fraud", "claims"}
    assert result.status is ReviewStatus.BLOCKED
    assert adapter.get_rule_version("tenant-a", version.id).terms == version.terms


def test_real_person_and_ip_rights_are_blocked_until_valid_evidence(governance):
    service, _ = governance
    manager = actor("rules", "admin.compliance.manage")
    service.publish_rules(manager, [], idempotency_key="publish-1")
    subject = SubjectSnapshot(
        "project",
        "p-2",
        "sha256:v1",
        {"caption": "普通内容"},
        rights_risks=(RightsKind.REAL_PERSON, RightsKind.IP_ADAPTATION),
    )

    result = service.screen(manager, subject, rights=(), idempotency_key="screen-1")

    assert result.status is ReviewStatus.BLOCKED
    assert {finding.code for finding in result.findings} == {
        "missing_real_person_rights",
        "missing_ip_adaptation_rights",
    }


def test_manual_review_appeal_and_two_person_release_cannot_be_bypassed(governance):
    service, _ = governance
    manager = actor("rules", "admin.compliance.manage")
    service.publish_rules(manager, [RuleTerm("高危", RiskLevel.HIGH, "danger")], idempotency_key="publish-1")
    review = service.screen(
        manager,
        SubjectSnapshot("project", "p-3", "sha256:v1", {"title": "高危"}),
        rights=(),
        idempotency_key="screen-1",
    )
    claimed = service.claim_review(actor("reviewer", "admin.compliance.manage"), review.id, "claim-1")
    blocked = service.decide_review(
        actor("reviewer", "admin.compliance.manage"), claimed.id, ReviewDecision.BLOCK, "证据成立", "decision-1"
    )
    appeal = service.submit_appeal(
        actor("owner", "compliance.appeal"), blocked.id, "已补充授权证明", ("evidence://rights/1",), "appeal-1"
    )
    request = service.request_high_risk_release(
        actor("legal", "admin.compliance.manage"), appeal.id, "复核后拟放行", "release-1"
    )

    with pytest.raises(AuthorizationError):
        service.approve_high_risk_release(
            actor("legal", "admin.security.manage"), request.id, ApprovalDecision.APPROVE, "self-approve"
        )

    first = service.approve_high_risk_release(
        actor("security-1", "admin.security.manage"), request.id, ApprovalDecision.APPROVE, "approve-1"
    )
    assert first.completed is False
    with pytest.raises(AuthorizationError):
        service.approve_high_risk_release(
            actor("security-1", "admin.security.manage"), request.id, ApprovalDecision.APPROVE, "approve-again"
        )
    second = service.approve_high_risk_release(
        actor("security-2", "admin.security.manage"), request.id, ApprovalDecision.APPROVE, "approve-2"
    )

    assert second.completed is True
    assert service.get_review(actor("auditor", "admin.compliance.view"), review.id).status is ReviewStatus.RELEASED


def test_role_contract_and_sensitive_actions_are_audited_and_tenant_scoped(governance):
    service, adapter = governance
    security = actor("security", "admin.security.manage", "admin.security.view")
    role = service.save_role(
        security,
        "compliance-reviewer",
        frozenset({"admin.compliance.view", "admin.compliance.manage"}),
        expected_version=0,
        idempotency_key="role-1",
    )

    assert role.version == 1
    assert service.get_role(actor("viewer", "admin.security.view"), role.id).permissions == role.permissions
    with pytest.raises(NotFoundError):
        service.get_role(Actor("tenant-b", "viewer", frozenset({"admin.security.view"}), "req-b"), role.id)
    events = adapter.query_audit("tenant-a", actor_id="security", object_id=role.id)
    assert [(event.action, event.result) for event in events] == [("admin_role.saved", "success")]
    assert events[0].request_id == "req-security"


def test_idempotency_replays_same_result_without_duplicate_audit(governance):
    service, adapter = governance
    manager = actor("rules", "admin.compliance.manage")

    first = service.publish_rules(manager, [], idempotency_key="same")
    second = service.publish_rules(manager, [], idempotency_key="same")

    assert first == second
    assert len(adapter.query_audit("tenant-a", actor_id="rules")) == 1


def test_clean_content_enters_manual_queue_instead_of_being_fixed_to_pass(governance):
    service, _ = governance
    manager = actor("rules", "admin.compliance.manage", "admin.compliance.view")
    service.publish_rules(manager, [], idempotency_key="publish")
    review = service.screen(
        manager,
        SubjectSnapshot("project", "clean", "sha256:clean", {"title": "普通内容"}),
        rights=(),
        idempotency_key="screen",
    )

    assert review.status is ReviewStatus.PENDING
    assert service.list_review_queue(manager, status=ReviewStatus.PENDING) == (review,)


def test_admin_governance_combines_search_and_multiple_status_filters() -> None:
    records = [
        {"id": "review-1", "name": "视频审核", "status": "pending"},
        {"id": "review-2", "name": "图片审核", "status": "approved"},
        {"id": "risk-1", "name": "视频风险", "status": "blocked"},
    ]

    filtered = _filter_records(records, search="视频", status="pending,blocked")

    assert [item["id"] for item in filtered] == ["review-1", "risk-1"]


def test_audit_export_serializes_stable_csv_with_evidence() -> None:
    content = _audit_csv([{
        "id": "audit-1", "name": "保存权限", "status": "success",
        "updatedAt": "2026-08-11T10:00:00+00:00",
        "details": {"actorId": "security-1", "before": {"role": "viewer"}},
    }])

    assert content.splitlines()[0] == "id,action,result,occurred_at,details_json"
    assert "audit-1" in content
    assert "security-1" in content
