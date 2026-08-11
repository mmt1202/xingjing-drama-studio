from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Comparison(StrEnum):
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"

    def matches(self, value: float, threshold: float) -> bool:
        return {
            self.GREATER_THAN: value > threshold,
            self.GREATER_THAN_OR_EQUAL: value >= threshold,
            self.LESS_THAN: value < threshold,
            self.LESS_THAN_OR_EQUAL: value <= threshold,
        }[self]


class IncidentStatus(StrEnum):
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class AlertRule:
    rule_id: str
    metric: str
    comparison: Comparison
    threshold: float
    severity: str
    owner: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Suppression:
    suppression_id: str
    rule_id: str
    environment: str
    starts_at: str
    ends_at: str
    reason: str
    actor_id: str

    def applies(self, *, rule_id: str, environment: str, observed_at: str) -> bool:
        instant = _instant(observed_at)
        return (
            self.rule_id == rule_id
            and self.environment == environment
            and _instant(self.starts_at) <= instant < _instant(self.ends_at)
        )


@dataclass(frozen=True, slots=True)
class Incident:
    incident_id: str
    rule_id: str
    metric: str
    environment: str
    severity: str
    owner: str
    status: IncidentStatus
    value: float
    threshold: float
    started_at: str
    updated_at: str
    acknowledged_by: str | None = None
    suppression_id: str | None = None


class AlertEngine:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], Incident] = {}
        self._incidents: dict[str, Incident] = {}
        self._suppressions: list[Suppression] = []

    def add_suppression(self, suppression: Suppression) -> None:
        if _instant(suppression.starts_at) >= _instant(suppression.ends_at):
            raise ValueError("suppression end must be after start")
        if any(item.suppression_id == suppression.suppression_id for item in self._suppressions):
            raise ValueError(f"duplicate suppression: {suppression.suppression_id}")
        self._suppressions.append(suppression)

    def evaluate(self, rule: AlertRule, *, value: float, environment: str, observed_at: str) -> Incident | None:
        key = (rule.rule_id, environment)
        active = self._active.get(key)
        violating = rule.enabled and rule.comparison.matches(value, rule.threshold)
        suppression = next(
            (
                item
                for item in self._suppressions
                if item.applies(rule_id=rule.rule_id, environment=environment, observed_at=observed_at)
            ),
            None,
        )
        if not violating:
            if active is None:
                return None
            incident = replace(active, status=IncidentStatus.RESOLVED, value=value, updated_at=observed_at)
            self._active.pop(key, None)
        elif active is not None and active.status is not IncidentStatus.SUPPRESSED and suppression is None:
            incident = replace(active, value=value, updated_at=observed_at)
        else:
            incident_id = str(uuid5(NAMESPACE_URL, f"xingjing:{rule.rule_id}:{environment}:{observed_at}"))
            incident = Incident(
                incident_id=incident_id,
                rule_id=rule.rule_id,
                metric=rule.metric,
                environment=environment,
                severity=rule.severity,
                owner=rule.owner,
                status=IncidentStatus.SUPPRESSED if suppression else IncidentStatus.FIRING,
                value=value,
                threshold=rule.threshold,
                started_at=observed_at,
                updated_at=observed_at,
                suppression_id=suppression.suppression_id if suppression else None,
            )
            if suppression is None:
                self._active[key] = incident
        self._incidents[incident.incident_id] = incident
        return incident

    def acknowledge(self, incident_id: str, *, actor_id: str, observed_at: str) -> Incident:
        try:
            incident = self._incidents[incident_id]
        except KeyError as exc:
            raise KeyError(f"unknown incident: {incident_id}") from exc
        if incident.status is not IncidentStatus.FIRING:
            raise ValueError(f"incident cannot be acknowledged from {incident.status}")
        updated = replace(
            incident,
            status=IncidentStatus.ACKNOWLEDGED,
            acknowledged_by=actor_id,
            updated_at=observed_at,
        )
        self._incidents[incident_id] = updated
        self._active[(incident.rule_id, incident.environment)] = updated
        return updated
