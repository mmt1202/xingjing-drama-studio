from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Capability = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class Failure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
    message: Annotated[str, StringConstraints(min_length=1, max_length=4_000)]
    retryable: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)


class GenerationRequest(BaseModel):
    """Provider 无关且可持久化的媒体生成请求。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: Identifier
    project_id: Identifier
    media_type: MediaType
    capability: Capability
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    input_asset_ids: tuple[Identifier, ...] = ()
    requested_provider_id: Identifier | None = None
    requested_model_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_provider_selection(self) -> GenerationRequest:
        if self.requested_model_id is not None and self.requested_provider_id is None:
            raise ValueError("指定模型时必须同时指定供应商")
        return self


def compute_request_fingerprint(request: GenerationRequest) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class GenerationTask(BaseModel):
    """可由数据库或队列适配器保存的生成任务快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: Identifier
    request: GenerationRequest
    idempotency_key: IdempotencyKey
    idempotency_scope: str
    request_fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    status: TaskStatus
    attempt: Annotated[int, Field(ge=0)] = 0
    version: Annotated[int, Field(ge=1)] = 1
    resolved_provider_id: Identifier | None = None
    resolved_model_id: Identifier | None = None
    provider_job_id: Identifier | None = None
    next_attempt_at: datetime | None = None
    completed_at: datetime | None = None
    failure: Failure | None = None
    output_asset_ids: tuple[Identifier, ...] = ()
    processed_callback_event_ids: tuple[Identifier, ...] = ()
    created_at: datetime
    updated_at: datetime
    timeout_at: datetime

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        request: GenerationRequest,
        idempotency_key: str,
        created_at: datetime,
        timeout_at: datetime,
    ) -> GenerationTask:
        if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("幂等键只能包含 ASCII 字母、数字及 ._:/-，长度为 1 到 128")
        return cls(
            task_id=task_id,
            request=request,
            idempotency_key=idempotency_key,
            idempotency_scope=f"{request.workspace_id}:{idempotency_key}",
            request_fingerprint=compute_request_fingerprint(request),
            status=TaskStatus.QUEUED,
            created_at=created_at,
            updated_at=created_at,
            timeout_at=timeout_at,
        )

    @model_validator(mode="after")
    def validate_times_and_identity(self) -> GenerationTask:
        for name in ("created_at", "updated_at", "timeout_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} 必须包含时区")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if self.timeout_at <= self.created_at:
            raise ValueError("timeout_at 必须晚于 created_at")
        expected_scope = f"{self.request.workspace_id}:{self.idempotency_key}"
        if self.idempotency_scope != expected_scope:
            raise ValueError("幂等范围与工作区和幂等键不一致")
        if self.request_fingerprint != compute_request_fingerprint(self.request):
            raise ValueError("请求指纹与请求内容不一致")
        return self
