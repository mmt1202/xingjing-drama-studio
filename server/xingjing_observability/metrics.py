from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class MetricType(StrEnum):
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    GAUGE = "gauge"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    metric_type: MetricType
    description: str
    allowed_labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class MetricObservation:
    definition: MetricDefinition
    value: float
    labels: dict[str, str]


class MetricSink(Protocol):
    """Prometheus/OTel 等真实指标后端应实现的端口。"""

    def record(self, observation: MetricObservation) -> None: ...


class LabelContractError(ValueError):
    pass


class CardinalityLimitError(ValueError):
    pass


class InMemoryMetricSink:
    """确定性的契约测试适配器，不代表生产监控后端。"""

    def __init__(self) -> None:
        self.observations: list[MetricObservation] = []

    def record(self, observation: MetricObservation) -> None:
        self.observations.append(observation)


class MetricRegistry:
    def __init__(
        self,
        sink: MetricSink,
        definitions: tuple[MetricDefinition, ...],
        *,
        max_values_per_label: int = 100,
    ) -> None:
        if max_values_per_label < 1:
            raise ValueError("max_values_per_label must be positive")
        self._sink = sink
        self._definitions = {definition.name: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("metric names must be unique")
        self._max_values_per_label = max_values_per_label
        self._seen_values: dict[tuple[str, str], set[str]] = {}

    def record(self, name: str, value: float, labels: dict[str, str]) -> None:
        try:
            definition = self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"undefined metric: {name}") from exc
        actual = frozenset(labels)
        if actual != definition.allowed_labels:
            missing = sorted(definition.allowed_labels - actual)
            unexpected = sorted(actual - definition.allowed_labels)
            raise LabelContractError(f"invalid labels for {name}; missing={missing}, unexpected={unexpected}")
        pending: list[tuple[set[str], str, str]] = []
        for label, label_value in labels.items():
            values = self._seen_values.setdefault((name, label), set())
            if label_value not in values and len(values) >= self._max_values_per_label:
                raise CardinalityLimitError(f"cardinality limit reached for {name}.{label}")
            pending.append((values, label, label_value))
        for values, _label, label_value in pending:
            values.add(label_value)
        self._sink.record(MetricObservation(definition, float(value), dict(labels)))


def standard_metric_definitions() -> tuple[MetricDefinition, ...]:
    def metric(name: str, kind: MetricType, description: str, *labels: str) -> MetricDefinition:
        return MetricDefinition(name, kind, description, frozenset(labels))

    return (
        metric(
            "xingjing_api_requests_total",
            MetricType.COUNTER,
            "API requests",
            "method",
            "route",
            "status_class",
        ),
        metric(
            "xingjing_api_request_duration_seconds",
            MetricType.HISTOGRAM,
            "API request duration",
            "method",
            "route",
        ),
        metric("xingjing_queue_depth", MetricType.GAUGE, "Queue depth", "queue", "task_type", "state"),
        metric("xingjing_model_calls_total", MetricType.COUNTER, "Model calls", "provider", "capability", "outcome"),
        metric("xingjing_media_operations_total", MetricType.COUNTER, "Media operations", "operation", "outcome"),
        metric("xingjing_database_operations_total", MetricType.COUNTER, "Database operations", "operation", "outcome"),
        metric(
            "xingjing_object_storage_operations_total",
            MetricType.COUNTER,
            "Object storage operations",
            "operation",
            "outcome",
        ),
        metric("xingjing_billing_operations_total", MetricType.COUNTER, "Billing operations", "operation", "outcome"),
    )
