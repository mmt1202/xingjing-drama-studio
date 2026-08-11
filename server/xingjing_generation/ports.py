"""真实基础设施适配器需要实现的生成领域端口。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .contracts import GenerationRequest, GenerationTask, Identifier


class ProviderSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_job_id: Identifier
    accepted_at: datetime


class GenerationTaskRepository(Protocol):
    async def get_by_idempotency_scope(self, scope: str) -> GenerationTask | None:
        """按数据库唯一键读取；实现方必须以工作区隔离查询。"""
        ...

    async def create(self, task: GenerationTask) -> GenerationTask:
        """原子写入；幂等范围冲突必须由唯一约束裁决。"""
        ...

    async def save(self, task: GenerationTask, *, expected_version: int) -> GenerationTask:
        """以 expected_version 做乐观锁更新。"""
        ...


class GenerationQueue(Protocol):
    async def enqueue(self, task_id: str, *, available_at: datetime) -> None:
        """投递已有持久化任务；队列消息只携带稳定任务 ID。"""
        ...


class ProviderGateway(Protocol):
    @property
    def provider_id(self) -> str: ...

    async def submit(
        self,
        request: GenerationRequest,
        *,
        attempt: int,
        deadline: datetime,
    ) -> ProviderSubmission: ...

    async def cancel(self, provider_job_id: str) -> None: ...
