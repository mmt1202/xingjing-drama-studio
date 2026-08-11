from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyEffect(StrEnum):
    ADVISE = "advise"
    REQUIRE_MANUAL_REVIEW = "require_manual_review"
    BLOCK = "block"


class ReviewConclusion(StrEnum):
    APPROVED = "approved"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    BLOCKED = "blocked"


class ManualReviewStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPEALED = "appealed"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    APPEAL = "appeal"


@dataclass(frozen=True, slots=True)
class AuditContext:
    actor_id: str
    tenant_id: str
    request_id: str
    source: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PolicyRule:
    rule_id: str
    fact_key: str
    expected_value: object
    risk_level: RiskLevel
    effect: PolicyEffect
    message: str
    required_authorization_type: str | None = None


@dataclass(frozen=True, slots=True)
class PolicySet:
    policy_id: str
    version: str
    effective_at: datetime
    rules: tuple[PolicyRule, ...]


@dataclass(frozen=True, slots=True)
class AuthorizationReference:
    authorization_id: str
    authorization_type: str
    subject_id: str
    evidence_digest: str
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None

    def is_valid_at(self, instant: datetime) -> bool:
        return (
            self.valid_from <= instant
            and (self.valid_until is None or instant < self.valid_until)
            and (self.revoked_at is None or instant < self.revoked_at)
        )


@dataclass(frozen=True, slots=True)
class RiskItem:
    rule_id: str
    risk_level: RiskLevel
    effect: PolicyEffect
    message: str
    evidence: Mapping[str, object]
    missing_authorization_type: str | None = None


@dataclass(frozen=True, slots=True)
class ComplianceAssessment:
    assessment_id: str
    project_id: str
    project_version: str
    policy_id: str
    policy_version: str
    input_digest: str
    conclusion: ReviewConclusion
    manual_review_status: ManualReviewStatus
    risks: tuple[RiskItem, ...]
    authorization_ids: tuple[str, ...]
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderReviewRequest:
    project_id: str
    project_version: str
    input_digest: str
    facts: Mapping[str, object]
    audit: AuditContext


@dataclass(frozen=True, slots=True)
class ProviderReviewResult:
    provider_id: str
    provider_version: str
    conclusion: ReviewConclusion
    risks: tuple[RiskItem, ...]
    evidence_digest: str
    policy_digest: str
    policy_summary: Mapping[str, object]


class ComplianceProvider(Protocol):
    async def review(self, request: ProviderReviewRequest) -> ProviderReviewResult: ...


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    action: ReviewAction
    from_status: ManualReviewStatus
    to_status: ManualReviewStatus
    reason: str
    audit: AuditContext


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    review_id: str
    status: ManualReviewStatus
    version: int
    history: tuple[ReviewEvent, ...]


class ExportBlockCode(StrEnum):
    TENANT_MISMATCH = "tenant_mismatch"
    PERMISSION_DENIED = "permission_denied"
    PROJECT_VERSION_CHANGED = "project_version_changed"
    SNAPSHOT_NOT_IMMUTABLE = "snapshot_not_immutable"
    COMPLIANCE_NOT_APPROVED = "compliance_not_approved"
    MANUAL_REVIEW_INCOMPLETE = "manual_review_incomplete"
    AUTHORIZATION_INCOMPLETE = "authorization_incomplete"
    BILLING_NOT_SETTLED = "billing_not_settled"
    AIGC_MARKING_MISSING = "aigc_marking_missing"
    COMMERCIAL_EXPORT_DISABLED = "commercial_export_disabled"


@dataclass(frozen=True, slots=True)
class FormalExportRequest:
    project_id: str
    project_version: str
    target: str


@dataclass(frozen=True, slots=True)
class ExportAuthorityState:
    tenant_id: str
    current_project_version: str
    snapshot_is_immutable: bool
    has_export_permission: bool
    compliance_conclusion: ReviewConclusion
    compliance_project_version: str
    manual_review_status: ManualReviewStatus
    authorizations_complete: bool
    billing_settled: bool
    aigc_marking_satisfied: bool
    commercial_export_enabled: bool


class ExportAuthority(Protocol):
    async def load_export_authority(
        self, request: FormalExportRequest, audit: AuditContext
    ) -> ExportAuthorityState: ...


@dataclass(frozen=True, slots=True)
class ExportManifestContext:
    project_id: str
    project_version: str
    target: str
    tenant_id: str
    actor_id: str
    request_id: str
    authorized_at: datetime


@dataclass(frozen=True, slots=True)
class ExportGateDecision:
    allowed: bool
    block_codes: tuple[ExportBlockCode, ...]
    audit: AuditContext
    manifest_context: ExportManifestContext | None
