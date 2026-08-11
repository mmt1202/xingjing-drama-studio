from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from server.xingjing_generation.contracts import GenerationRequest, GenerationTask, MediaType, TaskStatus
from server.xingjing_generation.lifecycle import start_task
from server.xingjing_generation_persistence import (
    Base,
    CostEvidence,
    ImmutableAuditRecord,
    ProviderCallEvidence,
    SqlAlchemyGenerationTaskRepository,
    TaskScopeNotFound,
    VersionConflict,
)


@pytest.fixture
async def repository() -> AsyncIterator[SqlAlchemyGenerationTaskRepository]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield SqlAlchemyGenerationTaskRepository(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


def _task(
    *, task_id: str = "task-1", workspace_id: str = "workspace-a", project_id: str = "project-a"
) -> GenerationTask:
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    return GenerationTask.create(
        task_id=task_id,
        request=GenerationRequest(
            workspace_id=workspace_id,
            project_id=project_id,
            media_type=MediaType.IMAGE,
            capability="image_generate",
            prompt="a quiet city",
        ),
        idempotency_key="request-1",
        created_at=now,
        timeout_at=now + timedelta(minutes=5),
    )


@pytest.mark.uses_db
async def test_create_is_scoped_idempotently_by_workspace_and_project(
    repository: SqlAlchemyGenerationTaskRepository,
) -> None:
    task = _task()

    created = await repository.create(task)
    replay = await repository.create(task)

    assert created == task
    assert replay == task
    assert await repository.get_by_idempotency_scope(task.idempotency_scope, project_id="project-b") is None
    other_workspace = _task(task_id="task-2", workspace_id="workspace-b")
    assert await repository.create(other_workspace) == other_workspace


@pytest.mark.uses_db
async def test_save_uses_database_cas_and_does_not_overwrite_terminal_task(
    repository: SqlAlchemyGenerationTaskRepository,
) -> None:
    task = await repository.create(_task())
    running = start_task(
        task,
        provider_id="provider-1",
        model_id="model-1",
        provider_job_id="job-1",
        at=task.created_at + timedelta(seconds=1),
    )
    stored = await repository.save(running, expected_version=task.version)
    succeeded = stored.model_copy(
        update={
            "status": TaskStatus.SUCCEEDED,
            "completed_at": stored.updated_at + timedelta(seconds=1),
            "updated_at": stored.updated_at + timedelta(seconds=1),
            "version": stored.version + 1,
            "output_asset_ids": ("asset-1",),
        }
    )
    await repository.save(succeeded, expected_version=stored.version)

    with pytest.raises(VersionConflict):
        await repository.save(running, expected_version=stored.version)
    assert (await repository.get("workspace-a", "project-a", "task-1")) == succeeded

    malformed = succeeded.model_copy(update={"version": succeeded.version + 2})
    with pytest.raises(ValueError, match="任务版本必须连续递增"):
        await repository.save(malformed, expected_version=succeeded.version)


@pytest.mark.uses_db
async def test_provider_cost_and_audit_evidence_are_immutable_and_workspace_scoped(
    repository: SqlAlchemyGenerationTaskRepository,
) -> None:
    task = await repository.create(_task())
    now = task.created_at
    provider = ProviderCallEvidence(
        evidence_id="provider-call-1",
        task_id=task.task_id,
        workspace_id="workspace-a",
        project_id="project-a",
        provider_id="provider-1",
        model_id="model-1",
        provider_job_id="job-1",
        attempt=1,
        request_summary={"capability": "image_generate"},
        response_summary={"accepted": True},
        occurred_at=now,
    )
    cost = CostEvidence(
        evidence_id="cost-1",
        task_id=task.task_id,
        workspace_id="workspace-a",
        project_id="project-a",
        provider_call_evidence_id=provider.evidence_id,
        currency="CNY",
        estimated_minor=12,
        actual_minor=10,
        pricing_version="2026-07",
        recorded_at=now,
    )
    audit = ImmutableAuditRecord(
        audit_id="audit-1",
        workspace_id="workspace-a",
        project_id="project-a",
        task_id=task.task_id,
        request_id="request-1",
        actor_id="actor-1",
        action="generation.task.created",
        result="succeeded",
        before={},
        after={"status": "queued"},
        occurred_at=now,
    )

    await repository.append_provider_evidence(provider)
    await repository.append_cost_evidence(cost)
    await repository.append_audit(audit)
    await repository.append_provider_evidence(provider)

    stored_provider = await repository.list_provider_evidence("workspace-a", "project-a", task.task_id)
    stored_cost = await repository.list_cost_evidence("workspace-a", "project-a", task.task_id)
    stored_audit = await repository.query_audit("workspace-a", "project-a", request_id="request-1")
    assert stored_provider == (provider,)
    assert stored_cost == (cost,)
    assert stored_audit == (audit,)
    assert stored_provider[0].occurred_at.tzinfo == UTC
    assert stored_cost[0].recorded_at.tzinfo == UTC
    assert stored_audit[0].occurred_at.tzinfo == UTC
    assert await repository.list_provider_evidence("workspace-b", "project-a", task.task_id) == ()


@pytest.mark.uses_db
@pytest.mark.parametrize("scope", [("workspace-a", "project-b"), ("workspace-b", "project-a")])
async def test_evidence_append_rejects_missing_or_cross_scope_task_without_existence_leak(
    repository: SqlAlchemyGenerationTaskRepository, scope: tuple[str, str]
) -> None:
    task = await repository.create(_task())
    workspace_id, project_id = scope
    now = task.created_at
    evidence = (
        ProviderCallEvidence("provider-x", task.task_id, workspace_id, project_id, "p", "m", "j", 1, {}, {}, now),
        CostEvidence("cost-x", task.task_id, workspace_id, project_id, "provider-x", "CNY", 1, 1, "v1", now),
        ImmutableAuditRecord(
            "audit-x",
            workspace_id,
            project_id,
            task.task_id,
            "request-x",
            "actor-x",
            "generation.task.created",
            "succeeded",
            {},
            {},
            now,
        ),
    )

    for append, item in zip(
        (repository.append_provider_evidence, repository.append_cost_evidence, repository.append_audit),
        evidence,
        strict=True,
    ):
        with pytest.raises(TaskScopeNotFound, match="GENERATION_TASK_NOT_FOUND"):
            await append(item)
