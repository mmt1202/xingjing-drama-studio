from __future__ import annotations

import pytest

from server.xingjing_generation_persistence import TaskScopeNotFound
from server.xingjing_generation_runtime.runtime import GenerationRuntime


class _Session:
    def __init__(self, tenant_id: str | None) -> None:
        self.tenant_id = tenant_id

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalar(self, _statement: object) -> str | None:
        return self.tenant_id


@pytest.mark.asyncio
async def test_provider_recovery_resolves_real_project_tenant_not_workspace_id() -> None:
    runtime = GenerationRuntime(
        repository=None,
        session_factory=lambda: _Session("tenant-a"),  # type: ignore[arg-type]
        context_resolver=None,
    )

    assert await runtime._tenant_for_project("workspace-a", "project-a") == "tenant-a"

    missing = GenerationRuntime(
        repository=None,
        session_factory=lambda: _Session(None),  # type: ignore[arg-type]
        context_resolver=None,
    )
    with pytest.raises(TaskScopeNotFound):
        await missing._tenant_for_project("workspace-a", "missing")
