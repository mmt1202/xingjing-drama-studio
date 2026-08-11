from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

from .models import (
    AuthorizationReference,
    ComplianceAssessment,
    ManualReviewStatus,
    PolicyEffect,
    PolicySet,
    ReviewConclusion,
    RiskItem,
)


def _digest_facts(facts: dict[str, object]) -> str:
    canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def evaluate_policy(
    *,
    policy: PolicySet,
    project_id: str,
    project_version: str,
    facts: dict[str, object],
    authorizations: tuple[AuthorizationReference, ...],
    evaluated_at: datetime,
) -> ComplianceAssessment:
    valid_authorizations = tuple(item for item in authorizations if item.is_valid_at(evaluated_at))
    valid_types = {item.authorization_type for item in valid_authorizations}
    risks: list[RiskItem] = []

    for rule in policy.rules:
        if facts.get(rule.fact_key) != rule.expected_value:
            continue
        missing_type = (
            rule.required_authorization_type
            if rule.required_authorization_type and rule.required_authorization_type not in valid_types
            else None
        )
        if rule.required_authorization_type and missing_type is None:
            continue
        risks.append(
            RiskItem(
                rule_id=rule.rule_id,
                risk_level=rule.risk_level,
                effect=rule.effect,
                message=rule.message,
                evidence={"fact_key": rule.fact_key, "observed_value": facts.get(rule.fact_key)},
                missing_authorization_type=missing_type,
            )
        )

    effects = {item.effect for item in risks}
    if PolicyEffect.BLOCK in effects:
        conclusion = ReviewConclusion.BLOCKED
        review_status = ManualReviewStatus.PENDING
    elif PolicyEffect.REQUIRE_MANUAL_REVIEW in effects:
        conclusion = ReviewConclusion.MANUAL_REVIEW_REQUIRED
        review_status = ManualReviewStatus.PENDING
    else:
        conclusion = ReviewConclusion.APPROVED
        review_status = ManualReviewStatus.NOT_REQUIRED

    return ComplianceAssessment(
        assessment_id=str(uuid4()),
        project_id=project_id,
        project_version=project_version,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        input_digest=_digest_facts(facts),
        conclusion=conclusion,
        manual_review_status=review_status,
        risks=tuple(risks),
        authorization_ids=tuple(item.authorization_id for item in valid_authorizations),
        evaluated_at=evaluated_at,
    )
