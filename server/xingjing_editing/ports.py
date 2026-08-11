from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .contracts import AuditEvent, SourceMediaVersion, TimelineVersion
from .rendering import (
    FinalVideoSelection,
    FinalVideoVersion,
    RenderCompletion,
    RenderProfile,
    RenderTask,
    StoredRenderObject,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class StableIdGenerator(Protocol):
    def new_id(self, kind: str) -> str: ...


class RenderBillingPolicy(Protocol):
    currency: str
    pricing_version: str

    def estimate_minor(self, *, duration_ms: int, width: int, height: int) -> int: ...


class MediaVersionCatalog(Protocol):
    """生产实现必须按 tenant_id + workspace_id 过滤，并只返回不可变版本。"""

    async def get_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        version_id: str,
    ) -> SourceMediaVersion | None: ...

    async def list_versions(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        offset: int,
        limit: int,
    ) -> tuple[SourceMediaVersion, ...]: ...


class TimelineRepository(Protocol):
    """生产持久化边界。

    create_timeline 必须以 (tenant_id, workspace_id, idempotency_key) 建唯一约束，
    在同一事务中比较 request_fingerprint；同键异参必须抛出 IdempotencyConflict。
    """

    async def create_timeline(
        self,
        timeline: TimelineVersion,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]: ...

    async def get_current_timeline(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
    ) -> TimelineVersion | None: ...

    async def list_current_timelines(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        episode_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[TimelineVersion, ...]: ...

    async def append_timeline(
        self,
        timeline: TimelineVersion,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]:
        """原子 CAS + 幂等追加；生产实现必须保留全部旧版本。"""
        ...


class AuditRecorder(Protocol):
    """生产实现应写入追加式审计表或事务 outbox，不得仅记录进程日志。"""

    async def record(self, event: AuditEvent) -> None: ...


class AuditReader(Protocol):
    async def list_events(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        request_id: str | None,
        actor_id: str | None,
        object_id: str | None,
        action: str | None,
        offset: int,
        limit: int,
    ) -> tuple[AuditEvent, ...]: ...


class RenderRepository(Protocol):
    """渲染任务与成片版本的生产持久化边界。

    幂等范围必须以数据库唯一约束裁决；任务更新必须使用 task_revision 做 CAS。
    该端口后续的完成操作必须在同一事务内写任务、去重后的成片版本和 outbox。
    """

    async def get_timeline_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
        version_id: str,
    ) -> TimelineVersion | None: ...

    async def get_render_task_by_idempotency(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        idempotency_key: str,
    ) -> RenderTask | None: ...

    async def create_render_task(
        self,
        task: RenderTask,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[RenderTask, bool]: ...

    async def get_render_task(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        task_id: str,
    ) -> RenderTask | None: ...

    async def list_render_tasks(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        offset: int,
        limit: int,
    ) -> tuple[RenderTask, ...]: ...

    async def list_final_video_versions(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        final_video_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[FinalVideoVersion, ...]: ...

    async def get_final_video_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        version_id: str,
    ) -> FinalVideoVersion | None: ...

    async def select_final_video_version(
        self,
        selection: FinalVideoSelection,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[FinalVideoSelection, bool]: ...

    async def get_final_video_selection(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        final_video_id: str,
    ) -> FinalVideoSelection | None: ...

    async def save_render_task(self, task: RenderTask, *, expected_revision: int) -> RenderTask:
        """以 task_revision 原子 CAS；冲突必须抛出 VersionConflict。"""
        ...

    async def complete_render(
        self,
        task: RenderTask,
        version: FinalVideoVersion,
        *,
        expected_revision: int,
    ) -> RenderCompletion:
        """原子写入任务终态、去重后的成片版本与审计 outbox。"""
        ...


class RendererSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    renderer_job_id: str
    accepted_at: datetime


class RendererGateway(Protocol):
    """真实渲染基础设施端口；实现方必须以 task_id + attempt 保证提交幂等。"""

    async def submit(
        self,
        *,
        task_id: str,
        attempt: int,
        input_snapshot_sha256: str,
        timeline: TimelineVersion,
        profile: RenderProfile,
        deadline_at: datetime,
    ) -> RendererSubmission: ...

    async def cancel(self, *, renderer_job_id: str) -> None: ...


class RenderObjectStorage(Protocol):
    """真实对象存储校验端口；领域层不会凭回调字段伪造产物存在。"""

    async def stat(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        object_key: str,
    ) -> StoredRenderObject | None: ...
