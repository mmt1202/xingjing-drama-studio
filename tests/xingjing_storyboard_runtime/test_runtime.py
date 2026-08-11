from __future__ import annotations

import pytest
from fastapi import Request
from sqlalchemy.orm import sessionmaker

from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_storyboard.models import AssetReference
from server.xingjing_storyboard.ports import AssetReferenceFailure
from server.xingjing_storyboard.service import AccessContext
from server.xingjing_storyboard_persistence import SqlAlchemyStoryboardRepository
from server.xingjing_storyboard_runtime import (
    StoryboardRuntime,
    StoryboardRuntimeConfigurationError,
    create_production_storyboard_runtime,
)


class AssetValidator:
    def validate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        references: tuple[AssetReference, ...],
    ) -> tuple[AssetReferenceFailure, ...]:
        return ()


@pytest.mark.asyncio
async def test_runtime_uses_trusted_workspace_context_instead_of_client_identity_headers() -> None:
    trusted = TrustedWorkspaceContext(
        tenant_id="tenant-trusted",
        workspace_id="workspace-trusted",
        actor_id="actor-trusted",
        request_id="request-trusted",
        permissions=frozenset({"shot.view"}),
        role="MEMBER",
    )
    runtime = StoryboardRuntime(
        repository=SqlAlchemyStoryboardRepository(sessionmaker()),
        asset_validator=AssetValidator(),
        context_resolver=lambda _request: trusted,
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (b"x-tenant-id", b"attacker-tenant"),
                (b"x-workspace-id", b"attacker-workspace"),
                (b"x-actor-id", b"attacker-actor"),
                (b"x-permissions", b"shot.manage"),
            ],
        }
    )

    context = await runtime.access_context(request, project_id="project-1")

    assert context == AccessContext(
        tenant_id="tenant-trusted",
        workspace_id="workspace-trusted",
        project_id="project-1",
        actor_id="actor-trusted",
        request_id="request-trusted",
        permissions=frozenset({"shot.view"}),
    )


def test_production_runtime_requires_an_explicit_sync_database_url(monkeypatch) -> None:
    monkeypatch.delenv("XINGJING_STORYBOARD_DATABASE_URL", raising=False)

    try:
        create_production_storyboard_runtime(AssetValidator())
    except StoryboardRuntimeConfigurationError as error:
        assert str(error) == "XINGJING_STORYBOARD_DATABASE_URL_REQUIRED"
    else:
        raise AssertionError("production runtime must not silently select an in-memory or file repository")


def test_production_runtime_rejects_the_async_application_database_url() -> None:
    try:
        create_production_storyboard_runtime(AssetValidator(), database_url="sqlite+aiosqlite:///projects/.arcreel.db")
    except StoryboardRuntimeConfigurationError as error:
        assert str(error) == "XINGJING_STORYBOARD_DATABASE_URL_MUST_BE_SYNC"
    else:
        raise AssertionError("the synchronous storyboard repository cannot use an async SQLAlchemy URL")


def test_production_runtime_requires_a_real_asset_reference_validator(monkeypatch) -> None:
    monkeypatch.setenv("XINGJING_STORYBOARD_DATABASE_URL", "sqlite+pysqlite:///:memory:")

    try:
        create_production_storyboard_runtime(None)
    except StoryboardRuntimeConfigurationError as error:
        assert str(error) == "STORYBOARD_ASSET_VALIDATOR_REQUIRED"
    else:
        raise AssertionError("production runtime must not replace the M04 validation boundary with a mock")


def test_production_runtime_composes_sqlalchemy_repository_with_injected_asset_validator(monkeypatch) -> None:
    monkeypatch.setenv("XINGJING_STORYBOARD_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    validator = AssetValidator()

    runtime = create_production_storyboard_runtime(validator)

    assert isinstance(runtime.service._repository, SqlAlchemyStoryboardRepository)
    assert runtime.service._asset_validator is validator
    runtime.close()
