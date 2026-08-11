from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from server.xingjing_compliance.models import (
    ComplianceProvider,
    PolicyEffect,
    ProviderReviewRequest,
    ProviderReviewResult,
    ReviewConclusion,
    RiskItem,
    RiskLevel,
)


class ComplianceProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class HttpComplianceProvider(ComplianceProvider):
    def __init__(self, *, url: str, bearer_token: str) -> None:
        if not url.startswith(("https://", "http://")) or not bearer_token.strip():
            raise ComplianceProviderError("COMPLIANCE_PROVIDER_CONFIGURATION_INVALID")
        self._url = url
        self._bearer_token = bearer_token

    async def review(self, request: ProviderReviewRequest) -> ProviderReviewResult:
        payload = {
            "projectId": request.project_id,
            "projectVersion": request.project_version,
            "inputDigest": request.input_digest,
            "facts": dict(request.facts),
            "audit": {
                "actorId": request.audit.actor_id,
                "tenantId": request.audit.tenant_id,
                "requestId": request.audit.request_id,
                "occurredAt": request.audit.occurred_at.isoformat(),
            },
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._bearer_token}", "Accept": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ComplianceProviderError("COMPLIANCE_PROVIDER_REQUEST_FAILED") from error
        if not isinstance(body, Mapping):
            raise ComplianceProviderError("COMPLIANCE_PROVIDER_RESPONSE_INVALID")
        try:
            provider_id = _text(body, "providerId")
            provider_version = _text(body, "providerVersion")
            conclusion = ReviewConclusion(_text(body, "conclusion"))
            evidence_digest = _text(body, "evidenceDigest")
            policy_digest = _text(body, "policyDigest")
            policy_summary_raw = body.get("policySummary", {})
            if not isinstance(policy_summary_raw, Mapping):
                raise ValueError
            policy_summary = {str(key): item for key, item in policy_summary_raw.items()}
            raw_risks = body.get("risks", [])
            if not isinstance(raw_risks, list):
                raise ValueError
            risks = tuple(_risk(item) for item in raw_risks)
        except (TypeError, ValueError) as error:
            raise ComplianceProviderError("COMPLIANCE_PROVIDER_RESPONSE_INVALID") from error
        if conclusion is ReviewConclusion.APPROVED and risks:
            raise ComplianceProviderError("COMPLIANCE_PROVIDER_APPROVAL_HAS_RISKS")
        if any(not digest.startswith("sha256:") or len(digest) != 71 for digest in (evidence_digest, policy_digest)):
            raise ComplianceProviderError("COMPLIANCE_PROVIDER_EVIDENCE_DIGEST_INVALID")
        return ProviderReviewResult(
            provider_id=provider_id,
            provider_version=provider_version,
            conclusion=conclusion,
            risks=risks,
            evidence_digest=evidence_digest,
            policy_digest=policy_digest,
            policy_summary=policy_summary,
        )


def _risk(value: object) -> RiskItem:
    if not isinstance(value, Mapping):
        raise ValueError
    evidence = value.get("evidence", {})
    if not isinstance(evidence, Mapping):
        raise ValueError
    return RiskItem(
        rule_id=_text(value, "ruleId"),
        risk_level=RiskLevel(_text(value, "riskLevel")),
        effect=PolicyEffect(_text(value, "effect")),
        message=_text(value, "message"),
        evidence={str(key): item for key, item in evidence.items()},
        missing_authorization_type=_optional_text(value, "missingAuthorizationType"),
    )


def _text(value: Mapping[Any, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError
    return item.strip()


def _optional_text(value: Mapping[Any, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ValueError
    return item.strip()
