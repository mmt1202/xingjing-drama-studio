from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HealthResult:
    component: str
    status: HealthStatus
    observed_at: str
    latency_ms: float | None = None
    detail: str | None = None

    @classmethod
    def healthy(cls, component: str, *, observed_at: str, latency_ms: float | None = None) -> HealthResult:
        return cls(component, HealthStatus.HEALTHY, observed_at, latency_ms)

    @classmethod
    def unhealthy(cls, component: str, *, observed_at: str, detail: str) -> HealthResult:
        return cls(component, HealthStatus.UNHEALTHY, observed_at, detail=detail)


@dataclass(frozen=True, slots=True)
class ServiceHealth:
    service: str
    version: str
    status: HealthStatus
    observed_at: str
    checks: tuple[HealthResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Probe = Callable[[], Awaitable[HealthResult]]


class HealthChecker:
    def __init__(self, *, service: str, version: str, probe_timeout_seconds: float = 5.0) -> None:
        self._service = service
        self._version = version
        self._timeout = probe_timeout_seconds
        self._probes: list[tuple[str, Probe, bool]] = []

    def register(self, component: str, probe: Probe, *, critical: bool) -> None:
        if any(existing == component for existing, _probe, _critical in self._probes):
            raise ValueError(f"duplicate health component: {component}")
        self._probes.append((component, probe, critical))

    def liveness(self, *, observed_at: str) -> ServiceHealth:
        return ServiceHealth(self._service, self._version, HealthStatus.HEALTHY, observed_at, ())

    async def readiness(self, *, observed_at: str) -> ServiceHealth:
        checks = tuple(
            await asyncio.gather(
                *(
                    self._run_probe(component, probe, observed_at=observed_at)
                    for component, probe, _critical in self._probes
                )
            )
        )
        critical_statuses = [
            check.status for check, (_component, _probe, critical) in zip(checks, self._probes, strict=True) if critical
        ]
        if any(status in {HealthStatus.UNHEALTHY, HealthStatus.UNKNOWN} for status in critical_statuses):
            status = HealthStatus.UNHEALTHY
        elif any(check.status is not HealthStatus.HEALTHY for check in checks):
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY
        return ServiceHealth(self._service, self._version, status, observed_at, checks)

    async def _run_probe(self, component: str, probe: Probe, *, observed_at: str) -> HealthResult:
        try:
            result = await asyncio.wait_for(probe(), timeout=self._timeout)
        except TimeoutError:
            return HealthResult(component, HealthStatus.UNKNOWN, observed_at, detail="probe timeout")
        except Exception as exc:
            return HealthResult(
                component, HealthStatus.UNKNOWN, observed_at, detail=f"probe failed: {type(exc).__name__}"
            )
        if result.component != component:
            return HealthResult(component, HealthStatus.UNKNOWN, observed_at, detail="probe component mismatch")
        return result
