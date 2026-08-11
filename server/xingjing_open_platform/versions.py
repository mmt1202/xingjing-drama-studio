from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class VersionStatus(StrEnum):
    SUPPORTED = "supported"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class VersionDecision:
    status: VersionStatus
    version: int | None
    current_version: int
    sunset_at: datetime | None = None


class ApiVersionPolicy:
    def __init__(self, *, current: int, minimum_supported: int, deprecated: dict[int, datetime]) -> None:
        if current < minimum_supported or minimum_supported < 1:
            raise ValueError("invalid version range")
        self._current = current
        self._minimum = minimum_supported
        self._deprecated = dict(deprecated)

    def evaluate(self, requested: str, *, now: datetime) -> VersionDecision:
        if not requested.startswith("v") or not requested[1:].isdigit():
            return VersionDecision(VersionStatus.REJECTED, None, self._current)
        version = int(requested[1:])
        if version < self._minimum or version > self._current:
            return VersionDecision(VersionStatus.REJECTED, version, self._current)
        sunset = self._deprecated.get(version)
        if sunset is not None:
            if now >= sunset:
                return VersionDecision(VersionStatus.REJECTED, version, self._current, sunset)
            return VersionDecision(VersionStatus.DEPRECATED, version, self._current, sunset)
        return VersionDecision(VersionStatus.SUPPORTED, version, self._current)
