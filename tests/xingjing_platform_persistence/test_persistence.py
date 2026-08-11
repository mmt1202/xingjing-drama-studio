from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.xingjing_platform_persistence import (
    Base,
    OptimisticConflict,
    PersistenceUnitOfWork,
    Scope,
    build_claim_tasks_statement,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"autocommit": False})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.uses_db
async def test_project_is_idempotent_and_invisible_outside_scope(session_factory):
    owner = Scope("tenant-a", "workspace-a")
    outsider = Scope("tenant-b", "workspace-b")

    async with PersistenceUnitOfWork(session_factory, owner) as uow:
        first = await uow.projects.create("request-1", "同名项目")
        repeated = await uow.projects.create("request-1", "被忽略的名称")
        assert repeated.id == first.id
        await uow.commit()

    async with PersistenceUnitOfWork(session_factory, outsider) as uow:
        assert await uow.projects.get(first.id) is None
        page = await uow.projects.list_page(limit=20)
        assert page.items == ()


@pytest.mark.uses_db
async def test_optimistic_update_rejects_stale_version(session_factory):
    scope = Scope("tenant-a", "workspace-a")
    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        project = await uow.projects.create("request-1", "初版")
        await uow.commit()

    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        updated = await uow.projects.rename(project.id, "第二版", expected_version=1)
        assert updated.version == 2
        await uow.commit()

    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        with pytest.raises(OptimisticConflict):
            await uow.projects.rename(project.id, "陈旧写入", expected_version=1)


@pytest.mark.uses_db
async def test_soft_delete_restore_and_stable_cursor(session_factory):
    scope = Scope("tenant-a", "workspace-a")
    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        projects = [await uow.projects.create(f"request-{index}", f"项目 {index}") for index in range(3)]
        await uow.commit()

    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        first_page = await uow.projects.list_page(limit=2)
        second_page = await uow.projects.list_page(limit=2, cursor=first_page.next_cursor)
        assert len(first_page.items) == 2
        assert [item.id for item in (*first_page.items, *second_page.items)] == [item.id for item in projects]
        await uow.projects.soft_delete(projects[0].id, expected_version=1)
        assert await uow.projects.get(projects[0].id) is None
        restored = await uow.projects.restore(projects[0].id, expected_version=2)
        assert restored.deleted_at is None
        await uow.commit()


@pytest.mark.uses_db
async def test_rollback_removes_domain_change_audit_and_outbox(session_factory):
    scope = Scope("tenant-a", "workspace-a")
    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        await uow.projects.create("request-1", "会回滚")
        await uow.audit.append("user-1", "project.created", "project", "unknown")
        await uow.outbox.add("project.created", "project", "unknown", {"name": "会回滚"})
        await uow.rollback()

    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        assert (await uow.projects.list_page(limit=10)).items == ()
        assert await uow.audit.list_recent(limit=10) == ()
        assert await uow.outbox.list_pending(limit=10) == ()


@pytest.mark.uses_db
async def test_outbox_is_atomic_and_inbox_claim_is_idempotent(session_factory):
    scope = Scope("tenant-a", "workspace-a")
    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        project = await uow.projects.create("request-1", "已提交")
        event = await uow.outbox.add("project.created", "project", project.id, {"name": project.name})
        assert await uow.inbox.claim("consumer-a", "message-1") is True
        assert await uow.inbox.claim("consumer-a", "message-1") is False
        await uow.commit()

    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        pending = await uow.outbox.list_pending(limit=10)
        assert [item.id for item in pending] == [event.id]


@pytest.mark.uses_db
async def test_task_claim_and_failure_retry_metadata(session_factory):
    scope = Scope("tenant-a", "workspace-a")
    now = datetime.now(UTC)
    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        task = await uow.tasks.create("request-1", "video.generate", {"shot_id": "shot-1"}, max_attempts=3)
        await uow.commit()

    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        claimed = await uow.tasks.claim("worker-1", now=now, lease_for=timedelta(seconds=30), limit=1)
        assert [item.id for item in claimed] == [task.id]
        assert claimed[0].attempt_count == 1
        await uow.tasks.record_failure(task.id, "provider_timeout", "供应商超时", retry_at=now + timedelta(minutes=1))
        await uow.commit()

    async with PersistenceUnitOfWork(session_factory, scope) as uow:
        failed = await uow.tasks.get(task.id)
        assert failed is not None
        assert failed.status == "retrying"
        assert failed.last_error_code == "provider_timeout"
        assert failed.next_attempt_at == now + timedelta(minutes=1)


def test_postgresql_claim_uses_skip_locked_and_scope_predicates():
    sql = str(
        build_claim_tasks_statement(Scope("tenant-a", "workspace-a"), datetime.now(UTC), 5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "tenant_id = 'tenant-a'" in sql
    assert "workspace_id = 'workspace-a'" in sql
