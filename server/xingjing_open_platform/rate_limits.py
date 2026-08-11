from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    limit: int
    window: timedelta

    def __post_init__(self) -> None:
        if self.limit <= 0 or self.window <= timedelta(0):
            raise ValueError("rate limit and window must be positive")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_at: datetime
