from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol

from .redaction import redact


class EvidenceSource(StrEnum):
    PROBE = "probe"
    TEST = "test"
    BACKEND = "backend"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class RuntimeEvidence:
    evidence_id: str
    subject: str
    source: EvidenceSource
    source_ref: str
    outcome: str
    observed_at: str
    facts: dict[str, Any]
    schema_version: str = "1.0"

    @classmethod
    def create(
        cls,
        *,
        evidence_id: str,
        subject: str,
        source: EvidenceSource,
        source_ref: str,
        outcome: str,
        observed_at: str,
        facts: dict[str, Any],
    ) -> RuntimeEvidence:
        required = (evidence_id, subject, source_ref, outcome, observed_at)
        if any(not value.strip() for value in required):
            raise ValueError("runtime evidence identity, source, outcome and observation time are required")
        return cls(evidence_id, subject, source, source_ref, outcome, observed_at, redact(facts))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class EvidenceSink(Protocol):
    """审计存储、对象存储或证据仓库应实现的持久化端口。"""

    def write(self, evidence: RuntimeEvidence) -> None: ...


class InMemoryEvidenceSink:
    """仅供测试的适配器；其内容不得宣称为生产运行证据。"""

    def __init__(self) -> None:
        self.items: list[RuntimeEvidence] = []

    def write(self, evidence: RuntimeEvidence) -> None:
        self.items.append(evidence)
