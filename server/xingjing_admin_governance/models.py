from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class GovernanceError(Exception):
    pass


class AuthorizationError(GovernanceError):
    pass


class ConflictError(GovernanceError):
    pass


class NotFoundError(GovernanceError):
    pass


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"
    CLAIMED = "claimed"
    APPEALED = "appealed"
    RELEASE_PENDING = "release_pending"
    RELEASED = "released"


class ReviewDecision(StrEnum):
    BLOCK = "block"
    PASS = "pass"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RightsKind(StrEnum):
    REAL_PERSON = "real_person"
    IP_ADAPTATION = "ip_adaptation"
    BRAND = "brand"
    MUSIC = "music"
    FONT = "font"


@dataclass(frozen=True, slots=True)
class Actor:
    tenant_id: str
    actor_id: str
    permissions: frozenset[str]
    request_id: str
    ip: str | None = None
    device: str | None = None


@dataclass(frozen=True, slots=True)
class RuleTerm:
    phrase: str
    level: RiskLevel
    code: str


@dataclass(frozen=True, slots=True)
class RuleVersion:
    id: str
    tenant_id: str
    sequence: int
    terms: tuple[RuleTerm, ...]
    published_by: str
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class SubjectSnapshot:
    object_type: str
    object_id: str
    digest: str
    text_fields: Mapping[str, str]
    rights_risks: tuple[RightsKind, ...] = ()


@dataclass(frozen=True, slots=True)
class RightsEvidence:
    kind: RightsKind
    evidence_uri: str
    valid_until: datetime | None = None

    def is_valid_at(self, moment: datetime) -> bool:
        return bool(self.evidence_uri) and (self.valid_until is None or self.valid_until > moment)


@dataclass(frozen=True, slots=True)
class RiskFinding:
    code: str
    level: RiskLevel
    evidence: str


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    id: str
    tenant_id: str
    subject: SubjectSnapshot
    rule_version_id: str
    level: RiskLevel
    findings: tuple[RiskFinding, ...]
    status: ReviewStatus
    assignee_id: str | None = None
    decision_reason: str | None = None
    version: int = 1


@dataclass(frozen=True, slots=True)
class Appeal:
    id: str
    tenant_id: str
    review_id: str
    appellant_id: str
    reason: str
    evidence_uris: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalVote:
    actor_id: str
    decision: ApprovalDecision
    request_id: str


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    id: str
    tenant_id: str
    review_id: str
    requested_by: str
    reason: str
    votes: tuple[ApprovalVote, ...] = ()
    completed: bool = False
    rejected: bool = False


@dataclass(frozen=True, slots=True)
class AdminRole:
    id: str
    tenant_id: str
    name: str
    permissions: frozenset[str]
    version: int


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    tenant_id: str
    actor_id: str
    action: str
    object_type: str
    object_id: str
    before: Mapping[str, object] | None
    after: Mapping[str, object] | None
    request_id: str
    result: str
    ip: str | None = None
    device: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
