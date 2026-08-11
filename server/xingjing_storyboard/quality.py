from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from .errors import ContractViolation
from .models import AssetKind, QualityIssue, QualitySeverity, Shot


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    """由系统配置提供的 M05 质量阈值与合规词表，不在领域代码中硬编码。"""

    minimum_duration_ms: int
    maximum_duration_ms: int
    max_adjacent_duration_delta_ms: int
    max_dialogue_characters_per_second: int
    require_scene_reference: bool
    require_character_for_dialogue: bool
    compliance_terms: frozenset[str]

    def __post_init__(self) -> None:
        if (
            self.minimum_duration_ms < 1
            or self.maximum_duration_ms < self.minimum_duration_ms
            or self.max_adjacent_duration_delta_ms < 0
            or self.max_dialogue_characters_per_second < 1
        ):
            raise ContractViolation("INVALID_QUALITY_POLICY")
        if any(not term.strip() for term in self.compliance_terms):
            raise ContractViolation("INVALID_QUALITY_POLICY")


class QualityEvaluator:
    def __init__(self, policy: QualityPolicy) -> None:
        self._policy = policy

    def evaluate(self, shots: tuple[Shot, ...]) -> tuple[Shot, ...]:
        evaluated: list[Shot] = []
        previous: Shot | None = None
        for shot in shots:
            issues = self._issues_for(shot, previous)
            evaluated.append(replace(shot, quality_issues=tuple(issues)))
            previous = shot
        return tuple(evaluated)

    def _issues_for(self, shot: Shot, previous: Shot | None) -> list[QualityIssue]:
        kinds = {reference.kind for reference in shot.asset_references}
        issues: list[QualityIssue] = []
        if self._policy.require_scene_reference and AssetKind.SCENE not in kinds:
            issues.append(self._issue(shot, "MISSING_SCENE", QualitySeverity.ERROR, "asset_references", {}))
        if self._policy.require_character_for_dialogue and shot.dialogue.strip() and AssetKind.CHARACTER not in kinds:
            issues.append(self._issue(shot, "MISSING_CHARACTER", QualitySeverity.ERROR, "asset_references", {}))
        if not self._policy.minimum_duration_ms <= shot.duration_ms <= self._policy.maximum_duration_ms:
            issues.append(
                self._issue(
                    shot,
                    "DURATION_OUT_OF_BOUNDS",
                    QualitySeverity.WARNING,
                    "duration_ms",
                    {
                        "minimum_duration_ms": self._policy.minimum_duration_ms,
                        "maximum_duration_ms": self._policy.maximum_duration_ms,
                        "actual_duration_ms": shot.duration_ms,
                    },
                )
            )
        capacity = max(1, shot.duration_ms // 1_000) * self._policy.max_dialogue_characters_per_second
        if len(shot.dialogue) > capacity:
            issues.append(
                self._issue(
                    shot,
                    "DIALOGUE_TOO_LONG",
                    QualitySeverity.WARNING,
                    "dialogue",
                    {"characters": len(shot.dialogue), "capacity": capacity},
                )
            )
        if (
            previous is not None
            and abs(previous.duration_ms - shot.duration_ms) > self._policy.max_adjacent_duration_delta_ms
        ):
            issues.append(
                self._issue(
                    shot,
                    "RHYTHM_BREAK",
                    QualitySeverity.WARNING,
                    "duration_ms",
                    {
                        "previous_duration_ms": previous.duration_ms,
                        "actual_duration_ms": shot.duration_ms,
                        "maximum_delta_ms": self._policy.max_adjacent_duration_delta_ms,
                    },
                )
            )
        text = f"{shot.dialogue}\n{shot.prompt}".casefold()
        for term in sorted(self._policy.compliance_terms, key=str.casefold):
            if term.casefold() in text:
                issues.append(
                    self._issue(
                        shot,
                        "COMPLIANCE_RISK",
                        QualitySeverity.ERROR,
                        "prompt",
                        {"matched_term": term},
                    )
                )
        return issues

    @staticmethod
    def _issue(
        shot: Shot,
        code: str,
        severity: QualitySeverity,
        field: str,
        details: dict[str, object],
    ) -> QualityIssue:
        source = json.dumps(
            {"shot_id": shot.shot_id, "code": code, "field": field},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return QualityIssue(
            issue_id=f"quality-{hashlib.sha256(source).hexdigest()[:24]}",
            shot_id=shot.shot_id,
            code=code,
            severity=severity,
            field=field,
            details=details,
        )
