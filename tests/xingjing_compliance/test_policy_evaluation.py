from datetime import UTC, datetime

from server.xingjing_compliance import (
    AuthorizationReference,
    PolicyEffect,
    PolicyRule,
    PolicySet,
    ReviewConclusion,
    RiskLevel,
    evaluate_policy,
)


def test_policy_evaluation_blocks_matching_risk_and_missing_authorization() -> None:
    policy = PolicySet(
        policy_id="formal-export-cn",
        version="2026.07.15",
        effective_at=datetime(2026, 7, 15, tzinfo=UTC),
        rules=(
            PolicyRule(
                rule_id="public-figure",
                fact_key="contains_public_figure",
                expected_value=True,
                risk_level=RiskLevel.HIGH,
                effect=PolicyEffect.BLOCK,
                message="真人形象需要人工复核",
                required_authorization_type="likeness",
            ),
        ),
    )

    result = evaluate_policy(
        policy=policy,
        project_id="project-1",
        project_version="v7",
        facts={"contains_public_figure": True},
        authorizations=(),
        evaluated_at=datetime(2026, 7, 15, 8, tzinfo=UTC),
    )

    assert result.conclusion is ReviewConclusion.BLOCKED
    assert result.policy_version == "2026.07.15"
    assert result.project_version == "v7"
    assert result.input_digest
    assert [(item.rule_id, item.missing_authorization_type) for item in result.risks] == [("public-figure", "likeness")]


def test_policy_evaluation_allows_rule_when_valid_authorization_is_bound() -> None:
    policy = PolicySet(
        policy_id="formal-export-cn",
        version="2",
        effective_at=datetime(2026, 7, 15, tzinfo=UTC),
        rules=(
            PolicyRule(
                rule_id="adaptation-right",
                fact_key="uses_adapted_ip",
                expected_value=True,
                risk_level=RiskLevel.HIGH,
                effect=PolicyEffect.BLOCK,
                message="缺少改编权",
                required_authorization_type="ip_adaptation",
            ),
        ),
    )
    authorization = AuthorizationReference(
        authorization_id="right-9",
        authorization_type="ip_adaptation",
        subject_id="novel-3",
        evidence_digest="sha256:evidence",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2027, 1, 1, tzinfo=UTC),
        revoked_at=None,
    )

    result = evaluate_policy(
        policy=policy,
        project_id="project-1",
        project_version="v7",
        facts={"uses_adapted_ip": True},
        authorizations=(authorization,),
        evaluated_at=datetime(2026, 7, 15, 8, tzinfo=UTC),
    )

    assert result.conclusion is ReviewConclusion.APPROVED
    assert result.risks == ()
    assert result.authorization_ids == ("right-9",)
