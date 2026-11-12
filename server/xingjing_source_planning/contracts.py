"""Bounded commands for source planning; no client-supplied project identity."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class CreatePlan(Contract):
    source_id: str = Field(alias="sourceId", min_length=1, max_length=1024)
    source_digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] = Field(alias="sourceDigest")
    episode_revision: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] = Field(alias="episodeRevision")
    count: int = Field(ge=1, le=200)
    chapter_ids: list[str] | None = Field(default=None, alias="chapterIds", min_length=1, max_length=4096)


class Candidate(Contract):
    id: str = Field(min_length=1, max_length=36)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    target_duration: int = Field(alias="targetDuration", ge=1, le=3600)


class ConfirmPlan(Contract):
    candidates: list[Candidate] = Field(min_length=1, max_length=200)
