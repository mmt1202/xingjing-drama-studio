from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import uuid4

from .models import (
    Actor,
    AdminRole,
    Appeal,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalVote,
    AuditEvent,
    AuthorizationError,
    ConflictError,
    ReviewDecision,
    ReviewRecord,
    ReviewStatus,
    RightsEvidence,
    RightsKind,
    RiskFinding,
    RiskLevel,
    RuleTerm,
    RuleVersion,
    SubjectSnapshot,
)
from .ports import AuditPort, ReviewPort, RulePort

_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
_RIGHT_CODES = {kind: f"missing_{kind.value}_rights" for kind in RightsKind}


class GovernanceService:
    def __init__(self, rules: RulePort, reviews: ReviewPort, audit: AuditPort) -> None:
        self.rules, self.reviews, self.audit = rules, reviews, audit

    @staticmethod
    def _require(actor: Actor, permission: str) -> None:
        if permission not in actor.permissions:
            raise AuthorizationError(f"missing permission: {permission}")

    def _log(self, actor: Actor, action: str, kind: str, identifier: str, before, after) -> None:
        self.audit.append_audit(
            AuditEvent(
                str(uuid4()),
                actor.tenant_id,
                actor.actor_id,
                action,
                kind,
                identifier,
                before,
                after,
                actor.request_id,
                "success",
                actor.ip,
                actor.device,
            )
        )

    def publish_rules(self, actor: Actor, terms: list[RuleTerm], *, idempotency_key: str) -> RuleVersion:
        self._require(actor, "admin.compliance.manage")

        def op() -> RuleVersion:
            if any(not term.phrase.strip() or not term.code.strip() for term in terms):
                raise ValueError("rule phrase and code are required")
            item = RuleVersion(
                str(uuid4()),
                actor.tenant_id,
                self.rules.next_rule_sequence(actor.tenant_id),
                tuple(terms),
                actor.actor_id,
            )
            self.rules.save_rule_version(item)
            self._log(actor, "sensitive_rules.published", "rule_version", item.id, None, asdict(item))
            return item

        return self.rules.atomic(actor.tenant_id, f"publish_rules:{idempotency_key}", op)

    def screen(
        self, actor: Actor, subject: SubjectSnapshot, *, rights: tuple[RightsEvidence, ...], idempotency_key: str
    ) -> ReviewRecord:
        self._require(actor, "admin.compliance.manage")

        def op() -> ReviewRecord:
            version = self.rules.current_rule_version(actor.tenant_id)
            if version is None:
                raise ConflictError("no published rule version")
            text = "\n".join(subject.text_fields.values()).casefold()
            findings = [
                RiskFinding(term.code, term.level, term.phrase)
                for term in version.terms
                if term.phrase.casefold() in text
            ]
            now = datetime.now(UTC)
            valid = {e.kind for e in rights if e.is_valid_at(now)}
            findings += [
                RiskFinding(_RIGHT_CODES[k], RiskLevel.HIGH, k.value) for k in subject.rights_risks if k not in valid
            ]
            level = max((f.level for f in findings), key=lambda item: _ORDER[item], default=RiskLevel.LOW)
            status = ReviewStatus.BLOCKED if _ORDER[level] >= _ORDER[RiskLevel.HIGH] else ReviewStatus.PENDING
            item = ReviewRecord(str(uuid4()), actor.tenant_id, subject, version.id, level, tuple(findings), status)
            self.reviews.save_review(item)
            self._log(actor, "compliance.screened", subject.object_type, subject.object_id, None, asdict(item))
            return item

        return self.rules.atomic(actor.tenant_id, f"screen:{idempotency_key}", op)

    def get_review(self, actor: Actor, review_id: str) -> ReviewRecord:
        self._require(actor, "admin.compliance.view")
        return self.reviews.get_review(actor.tenant_id, review_id)

    def list_review_queue(self, actor: Actor, *, status: ReviewStatus | None = None) -> tuple[ReviewRecord, ...]:
        self._require(actor, "admin.compliance.view")
        return tuple(self.reviews.list_reviews(actor.tenant_id, status=status.value if status else None))

    def claim_review(self, actor: Actor, review_id: str, idempotency_key: str) -> ReviewRecord:
        self._require(actor, "admin.compliance.manage")

        def op() -> ReviewRecord:
            old = self.reviews.get_review(actor.tenant_id, review_id)
            if old.status not in (ReviewStatus.PENDING, ReviewStatus.BLOCKED) or old.assignee_id not in (
                None,
                actor.actor_id,
            ):
                raise ConflictError("review is not claimable")
            new = replace(old, status=ReviewStatus.CLAIMED, assignee_id=actor.actor_id, version=old.version + 1)
            self.reviews.save_review(new)
            self._log(actor, "review.claimed", "review", old.id, asdict(old), asdict(new))
            return new

        return self.rules.atomic(actor.tenant_id, f"claim_review:{idempotency_key}", op)

    def decide_review(
        self, actor: Actor, review_id: str, decision: ReviewDecision, reason: str, idempotency_key: str
    ) -> ReviewRecord:
        self._require(actor, "admin.compliance.manage")

        def op() -> ReviewRecord:
            old = self.reviews.get_review(actor.tenant_id, review_id)
            if old.status is not ReviewStatus.CLAIMED or old.assignee_id != actor.actor_id:
                raise ConflictError("only assigned reviewer can decide")
            if decision is ReviewDecision.PASS and _ORDER[old.level] >= _ORDER[RiskLevel.HIGH]:
                raise AuthorizationError("high-risk release requires two approvals")
            status = ReviewStatus.RELEASED if decision is ReviewDecision.PASS else ReviewStatus.BLOCKED
            new = replace(old, status=status, decision_reason=reason, version=old.version + 1)
            self.reviews.save_review(new)
            self._log(actor, f"review.{decision.value}", "review", old.id, asdict(old), asdict(new))
            return new

        return self.rules.atomic(actor.tenant_id, f"decide_review:{idempotency_key}", op)

    def submit_appeal(
        self, actor: Actor, review_id: str, reason: str, evidence_uris: tuple[str, ...], idempotency_key: str
    ) -> Appeal:
        self._require(actor, "compliance.appeal")

        def op() -> Appeal:
            old = self.reviews.get_review(actor.tenant_id, review_id)
            if old.status is not ReviewStatus.BLOCKED:
                raise ConflictError("only blocked reviews can be appealed")
            item = Appeal(str(uuid4()), actor.tenant_id, review_id, actor.actor_id, reason, evidence_uris)
            self.reviews.save_appeal(item)
            new = replace(old, status=ReviewStatus.APPEALED, version=old.version + 1)
            self.reviews.save_review(new)
            self._log(actor, "review.appealed", "review", old.id, asdict(old), asdict(new))
            return item

        return self.rules.atomic(actor.tenant_id, f"submit_appeal:{idempotency_key}", op)

    def request_high_risk_release(
        self, actor: Actor, appeal_id: str, reason: str, idempotency_key: str
    ) -> ApprovalRequest:
        self._require(actor, "admin.compliance.manage")

        def op() -> ApprovalRequest:
            appeal = self.reviews.get_appeal(actor.tenant_id, appeal_id)
            old = self.reviews.get_review(actor.tenant_id, appeal.review_id)
            if old.status is not ReviewStatus.APPEALED:
                raise ConflictError("appeal is not pending")
            item = ApprovalRequest(str(uuid4()), actor.tenant_id, old.id, actor.actor_id, reason)
            self.reviews.save_approval(item)
            self.reviews.save_review(replace(old, status=ReviewStatus.RELEASE_PENDING, version=old.version + 1))
            self._log(actor, "high_risk_release.requested", "approval", item.id, None, asdict(item))
            return item

        return self.rules.atomic(actor.tenant_id, f"request_release:{idempotency_key}", op)

    def approve_high_risk_release(
        self, actor: Actor, approval_id: str, decision: ApprovalDecision, idempotency_key: str
    ) -> ApprovalRequest:
        self._require(actor, "admin.security.manage")

        def op() -> ApprovalRequest:
            old = self.reviews.get_approval(actor.tenant_id, approval_id)
            if old.completed or old.rejected:
                raise ConflictError("approval is final")
            if actor.actor_id == old.requested_by or any(v.actor_id == actor.actor_id for v in old.votes):
                raise AuthorizationError("requester and prior approvers cannot vote")
            votes = old.votes + (ApprovalVote(actor.actor_id, decision, actor.request_id),)
            rejected = decision is ApprovalDecision.REJECT
            completed = not rejected and sum(v.decision is ApprovalDecision.APPROVE for v in votes) >= 2
            new = replace(old, votes=votes, completed=completed, rejected=rejected)
            self.reviews.save_approval(new)
            if completed:
                review = self.reviews.get_review(actor.tenant_id, new.review_id)
                self.reviews.save_review(replace(review, status=ReviewStatus.RELEASED, version=review.version + 1))
            self._log(actor, f"high_risk_release.{decision.value}", "approval", old.id, asdict(old), asdict(new))
            return new

        return self.rules.atomic(actor.tenant_id, f"approve_release:{idempotency_key}", op)

    def save_role(
        self,
        actor: Actor,
        name: str,
        permissions: frozenset[str],
        *,
        expected_version: int,
        idempotency_key: str,
        role_id: str | None = None,
    ) -> AdminRole:
        self._require(actor, "admin.security.manage")

        def op() -> AdminRole:
            old = self.reviews.get_role(actor.tenant_id, role_id) if role_id else None
            if (old and old.version != expected_version) or (old is None and expected_version != 0):
                raise ConflictError("VERSION_CONFLICT")
            item = AdminRole(role_id or str(uuid4()), actor.tenant_id, name, permissions, expected_version + 1)
            self.reviews.save_role(item)
            self._log(actor, "admin_role.saved", "admin_role", item.id, asdict(old) if old else None, asdict(item))
            return item

        return self.rules.atomic(actor.tenant_id, f"save_role:{idempotency_key}", op)

    def get_role(self, actor: Actor, role_id: str) -> AdminRole:
        self._require(actor, "admin.security.view")
        return self.reviews.get_role(actor.tenant_id, role_id)
