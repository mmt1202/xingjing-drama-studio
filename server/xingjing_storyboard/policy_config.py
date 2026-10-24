"""Trusted deployment configuration for deterministic editorial checks."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from .errors import ContractViolation
from .quality import QualityPolicy

EDITORIAL_POLICY_V1 = QualityPolicy(
    minimum_duration_ms=1_000,
    maximum_duration_ms=30_000,
    max_adjacent_duration_delta_ms=10_000,
    max_dialogue_characters_per_second=12,
    require_scene_reference=True,
    require_character_for_dialogue=True,
    compliance_terms=frozenset(),
    policy_id="deterministic-editorial",
    policy_version=1,
)


class QualityPolicyConfigurationError(ValueError):
    pass


def load_quality_policy(environ: Mapping[str, str] | None = None) -> QualityPolicy:
    """Load one immutable policy; an explicitly invalid override never falls back."""
    variables = os.environ if environ is None else environ
    if "XINGJING_STORYBOARD_QUALITY_POLICY_PATH" not in variables:
        return EDITORIAL_POLICY_V1
    path = variables["XINGJING_STORYBOARD_QUALITY_POLICY_PATH"].strip()
    try:
        if not path:
            raise ValueError("empty configuration path")
        source = Path(path)
        if source.stat().st_size > 131_072:
            raise ValueError("configuration too large")
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"schema", "policyId", "version", "rules"}:
            raise ValueError("invalid configuration structure")
        rules = payload["rules"]
        if (
            payload["schema"] != "xingjing.storyboard-quality/v1"
            or not isinstance(rules, dict)
            or set(rules) != set(EDITORIAL_POLICY_V1.rules())
            or rules["require_scene_reference"] is not True
            or rules["require_character_for_dialogue"] is not True
        ):
            raise ValueError("invalid editorial policy")
        terms = rules["compliance_terms"]
        if (
            not isinstance(terms, list)
            or len(terms) > 1024
            or any(not isinstance(term, str) or not term.strip() or len(term) > 256 for term in terms)
        ):
            raise ValueError("invalid keyword configuration")
        return QualityPolicy(
            minimum_duration_ms=rules["minimum_duration_ms"],
            maximum_duration_ms=rules["maximum_duration_ms"],
            max_adjacent_duration_delta_ms=rules["max_adjacent_duration_delta_ms"],
            max_dialogue_characters_per_second=rules["max_dialogue_characters_per_second"],
            require_scene_reference=rules["require_scene_reference"],
            require_character_for_dialogue=rules["require_character_for_dialogue"],
            compliance_terms=frozenset(terms),
            policy_id=payload["policyId"],
            policy_version=payload["version"],
        )
    except (OSError, ValueError, TypeError, KeyError, ContractViolation) as error:
        raise QualityPolicyConfigurationError("STORYBOARD_QUALITY_POLICY_INVALID") from error
