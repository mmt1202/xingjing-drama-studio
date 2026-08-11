"""Reliable M06-to-legacy-queue dispatch adapter.

The legacy worker remains the actual model execution engine for now.  This
adapter gives it a stable, recoverable relationship with the M06 task record
without pretending that a local queue ID is an external provider job ID.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.generation_queue import GenerationQueue as LegacyGenerationQueue
from server.xingjing_generation.contracts import Failure, GenerationRequest, GenerationTask, TaskStatus
from server.xingjing_generation.lifecycle import cancel_task, fail_task, mark_cancelled
from server.xingjing_generation_persistence import LegacyQueueDispatch, SqlAlchemyGenerationTaskRepository
from server.xingjing_platform_persistence.persistence import ProjectRow

AsyncSessionFactory = async_sessionmaker[AsyncSession]
LegacyQueueFactory = Callable[[], LegacyGenerationQueue]


class ProjectScopeNotFound(LookupError):
    pass


class UnsupportedLegacyGenerationCapability(ValueError):
    """The old worker cannot safely execute the requested M06 capability."""

    pass


class SqlAlchemyProjectNameResolver:
    """Maps a trusted S03 project ID to the legacy worker's project name."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def resolve(self, *, tenant_id: str, workspace_id: str, project_id: str) -> str:
        async with self._session_factory() as session:
            name = (
                await session.execute(
                    select(ProjectRow.name).where(
                        ProjectRow.id == project_id,
                        ProjectRow.tenant_id == tenant_id,
                        ProjectRow.workspace_id == workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
        if not isinstance(name, str) or not name.strip():
            raise ProjectScopeNotFound("PROJECT_NOT_FOUND")
        return name


class LegacyQueueDispatcher:
    """Compatibility boundary for the old queue and M06 task state.

    The current old worker has no generic M06 executor.  It therefore rejects
    an incompatible request before the old queue accepts it.  A later provider
    adapter may reuse the durable dispatch/reconciliation records, but must not
    infer compatibility merely from a media type.
    """

    def __init__(
        self,
        repository: SqlAlchemyGenerationTaskRepository,
        project_names: SqlAlchemyProjectNameResolver,
        legacy_queue_factory: LegacyQueueFactory,
    ) -> None:
        self._repository = repository
        self._project_names = project_names
        self._legacy_queue_factory = legacy_queue_factory

    async def dispatch(self, task: GenerationTask, *, tenant_id: str) -> LegacyQueueDispatch:
        request = task.request
        try:
            task_type, payload = _legacy_task_contract(request, task_id=task.task_id)
        except UnsupportedLegacyGenerationCapability as error:
            await self._fail_unsupported_capability(task, error)
            raise
        project_name = await self._project_names.resolve(
            tenant_id=tenant_id,
            workspace_id=request.workspace_id,
            project_id=request.project_id,
        )
        now = datetime.now(UTC)
        link = await self._repository.create_legacy_dispatch(
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            task_id=task.task_id,
            project_name=project_name,
            created_at=now,
        )
        if link.legacy_task_id is not None:
            return link

        queue = self._legacy_queue_factory()
        try:
            existing = await queue.find_external_resource_task(
                project_name=project_name,
                task_type=task_type,
                resource_id=task.task_id,
                source="xingjing_m06",
            )
            if existing is None:
                existing = await queue.enqueue_task(
                    project_name=project_name,
                    task_type=task_type,
                    media_type=request.media_type.value,
                    resource_id=task.task_id,
                    payload=payload,
                    source="xingjing_m06",
                    provider_id=request.requested_provider_id,
                )
            legacy_task_id = existing.get("task_id")
            if not isinstance(legacy_task_id, str) or not legacy_task_id:
                raise RuntimeError("LEGACY_QUEUE_INVALID_RESPONSE")
            return await self._repository.mark_legacy_dispatch_enqueued(
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                task_id=task.task_id,
                legacy_task_id=legacy_task_id,
                at=datetime.now(UTC),
            )
        except Exception as error:
            await self._repository.record_legacy_dispatch_failure(
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                task_id=task.task_id,
                error=type(error).__name__,
                at=datetime.now(UTC),
            )
            raise

    async def _fail_unsupported_capability(
        self, task: GenerationTask, error: UnsupportedLegacyGenerationCapability
    ) -> None:
        """Do not leave a task queued when no real legacy executor exists for it."""
        if task.status not in {TaskStatus.QUEUED, TaskStatus.RETRYING}:
            return
        try:
            await self._repository.save(
                fail_task(
                    task,
                    failure=Failure(
                        code="LEGACY_CAPABILITY_UNSUPPORTED",
                        message=str(error),
                        retryable=False,
                        details={"capability": task.request.capability, "mediaType": task.request.media_type.value},
                    ),
                    at=datetime.now(UTC),
                ),
                expected_version=task.version,
            )
        except ValueError:
            # A concurrent cancel/retry/timeout is authoritative; never overwrite it.
            return


def _legacy_task_contract(request: GenerationRequest, *, task_id: str) -> tuple[str, dict[str, object]]:
    """Reject the incompatible old queue before it can accept a false job.

    Old task types such as ``storyboard`` and ``video`` are not generic media
    operations.  Each expects a legacy scene/asset identifier and mutates the
    old project JSON.  An M06 task ID is neither of those things, so mapping a
    request merely by ``media_type`` would either fail later in the worker or,
    worse, write a result to an unrelated legacy object.  A dedicated M06
    provider executor and generated-asset adapter are therefore prerequisites
    for dispatch.  Keep this failure explicit until those two pieces exist.
    """
    raise UnsupportedLegacyGenerationCapability(
        "旧生成 Worker 没有可安全执行的通用 M06 任务入口；"
        f"能力 {request.capability!r}（{request.media_type.value}）尚未接入正式 Provider 适配器"
    )


class LegacyQueueReconciler:
    """Observe legacy terminal states without fabricating generated assets.

    A successful legacy worker result is stored only as a bridge observation.
    It cannot mark an M06 task successful until a later adapter has persisted
    outputs under stable, scoped asset IDs and verified their metadata.
    """

    def __init__(self, repository: SqlAlchemyGenerationTaskRepository, legacy_queue_factory: LegacyQueueFactory) -> None:
        self._repository = repository
        self._legacy_queue_factory = legacy_queue_factory

    async def reconcile(self, *, limit: int = 100) -> tuple[LegacyQueueDispatch, ...]:
        links = await self._repository.list_legacy_dispatches_for_reconciliation(limit=limit)
        updated: list[LegacyQueueDispatch] = []
        queue = self._legacy_queue_factory()
        for link in links:
            if link.legacy_task_id is None:
                continue
            legacy = await queue.get_task(link.legacy_task_id)
            if legacy is None:
                updated.append(
                    await self._repository.record_legacy_dispatch_failure(
                        workspace_id=link.workspace_id,
                        project_id=link.project_id,
                        task_id=link.task_id,
                        error="LEGACY_QUEUE_TASK_NOT_FOUND",
                        at=datetime.now(UTC),
                    )
                )
                continue
            status = legacy.get("status")
            if not isinstance(status, str) or not status:
                continue
            observed = await self._repository.update_legacy_observation(
                workspace_id=link.workspace_id,
                project_id=link.project_id,
                task_id=link.task_id,
                legacy_status=status,
                at=datetime.now(UTC),
            )
            updated.append(observed)
            task = await self._repository.get(link.workspace_id, link.project_id, link.task_id)
            if task is None or task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED}:
                continue
            now = datetime.now(UTC)
            if status == "cancelled":
                try:
                    transitioned = mark_cancelled(task, at=now) if task.status is TaskStatus.CANCELLING else cancel_task(task, at=now)
                    await self._repository.save(transitioned, expected_version=task.version)
                except ValueError:
                    continue
            elif status == "failed":
                message = legacy.get("error_message")
                await self._repository.save(
                    fail_task(
                        task,
                        failure=Failure(
                            code="LEGACY_QUEUE_FAILED",
                            message=message if isinstance(message, str) and message else "旧生成队列任务失败",
                            retryable=False,
                            details={"legacyTaskId": link.legacy_task_id},
                        ),
                        at=now,
                    ),
                    expected_version=task.version,
                )
        return tuple(updated)
