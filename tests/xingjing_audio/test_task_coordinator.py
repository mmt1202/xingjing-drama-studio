from __future__ import annotations

import pytest

from server.xingjing_audio import (
    FallbackPolicy,
    MediaTask,
    MediaTaskKind,
    MediaTaskStatus,
    TaskCoordinator,
    TaskNotFound,
    TaskRegistration,
)


class RepositoryStub:
    def __init__(self, registration: TaskRegistration) -> None:
        self.registration = registration
        self.saved: list[MediaTask] = []

    async def add_idempotent(self, task: MediaTask) -> TaskRegistration:
        return self.registration

    async def get(self, *, workspace_id: str, task_id: str) -> MediaTask | None:
        task = self.registration.task
        return task if task.workspace_id == workspace_id and task.task_id == task_id else None

    async def save(self, task: MediaTask) -> None:
        self.saved.append(task)


class DispatcherStub:
    def __init__(self) -> None:
        self.dispatched: list[MediaTask] = []

    async def dispatch(self, task: MediaTask) -> None:
        self.dispatched.append(task)


class ProviderStub:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def submit(self, task: MediaTask) -> str:
        return "remote"

    async def cancel(self, provider_job_id: str) -> None:
        self.cancelled.append(provider_job_id)


def queued_task() -> MediaTask:
    return MediaTask.queued(
        "task-1",
        "ws-1",
        "p-1",
        MediaTaskKind.TTS,
        "idem-1",
        ("subtitle:1",),
        0,
        100,
        FallbackPolicy.NONE,
    )


@pytest.mark.asyncio
async def test_new_task_is_dispatched_once_after_atomic_registration() -> None:
    task = queued_task()
    repository = RepositoryStub(TaskRegistration(task=task, created=True))
    dispatcher = DispatcherStub()

    result = await TaskCoordinator(repository, dispatcher).enqueue(task)

    assert result == task
    assert dispatcher.dispatched == [task]


@pytest.mark.asyncio
async def test_idempotent_replay_returns_existing_task_without_dispatch() -> None:
    existing = queued_task()
    repository = RepositoryStub(TaskRegistration(task=existing, created=False))
    dispatcher = DispatcherStub()

    result = await TaskCoordinator(repository, dispatcher).enqueue(queued_task())

    assert result == existing
    assert dispatcher.dispatched == []


@pytest.mark.asyncio
async def test_running_task_cancellation_reaches_provider_and_persists_terminal_state() -> None:
    running = queued_task().start("tts-provider", "remote-7")
    repository = RepositoryStub(TaskRegistration(task=running, created=False))
    provider = ProviderStub()
    coordinator = TaskCoordinator(repository, DispatcherStub(), provider=provider)

    cancelled = await coordinator.cancel(workspace_id="ws-1", task_id="task-1", reason="user")

    assert provider.cancelled == ["remote-7"]
    assert cancelled.status is MediaTaskStatus.CANCELLED
    assert repository.saved == [cancelled]


@pytest.mark.asyncio
async def test_cancellation_does_not_cross_workspace_boundary() -> None:
    repository = RepositoryStub(TaskRegistration(task=queued_task(), created=False))
    with pytest.raises(TaskNotFound):
        await TaskCoordinator(repository, DispatcherStub()).cancel(
            workspace_id="ws-other", task_id="task-1", reason="user"
        )
