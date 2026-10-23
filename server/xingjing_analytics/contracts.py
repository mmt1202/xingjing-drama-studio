from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

UNKNOWN = "unknown"
SCAN_LIMIT = 20_000
GROUP_LIMIT = 5_000
EXPORT_LIMIT = 1_000
EXPORT_BYTES_LIMIT = 2 * 1024 * 1024


class AnalyticsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    range: Literal["week", "month", "quarter", "all"] = "month"
    projectId: str = Field(default="", max_length=128)
    episodeId: str = Field(default="", max_length=257)
    modelId: str = Field(default="", max_length=512)
    memberId: str = Field(default="", max_length=128)
    q: str = Field(default="", max_length=160)
    groupBy: Literal["project", "episode", "model", "member"] = "project"
    offset: StrictInt = Field(default=0, ge=0, le=100_000)
    limit: StrictInt = Field(default=20, ge=1, le=50)


class AnalyticsFailure(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return utc(value)
    if isinstance(value, str):
        try:
            return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def period(key: str, now: datetime) -> datetime | None:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "week":
        return day - timedelta(days=day.weekday())
    if key == "month":
        return day.replace(day=1)
    if key == "quarter":
        return day.replace(month=((day.month - 1) // 3) * 3 + 1, day=1)
    return None


@dataclass(slots=True)
class Fact:
    kind: str
    project: str
    at: datetime
    episode: str = UNKNOWN
    model: str = UNKNOWN
    member: str = UNKNOWN
    status: str = ""
    retry: bool = False
    duration: int | None = None
    shots: frozenset[str] = field(default_factory=frozenset)
    currency: str = ""
    amount: int = 0


@dataclass(slots=True)
class Dataset:
    facts: list[Fact]
    projects: dict[str, dict[str, str]]
    episodes: dict[tuple[str, str], str]
    models: dict[str, str]
    members: dict[str, str]
    visible_projects: list[str]
    notes: set[str]
    sources: list[str]
    scope_permissions: dict[str, list[str]] = field(default_factory=dict)
    assignment_scopes: dict[str, list[str]] = field(default_factory=dict)
