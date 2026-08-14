from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from server.xingjing_generation import GenerationRequest, MediaType, TaskStatus
from server.xingjing_generation.ports import ProviderSubmission
from server.xingjing_generation_persistence import Base, SqlAlchemyGenerationTaskRepository, TaskScopeNotFound
from server.xingjing_generation_runtime.artifact_storage import LocalGenerationArtifactStore
from server.xingjing_generation_runtime.runtime import GenerationBillingPolicy, GenerationRuntime
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_model_control import ModelRouteDecision


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


@pytest.mark.asyncio
async def test_submit_freezes_billing_dispatches_provider_and_signed_callback_confirms_artifact(tmp_path) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SqlAlchemyGenerationTaskRepository(async_sessionmaker(engine, expire_on_commit=False))
    await repository.grant_credits(
        workspace_id="workspace-a",
        currency="CNY",
        amount_minor=1_000,
        event_id="grant-1",
        reference="test-credit",
        at=datetime.now(UTC),
    )

    class Gateway:
        provider_id = "provider-a"
        default_model_id = "image-v1"

        async def submit(self, request, *, task_id: str, attempt: int, deadline) -> ProviderSubmission:
            assert request.requested_model_id == "image-v1"
            assert attempt == 1
            assert deadline > datetime.now(UTC)
            return ProviderSubmission(provider_job_id="provider-job-1", accepted_at=datetime.now(UTC))

    class Routes:
        def __init__(self) -> None:
            self.releases: list[tuple[str, str, bool]] = []

        async def resolve(self, **_kwargs) -> ModelRouteDecision:
            return ModelRouteDecision("registry-a", "provider-a", "image-v1", "price-v1", "CNY", 100, False)

        async def release(self, *, provider_id: str, model_id: str, succeeded: bool) -> None:
            self.releases.append((provider_id, model_id, succeeded))

    routes = Routes()
    provider_root = tmp_path / "provider"
    provider_root.mkdir()
    (provider_root / "result.png").write_bytes(b"real-image-output")
    runtime = GenerationRuntime(
        repository=repository,
        session_factory=None,
        context_resolver=None,
        provider_gateway=Gateway(),  # type: ignore[arg-type]
        artifact_store=LocalGenerationArtifactStore(tmp_path / "objects"),
        provider_callback_secret="callback-secret",
        provider_project_root=str(provider_root),
        billing_policy=GenerationBillingPolicy("CNY", 100, "price-v1"),
        model_route_resolver=routes,  # type: ignore[arg-type]
    )
    context = TrustedWorkspaceContext(
        "tenant-a",
        "workspace-a",
        "actor-a",
        "request-a",
        frozenset({"generation.view", "generation.manage"}),
        "OWNER",
    )
    request = GenerationRequest(
        workspace_id="workspace-a",
        project_id="project-a",
        media_type=MediaType.IMAGE,
        capability="image_generate",
        prompt="雨夜电影画面",
    )

    started = await runtime.submit(context, "project-a", request, idempotency_key="submit-1")
    assert started.status is TaskStatus.RUNNING
    assert started.provider_job_id == "provider-job-1"
    billing = await repository.billing_for_task("workspace-a", "project-a", started.task_id)
    assert billing is not None and billing["status"] == "active"

    payload = {
        "workspaceId": "workspace-a",
        "projectId": "project-a",
        "taskId": started.task_id,
        "providerJobId": "provider-job-1",
        "eventId": "callback-1",
        "attempt": 1,
        "status": "succeeded",
        "outputs": [{"assetId": "asset-1", "sourcePath": "result.png"}],
        "billing": {"currency": "CNY", "estimatedMinor": 100, "actualMinor": 80, "pricingVersion": "price-v1"},
    }
    invalid_payload = {**payload, "eventId": "callback-without-billing"}
    invalid_payload.pop("billing")
    invalid_raw = json.dumps(invalid_payload, separators=(",", ":")).encode()
    invalid_signature = hmac.new(b"callback-secret", invalid_raw, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="GENERATION_PROVIDER_CALLBACK_BILLING_REQUIRED"):
        await runtime.apply_provider_callback(
            raw_body=invalid_raw,
            signature=invalid_signature,
            payload=invalid_payload,
        )
    assert not (tmp_path / "objects").exists()

    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"callback-secret", raw, hashlib.sha256).hexdigest()
    completed = await runtime.apply_provider_callback(raw_body=raw, signature=signature, payload=payload)

    assert completed.status is TaskStatus.SUCCEEDED
    assets = await repository.list_generated_assets("workspace-a", "project-a", started.task_id)
    assert len(assets) == 1 and assets[0].content_sha256 == hashlib.sha256(b"real-image-output").hexdigest()
    settled = await repository.billing_for_task("workspace-a", "project-a", started.task_id)
    assert settled is not None and settled["status"] == "settled" and settled["actual_minor"] == 80
    assert routes.releases == [("provider-a", "image-v1", True)]
    await engine.dispose()
