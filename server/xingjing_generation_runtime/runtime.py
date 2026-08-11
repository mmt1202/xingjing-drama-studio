"""Production composition for M06 generation task reads and guarded control actions.

The runtime deliberately has no fallback queue or in-memory repository. A
provider submission adapter is a separate deployment prerequisite: accepting a
task before such an adapter exists would create a false, permanently queued
generation request.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_assets_persistence.repository import AssetRow, ReferenceRow
from server.xingjing_generation import GenerationRequest, GenerationTask, TaskStatus
from server.xingjing_generation.lifecycle import (
    CallbackDisposition,
    Failure,
    ProviderCallback,
    cancel_task,
    expire_task,
    mark_cancelled,
    start_task,
)
from server.xingjing_generation_persistence import (
    CostEvidence,
    GeneratedAsset,
    GeneratedCandidateSelection,
    GeneratedCandidateSet,
    ImmutableAuditRecord,
    ModelUsageReport,
    ProviderCallEvidence,
    SqlAlchemyGenerationTaskRepository,
    TaskScopeNotFound,
    VersionConflict,
)
from server.xingjing_generation_persistence.repository import GeneratedAssetRow
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_model_control import ModelRouteResolver
from server.xingjing_platform_persistence.persistence import ProjectRow

from .artifact_storage import GeneratedArtifactStorageError, LocalGenerationArtifactStore
from .provider_callback import GenerationProviderCallbackService, ProviderGeneratedOutput
from .provider_gateway import GenerationProviderGatewayError, HttpGenerationProviderGateway

TrustedContextResolver = Callable[[Request], Awaitable[TrustedWorkspaceContext]]
SessionFactory = async_sessionmaker[AsyncSession]
logger = logging.getLogger(__name__)


class GenerationRuntimeUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GenerationModelDefinition:
    provider_id: str
    model_id: str
    display_name: str
    version: str
    region: str
    media_types: tuple[str, ...]
    capabilities: tuple[str, ...]
    active: bool


@dataclass(frozen=True)
class GenerationModelAvailability:
    provider_id: str | None
    model_id: str | None
    submission_available: bool
    callback_available: bool
    billing_currency: str | None
    estimated_cost_minor: int | None
    pricing_version: str | None
    models: tuple[GenerationModelDefinition, ...] = ()


@dataclass(frozen=True)
class GenerationBillingPolicy:
    currency: str
    estimated_minor: int
    pricing_version: str
    max_active_tasks: int = 20


class GenerationRuntime:
    """Owns only deployed M06 dependencies; it never synthesizes a provider."""

    def __init__(
        self,
        *,
        repository: SqlAlchemyGenerationTaskRepository | None,
        session_factory: SessionFactory | None,
        context_resolver: TrustedContextResolver | None,
        provider_gateway: HttpGenerationProviderGateway | None = None,
        artifact_store: LocalGenerationArtifactStore | None = None,
        provider_callback_secret: str | None = None,
        provider_project_root: str | None = None,
        billing_policy: GenerationBillingPolicy | None = None,
        billing_callback_secret: str | None = None,
        provider_recovery_secret: str | None = None,
        model_catalog: tuple[GenerationModelDefinition, ...] = (),
        model_route_resolver: ModelRouteResolver | None = None,
        unavailable_code: str | None = None,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.repository = repository
        self._session_factory = session_factory
        self._context_resolver = context_resolver
        self._provider_gateway = provider_gateway
        self._artifact_store = artifact_store
        self._provider_callback_secret = provider_callback_secret
        self._provider_project_root = provider_project_root
        self._billing_policy = billing_policy
        self._billing_callback_secret = billing_callback_secret
        self._provider_recovery_secret = provider_recovery_secret
        self._model_catalog = model_catalog
        self._model_route_resolver = model_route_resolver
        self._timeout_sweeper: asyncio.Task[None] | None = None
        self.unavailable_code = unavailable_code
        self._close = close

    @property
    def available(self) -> bool:
        return self.unavailable_code is None

    async def context_for(self, request: Request, project_id: str, permission: str) -> TrustedWorkspaceContext:
        if self.unavailable_code or self._context_resolver is None:
            raise GenerationRuntimeUnavailable(self.unavailable_code or "GENERATION_RUNTIME_UNAVAILABLE")
        context = await self._context_resolver(request)
        if permission not in context.permissions:
            raise PermissionError(permission)
        if not await self._project_exists(context, project_id):
            raise LookupError("PROJECT_NOT_FOUND")
        return context

    async def apply_billing_grant(self, *, raw_body: bytes, signature: str) -> dict[str, object]:
        secret = self._billing_callback_secret
        if not secret:
            raise GenerationRuntimeUnavailable("GENERATION_BILLING_CALLBACK_UNAVAILABLE")
        normalized = signature.removeprefix("sha256=").strip().lower()
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not normalized or not hmac.compare_digest(normalized, expected):
            raise PermissionError("GENERATION_BILLING_CALLBACK_SIGNATURE_INVALID")
        try:
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("GENERATION_BILLING_CALLBACK_JSON_INVALID") from error
        if not isinstance(body, dict):
            raise ValueError("GENERATION_BILLING_CALLBACK_JSON_INVALID")
        workspace_id = _billing_text(body, "workspaceId")
        tenant_id = _billing_text(body, "tenantId")
        event_id = _billing_text(body, "eventId")
        reference = _billing_text(body, "reference")
        currency = _billing_text(body, "currency").upper()
        amount_minor = body.get("amountMinor")
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor <= 0:
            raise ValueError("GENERATION_BILLING_AMOUNT_INVALID")
        if self._session_factory is None:
            raise GenerationRuntimeUnavailable("GENERATION_RUNTIME_UNAVAILABLE")
        async with self._session_factory() as session:
            exists = await session.scalar(
                select(ProjectRow.id)
                .where(
                    ProjectRow.tenant_id == tenant_id,
                    ProjectRow.workspace_id == workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
                .limit(1)
            )
        if exists is None:
            raise LookupError("WORKSPACE_NOT_FOUND")
        return await self._repository_or_raise().grant_credits(
            workspace_id=workspace_id,
            currency=currency,
            amount_minor=amount_minor,
            event_id=event_id,
            reference=reference,
            at=datetime.now(UTC),
        )

    async def list_tasks(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        *,
        offset: int,
        limit: int,
        status: str | None,
        media_type: str | None,
        capability: str | None,
    ) -> tuple[tuple[GenerationTask, ...], int]:
        return await self._repository_or_raise().list(
            context.workspace_id,
            project_id,
            offset=offset,
            limit=limit,
            status=status,
            media_type=media_type,
            capability=capability,
        )

    def model_availability(self) -> GenerationModelAvailability:
        gateway = self._provider_gateway
        billing = self._billing_policy
        return GenerationModelAvailability(
            provider_id=gateway.provider_id if gateway else None,
            model_id=gateway.default_model_id if gateway else None,
            submission_available=gateway is not None and billing is not None and bool(self._model_catalog),
            callback_available=bool(
                self._provider_callback_secret and self._artifact_store and self._provider_project_root
            ),
            billing_currency=billing.currency if billing else None,
            estimated_cost_minor=billing.estimated_minor if billing else None,
            pricing_version=billing.pricing_version if billing else None,
            models=self._model_catalog,
        )

    async def get_task(self, context: TrustedWorkspaceContext, project_id: str, task_id: str) -> GenerationTask:
        task = await self._repository_or_raise().get(context.workspace_id, project_id, task_id)
        if task is None:
            raise TaskScopeNotFound()
        return task

    async def generated_assets(
        self, context: TrustedWorkspaceContext, project_id: str, task_id: str
    ) -> tuple[GeneratedAsset, ...]:
        await self.get_task(context, project_id, task_id)
        return await self._repository_or_raise().list_generated_assets(context.workspace_id, project_id, task_id)

    async def generated_artifact_file(
        self, context: TrustedWorkspaceContext, project_id: str, asset_id: str
    ) -> tuple[GeneratedAsset, str]:
        asset = await self._repository_or_raise().get_generated_asset(context.workspace_id, project_id, asset_id)
        if asset is None:
            raise TaskScopeNotFound()
        if self._artifact_store is None:
            raise GenerationRuntimeUnavailable("GENERATION_ARTIFACT_STORAGE_UNAVAILABLE")
        try:
            return asset, str(self._artifact_store.resolve_object_key(asset.object_key))
        except GeneratedArtifactStorageError as error:
            raise GenerationRuntimeUnavailable(str(error)) from error

    async def generated_candidate_selection(
        self, context: TrustedWorkspaceContext, project_id: str, task_id: str
    ) -> GeneratedCandidateSelection | None:
        await self.get_task(context, project_id, task_id)
        return await self._repository_or_raise().get_generated_candidate_selection(
            context.workspace_id, project_id, task_id
        )

    async def cost_evidence(
        self, context: TrustedWorkspaceContext, project_id: str, task_id: str
    ) -> tuple[CostEvidence, ...]:
        await self.get_task(context, project_id, task_id)
        return await self._repository_or_raise().list_cost_evidence(context.workspace_id, project_id, task_id)

    async def billing_for_task(
        self, context: TrustedWorkspaceContext, project_id: str, task_id: str
    ) -> dict[str, object] | None:
        await self.get_task(context, project_id, task_id)
        return await self._repository_or_raise().billing_for_task(context.workspace_id, project_id, task_id)

    async def task_progress(self, context: TrustedWorkspaceContext, project_id: str, task_id: str) -> dict[str, object]:
        await self.get_task(context, project_id, task_id)
        return await self._repository_or_raise().task_progress(context.workspace_id, project_id, task_id)

    async def generated_candidate_sets(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        *,
        media_type: str | None,
        capability: str | None,
        offset: int,
        limit: int,
    ) -> tuple[tuple[GeneratedCandidateSet, ...], int]:
        return await self._repository_or_raise().list_generated_candidate_sets(
            context.workspace_id,
            project_id,
            media_type=media_type,
            capability=capability,
            offset=offset,
            limit=limit,
        )

    async def model_usage_report(
        self, context: TrustedWorkspaceContext, project_id: str
    ) -> tuple[ModelUsageReport, ...]:
        return await self._repository_or_raise().model_usage_report(context.workspace_id, project_id)

    async def select_generated_candidate(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        task_id: str,
        asset_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> GeneratedCandidateSelection:
        previous = await self.generated_candidate_selection(context, project_id, task_id)
        selected = await self._repository_or_raise().select_generated_candidate(
            workspace_id=context.workspace_id,
            project_id=project_id,
            task_id=task_id,
            asset_id=asset_id,
            expected_version=expected_version,
            actor_id=context.actor_id,
            idempotency_key=idempotency_key,
            at=datetime.now(UTC),
        )
        await self._repository_or_raise().append_audit(
            ImmutableAuditRecord(
                audit_id=str(uuid4()),
                workspace_id=context.workspace_id,
                project_id=project_id,
                task_id=task_id,
                request_id=context.request_id or idempotency_key,
                actor_id=context.actor_id,
                action="generation.candidate_selected",
                result="selected",
                before=asdict(previous) if previous else {},
                after=asdict(selected),
                occurred_at=selected.updated_at,
            )
        )
        return selected

    async def cancel_task(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        task_id: str,
        *,
        expected_version: int,
    ) -> GenerationTask:
        current = await self.get_task(context, project_id, task_id)
        if current.version != expected_version:
            raise ValueError("GENERATION_TASK_VERSION_CONFLICT")
        if current.status is TaskStatus.RUNNING:
            gateway = self._provider_or_raise()
            if not current.provider_job_id:
                raise GenerationRuntimeUnavailable("GENERATION_PROVIDER_JOB_ID_MISSING")
            cancelling = cancel_task(current, at=datetime.now(UTC))
            cancelling = await self._repository_or_raise().save(cancelling, expected_version=current.version)
            await self._repository_or_raise().append_audit(
                ImmutableAuditRecord(
                    audit_id=str(uuid4()),
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    task_id=cancelling.task_id,
                    request_id=context.request_id or cancelling.task_id,
                    actor_id=context.actor_id,
                    action="generation.cancel_requested",
                    result="provider_cancelling",
                    before=current.model_dump(mode="json"),
                    after=cancelling.model_dump(mode="json"),
                    occurred_at=cancelling.updated_at,
                )
            )
            try:
                await gateway.cancel(cancelling.provider_job_id or "")
            except GenerationProviderGatewayError as error:
                raise GenerationRuntimeUnavailable(str(error)) from error
            cancelled = mark_cancelled(cancelling, at=datetime.now(UTC))
            cancelled = await self._repository_or_raise().save_with_billing_release(
                cancelled,
                expected_version=cancelling.version,
                event_id=f"cancel:{cancelled.task_id}:{cancelled.version}",
            )
            if self._model_route_resolver is not None:
                await self._model_route_resolver.release(
                    provider_id=cancelled.resolved_provider_id or gateway.provider_id,
                    model_id=cancelled.resolved_model_id or gateway.default_model_id,
                    succeeded=True,
                )
            await self._repository_or_raise().append_audit(
                ImmutableAuditRecord(
                    audit_id=str(uuid4()),
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    task_id=cancelled.task_id,
                    request_id=context.request_id or cancelled.task_id,
                    actor_id=context.actor_id,
                    action="generation.cancel_confirmed",
                    result="cancelled",
                    before=cancelling.model_dump(mode="json"),
                    after=cancelled.model_dump(mode="json"),
                    occurred_at=cancelled.updated_at,
                )
            )
            return cancelled
        if current.status not in {TaskStatus.QUEUED, TaskStatus.RETRYING}:
            raise ValueError("GENERATION_TASK_NOT_CANCELLABLE")
        updated = cancel_task(current, at=datetime.now(UTC))
        updated = await self._repository_or_raise().save_with_billing_release(
            updated,
            expected_version=current.version,
            event_id=f"cancel:{updated.task_id}:{updated.version}",
        )
        await self._repository_or_raise().append_audit(
            ImmutableAuditRecord(
                audit_id=str(uuid4()),
                workspace_id=context.workspace_id,
                project_id=project_id,
                task_id=updated.task_id,
                request_id=context.request_id or updated.task_id,
                actor_id=context.actor_id,
                action="generation.cancel_requested",
                result="cancelled",
                before=current.model_dump(mode="json"),
                after=updated.model_dump(mode="json"),
                occurred_at=updated.updated_at,
            )
        )
        return updated

    async def retry_task(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        task_id: str,
        *,
        idempotency_key: str,
    ) -> GenerationTask:
        previous = await self.get_task(context, project_id, task_id)
        if previous.status not in {TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED}:
            raise ValueError("GENERATION_TASK_NOT_RETRYABLE")
        request = GenerationRequest.model_validate(
            {
                **previous.request.model_dump(mode="json"),
                "parameters": {**previous.request.parameters, "retry_of_task_id": previous.task_id},
            }
        )
        retried = await self.submit(context, project_id, request, idempotency_key=idempotency_key)
        await self._repository_or_raise().append_audit(
            ImmutableAuditRecord(
                audit_id=str(uuid4()),
                workspace_id=context.workspace_id,
                project_id=project_id,
                task_id=retried.task_id,
                request_id=context.request_id or idempotency_key,
                actor_id=context.actor_id,
                action="generation.task_retried",
                result="submitted",
                before=previous.model_dump(mode="json"),
                after=retried.model_dump(mode="json"),
                occurred_at=datetime.now(UTC),
            )
        )
        return retried

    async def submit(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        request: GenerationRequest,
        *,
        idempotency_key: str,
    ) -> GenerationTask:
        gateway = self._provider_or_raise()
        billing = self._billing_policy_or_raise()
        if request.workspace_id != context.workspace_id or request.project_id != project_id:
            raise ValueError("GENERATION_REQUEST_SCOPE_MISMATCH")
        await self._validate_input_assets(context, project_id, request)
        if self._model_route_resolver is None:
            raise GenerationRuntimeUnavailable("MODEL_ROUTE_CONTROL_UNAVAILABLE")
        try:
            route = await self._model_route_resolver.resolve(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                capability=request.capability,
                media_type=request.media_type.value,
                requested_provider_id=request.requested_provider_id,
                requested_model_id=request.requested_model_id,
                parameters=request.parameters,
                acquire=True,
            )
        except LookupError as error:
            raise ValueError(str(error)) from error
        if route.provider_id != gateway.provider_id:
            await self._model_route_resolver.release(
                provider_id=route.provider_id, model_id=route.model_id, succeeded=False
            )
            raise GenerationRuntimeUnavailable("MODEL_PROVIDER_ADAPTER_UNAVAILABLE")
        request = request.model_copy(
            update={"requested_provider_id": route.provider_id, "requested_model_id": route.model_id}
        )
        now = datetime.now(UTC)
        candidate = GenerationTask.create(
            task_id=str(uuid4()),
            request=request,
            idempotency_key=idempotency_key,
            created_at=now,
            timeout_at=now + timedelta(hours=6),
        )
        try:
            created = await self._repository_or_raise().create_with_billing(
                candidate,
                estimated_minor=route.estimated_minor,
                currency=route.currency,
                pricing_version=route.pricing_version,
                max_active_tasks=billing.max_active_tasks,
            )
        except Exception:
            await self._model_route_resolver.release(
                provider_id=route.provider_id, model_id=route.model_id, succeeded=False
            )
            raise
        if created.task_id != candidate.task_id:
            await self._model_route_resolver.release(
                provider_id=route.provider_id, model_id=route.model_id, succeeded=True
            )
            return created
        try:
            submission = await gateway.submit(request, task_id=created.task_id, attempt=1, deadline=created.timeout_at)
        except GenerationProviderGatewayError as error:
            await self._model_route_resolver.release(
                provider_id=route.provider_id, model_id=route.model_id, succeeded=False
            )
            await self._repository_or_raise().mark_provider_submission_failed(
                context.workspace_id,
                project_id,
                created.task_id,
                error=str(error),
                at=datetime.now(UTC),
            )
            await self._repository_or_raise().append_audit(
                ImmutableAuditRecord(
                    audit_id=str(uuid4()),
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    task_id=created.task_id,
                    request_id=context.request_id or created.task_id,
                    actor_id=context.actor_id,
                    action="generation.provider_submitted",
                    result="deferred_for_recovery",
                    before=created.model_dump(mode="json"),
                    after={"error": str(error)},
                    occurred_at=datetime.now(UTC),
                )
            )
            return created
        started = start_task(
            created,
            provider_id=gateway.provider_id,
            model_id=route.model_id,
            provider_job_id=submission.provider_job_id,
            at=datetime.now(UTC),
        )
        started = await self._repository_or_raise().save_provider_submission_accepted(
            started,
            expected_version=created.version,
            provider_job_id=submission.provider_job_id,
            at=started.updated_at,
        )
        await self._repository_or_raise().append_provider_evidence(
            ProviderCallEvidence(
                evidence_id=str(uuid4()),
                task_id=started.task_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                provider_id=gateway.provider_id,
                model_id=route.model_id,
                provider_job_id=submission.provider_job_id,
                attempt=started.attempt,
                request_summary={"capability": request.capability, "media_type": request.media_type.value},
                response_summary={"accepted_at": submission.accepted_at.isoformat()},
                occurred_at=datetime.now(UTC),
            )
        )
        await self._repository_or_raise().append_audit(
            ImmutableAuditRecord(
                audit_id=str(uuid4()),
                workspace_id=context.workspace_id,
                project_id=project_id,
                task_id=started.task_id,
                request_id=context.request_id or started.task_id,
                actor_id=context.actor_id,
                action="generation.provider_submitted",
                result="accepted",
                before=created.model_dump(mode="json"),
                after=started.model_dump(mode="json"),
                occurred_at=datetime.now(UTC),
            )
        )
        return started

    def _validate_model_request(
        self, request: GenerationRequest, gateway: HttpGenerationProviderGateway
    ) -> GenerationModelDefinition:
        provider_id = request.requested_provider_id or gateway.provider_id
        model_id = request.requested_model_id or gateway.default_model_id
        match = next(
            (
                item
                for item in self._model_catalog
                if item.active and item.provider_id == provider_id and item.model_id == model_id
            ),
            None,
        )
        if match is None:
            raise ValueError("GENERATION_MODEL_NOT_AVAILABLE")
        if request.media_type.value not in match.media_types or request.capability not in match.capabilities:
            raise ValueError("GENERATION_MODEL_CAPABILITY_NOT_SUPPORTED")
        return match

    async def recover_provider_submissions(self, *, secret: str, limit: int = 100) -> dict[str, int]:
        expected = self._provider_recovery_secret
        if not expected or not hmac.compare_digest(secret, expected):
            raise PermissionError("GENERATION_PROVIDER_RECOVERY_FORBIDDEN")
        gateway = self._provider_or_raise()
        pending = await self._repository_or_raise().list_pending_provider_submissions(limit=limit)
        accepted = 0
        deferred = 0
        for queued in pending:
            route = None
            submission_persisted = False
            try:
                if self._model_route_resolver is None:
                    raise GenerationRuntimeUnavailable("MODEL_ROUTE_CONTROL_UNAVAILABLE")
                tenant_id = await self._tenant_for_project(
                    queued.request.workspace_id, queued.request.project_id
                )
                route = await self._model_route_resolver.resolve(
                    tenant_id=tenant_id,
                    workspace_id=queued.request.workspace_id,
                    capability=queued.request.capability,
                    media_type=queued.request.media_type.value,
                    requested_provider_id=queued.request.requested_provider_id,
                    requested_model_id=queued.request.requested_model_id,
                    parameters=queued.request.parameters,
                    acquire=True,
                )
                if route.provider_id != gateway.provider_id:
                    raise GenerationRuntimeUnavailable("MODEL_PROVIDER_ADAPTER_UNAVAILABLE")
                submission = await gateway.submit(
                    queued.request,
                    task_id=queued.task_id,
                    attempt=queued.attempt + 1,
                    deadline=queued.timeout_at,
                )
                started = start_task(
                    queued,
                    provider_id=gateway.provider_id,
                    model_id=queued.request.requested_model_id or gateway.default_model_id,
                    provider_job_id=submission.provider_job_id,
                    at=datetime.now(UTC),
                )
                await self._repository_or_raise().save_provider_submission_accepted(
                    started,
                    expected_version=queued.version,
                    provider_job_id=submission.provider_job_id,
                    at=started.updated_at,
                )
                submission_persisted = True
                await self._repository_or_raise().append_provider_evidence(
                    ProviderCallEvidence(
                        evidence_id=str(uuid4()),
                        task_id=started.task_id,
                        workspace_id=started.request.workspace_id,
                        project_id=started.request.project_id,
                        provider_id=gateway.provider_id,
                        model_id=started.request.requested_model_id or gateway.default_model_id,
                        provider_job_id=submission.provider_job_id,
                        attempt=started.attempt,
                        request_summary={
                            "capability": started.request.capability,
                            "media_type": started.request.media_type.value,
                        },
                        response_summary={"accepted_at": submission.accepted_at.isoformat(), "recovered": True},
                        occurred_at=datetime.now(UTC),
                    )
                )
                accepted += 1
            except GenerationProviderGatewayError as error:
                if route is not None and self._model_route_resolver is not None:
                    await self._model_route_resolver.release(
                        provider_id=route.provider_id, model_id=route.model_id, succeeded=False
                    )
                await self._repository_or_raise().mark_provider_submission_failed(
                    queued.request.workspace_id,
                    queued.request.project_id,
                    queued.task_id,
                    error=str(error),
                    at=datetime.now(UTC),
                )
                deferred += 1
            except VersionConflict:
                # Another recovery worker already advanced this task.
                if route is not None and self._model_route_resolver is not None:
                    await self._model_route_resolver.release(
                        provider_id=route.provider_id, model_id=route.model_id, succeeded=True
                    )
                continue
            except Exception:
                if not submission_persisted and route is not None and self._model_route_resolver is not None:
                    await self._model_route_resolver.release(
                        provider_id=route.provider_id, model_id=route.model_id, succeeded=False
                    )
                raise
        return {"scanned": len(pending), "accepted": accepted, "deferred": deferred}

    async def apply_provider_callback(
        self,
        *,
        raw_body: bytes,
        signature: str,
        payload: dict[str, object],
    ) -> GenerationTask:
        """Apply a signed provider event and only persist controlled artifacts."""
        secret = self._provider_callback_secret
        if not secret:
            raise GenerationRuntimeUnavailable("GENERATION_PROVIDER_CALLBACK_UNAVAILABLE")
        normalized_signature = signature.removeprefix("sha256=").strip()
        expected_signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not normalized_signature or not hmac.compare_digest(normalized_signature, expected_signature):
            raise PermissionError("GENERATION_PROVIDER_CALLBACK_SIGNATURE_INVALID")

        workspace_id = self._callback_string(payload, "workspaceId")
        project_id = self._callback_string(payload, "projectId")
        task_id = self._callback_string(payload, "taskId")
        provider_job_id = self._callback_string(payload, "providerJobId")
        event_id = self._callback_string(payload, "eventId")
        attempt = self._callback_positive_int(payload, "attempt")
        outcome = self._callback_string(payload, "status")
        received_at = datetime.now(UTC)
        current = await self._repository_or_raise().get(workspace_id, project_id, task_id)
        if current is None:
            raise TaskScopeNotFound()
        if (
            event_id in current.processed_callback_event_ids
            or current.status
            in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.TIMEOUT,
                TaskStatus.CANCELLED,
                TaskStatus.CANCELLING,
            }
            or attempt != current.attempt
            or provider_job_id != current.provider_job_id
        ):
            return current

        if outcome == "progress":
            percent = self._callback_nonnegative_int(payload, "progressPercent")
            if percent > 100:
                raise ValueError("GENERATION_PROVIDER_CALLBACK_PROGRESSPERCENT_INVALID")
            message = self._callback_string(payload, "progressMessage")
            raw_eta = payload.get("etaSeconds")
            eta_seconds = None if raw_eta is None else self._callback_nonnegative_int(payload, "etaSeconds")
            await self._repository_or_raise().record_progress(
                workspace_id=workspace_id,
                project_id=project_id,
                task_id=task_id,
                event_id=event_id,
                percent=percent,
                message=message,
                eta_seconds=eta_seconds,
                at=received_at,
            )
            return current

        outputs: tuple[ProviderGeneratedOutput, ...] = ()
        if outcome == "succeeded":
            outputs = await self._store_callback_outputs(
                payload=payload,
                workspace_id=workspace_id,
                project_id=project_id,
                task=current,
                received_at=received_at,
            )
            callback = ProviderCallback(
                provider_job_id=provider_job_id,
                attempt=attempt,
                event_id=event_id,
                succeeded=True,
                output_asset_ids=tuple(item.asset.asset_id for item in outputs),
            )
        elif outcome == "failed":
            callback = ProviderCallback(
                provider_job_id=provider_job_id,
                attempt=attempt,
                event_id=event_id,
                succeeded=False,
                failure=Failure(
                    code=self._callback_string(payload, "failureCode"),
                    message=self._callback_string(payload, "failureMessage"),
                    retryable=self._callback_bool(payload, "retryable"),
                ),
            )
        else:
            raise ValueError("GENERATION_PROVIDER_CALLBACK_STATUS_INVALID")

        provider_evidence_id = str(uuid4())
        cost = self._callback_cost(
            payload,
            task_id=task_id,
            workspace_id=workspace_id,
            project_id=project_id,
            provider_evidence_id=provider_evidence_id,
            at=received_at,
        )
        if outcome == "succeeded" and cost is None:
            raise ValueError("GENERATION_PROVIDER_CALLBACK_BILLING_REQUIRED")

        result = await GenerationProviderCallbackService(self._repository_or_raise()).apply(
            workspace_id=workspace_id,
            project_id=project_id,
            task_id=task_id,
            callback=callback,
            outputs=outputs,
            cost=cost,
            billing_event_id=event_id,
            received_at=received_at,
        )
        if result.disposition is CallbackDisposition.APPLIED:
            if self._model_route_resolver is not None:
                await self._model_route_resolver.release(
                    provider_id=current.resolved_provider_id or "provider",
                    model_id=current.resolved_model_id or "model",
                    succeeded=outcome == "succeeded",
                )
            await self._repository_or_raise().append_provider_evidence(
                ProviderCallEvidence(
                    evidence_id=provider_evidence_id,
                    task_id=task_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    provider_id=current.resolved_provider_id or "provider",
                    model_id=current.resolved_model_id or "model",
                    provider_job_id=provider_job_id,
                    attempt=attempt,
                    request_summary={"event_id": event_id},
                    response_summary={"status": outcome, "output_asset_ids": list(callback.output_asset_ids)},
                    occurred_at=received_at,
                )
            )
            await self._repository_or_raise().append_audit(
                ImmutableAuditRecord(
                    audit_id=str(uuid4()),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    task_id=task_id,
                    request_id=event_id,
                    actor_id=f"provider:{current.resolved_provider_id or 'external'}",
                    action="generation.provider_callback",
                    result=outcome,
                    before=current.model_dump(mode="json"),
                    after=result.task.model_dump(mode="json"),
                    occurred_at=received_at,
                )
            )
        return result.task

    async def start(self) -> None:
        if self.unavailable_code is None and self._timeout_sweeper is None:
            self._timeout_sweeper = asyncio.create_task(self._timeout_sweeper_loop())

    async def _timeout_sweeper_loop(self) -> None:
        while True:
            try:
                await self.expire_overdue_tasks()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - transient provider/database failures retry next sweep
                logger.exception("M06 timeout sweep failed; the next sweep will retry")
            await asyncio.sleep(60)

    async def expire_overdue_tasks(self, *, at: datetime | None = None, limit: int = 100) -> int:
        expired_at = at or datetime.now(UTC)
        tasks = await self._repository_or_raise().list_overdue_tasks(at=expired_at, limit=limit)
        completed = 0
        provider_jobs: list[str] = []
        for current in tasks:
            expired = expire_task(current, at=expired_at)
            try:
                await self._repository_or_raise().save_with_billing_release(
                    expired,
                    expected_version=current.version,
                    event_id=f"timeout:{current.task_id}:{current.version}",
                )
            except VersionConflict:
                continue
            if current.provider_job_id:
                provider_jobs.append(current.provider_job_id)
            if self._model_route_resolver is not None and current.resolved_provider_id and current.resolved_model_id:
                await self._model_route_resolver.release(
                    provider_id=current.resolved_provider_id,
                    model_id=current.resolved_model_id,
                    succeeded=False,
                )
            await self._repository_or_raise().append_audit(
                ImmutableAuditRecord(
                    audit_id=str(uuid4()),
                    workspace_id=current.request.workspace_id,
                    project_id=current.request.project_id,
                    task_id=current.task_id,
                    request_id=f"timeout:{current.task_id}:{current.version}",
                    actor_id="generation-timeout-sweeper",
                    action="generation.task_timed_out",
                    result="timeout",
                    before=current.model_dump(mode="json"),
                    after=expired.model_dump(mode="json"),
                    occurred_at=expired_at,
                )
            )
            completed += 1
        if provider_jobs and self._provider_gateway is not None:
            results = await asyncio.gather(
                *(self._provider_gateway.cancel(job_id) for job_id in provider_jobs),
                return_exceptions=True,
            )
            for job_id, result in zip(provider_jobs, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning("M06 provider cancellation failed after timeout: job_id=%s error=%s", job_id, result)
        return completed

    async def close(self) -> None:
        if self._timeout_sweeper is not None:
            self._timeout_sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self._timeout_sweeper
            self._timeout_sweeper = None
        if self._close is not None:
            close = self._close
            self._close = None
            await close()

    def _repository_or_raise(self) -> SqlAlchemyGenerationTaskRepository:
        if self.unavailable_code or self.repository is None:
            raise GenerationRuntimeUnavailable(self.unavailable_code or "GENERATION_RUNTIME_UNAVAILABLE")
        return self.repository

    def _provider_or_raise(self) -> HttpGenerationProviderGateway:
        if self._provider_gateway is None:
            raise GenerationRuntimeUnavailable("GENERATION_PROVIDER_SUBMISSION_UNAVAILABLE")
        return self._provider_gateway

    def _billing_policy_or_raise(self) -> GenerationBillingPolicy:
        if self._billing_policy is None:
            raise GenerationRuntimeUnavailable("GENERATION_BILLING_POLICY_UNAVAILABLE")
        return self._billing_policy

    async def _store_callback_outputs(
        self,
        *,
        payload: dict[str, object],
        workspace_id: str,
        project_id: str,
        task: GenerationTask,
        received_at: datetime,
    ) -> tuple[ProviderGeneratedOutput, ...]:
        if self._artifact_store is None or not self._provider_project_root:
            raise GenerationRuntimeUnavailable("GENERATION_ARTIFACT_STORAGE_UNAVAILABLE")
        raw_outputs = payload.get("outputs")
        if not isinstance(raw_outputs, list) or not raw_outputs:
            raise ValueError("GENERATION_PROVIDER_CALLBACK_OUTPUTS_REQUIRED")
        results: list[ProviderGeneratedOutput] = []
        seen_asset_ids: set[str] = set()
        for raw_output in raw_outputs:
            if not isinstance(raw_output, dict):
                raise ValueError("GENERATION_PROVIDER_CALLBACK_OUTPUT_INVALID")
            asset_id = self._callback_string(raw_output, "assetId")
            source_path = self._callback_string(raw_output, "sourcePath")
            duration_ms = (
                self._callback_positive_int(raw_output, "durationMs")
                if task.request.media_type.value == "video"
                else None
            )
            if asset_id in seen_asset_ids:
                raise ValueError("GENERATION_PROVIDER_CALLBACK_OUTPUT_DUPLICATE")
            seen_asset_ids.add(asset_id)
            try:
                stored = await self._artifact_store.import_project_file(
                    project_root=self._provider_project_root,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    task_id=task.task_id,
                    asset_id=asset_id,
                    media_type=task.request.media_type.value,
                    source_path=source_path,
                )
            except GeneratedArtifactStorageError as error:
                raise ValueError(str(error)) from error
            results.append(
                ProviderGeneratedOutput(
                    asset=GeneratedAsset(
                        asset_id=asset_id,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        task_id=task.task_id,
                        media_type=stored.media_type,
                        object_key=stored.object_key,
                        content_sha256=stored.content_sha256,
                        size_bytes=stored.size_bytes,
                        metadata={
                            "mime_type": stored.mime_type,
                            "source": "provider_callback",
                            **({"duration_ms": duration_ms} if duration_ms is not None else {}),
                        },
                        created_at=received_at,
                    )
                )
            )
        return tuple(results)

    @staticmethod
    def _callback_string(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"GENERATION_PROVIDER_CALLBACK_{key.upper()}_REQUIRED")
        return value.strip()

    @staticmethod
    def _callback_positive_int(payload: dict[str, object], key: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"GENERATION_PROVIDER_CALLBACK_{key.upper()}_INVALID")
        return value

    @staticmethod
    def _callback_nonnegative_int(payload: dict[str, object], key: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"GENERATION_PROVIDER_CALLBACK_{key.upper()}_INVALID")
        return value

    @staticmethod
    def _callback_bool(payload: dict[str, object], key: str) -> bool:
        value = payload.get(key)
        if not isinstance(value, bool):
            raise ValueError(f"GENERATION_PROVIDER_CALLBACK_{key.upper()}_REQUIRED")
        return value

    @staticmethod
    def _callback_cost(
        payload: dict[str, object],
        *,
        task_id: str,
        workspace_id: str,
        project_id: str,
        provider_evidence_id: str,
        at: datetime,
    ) -> CostEvidence | None:
        raw = payload.get("billing")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("GENERATION_PROVIDER_CALLBACK_BILLING_INVALID")
        currency = raw.get("currency")
        estimated = raw.get("estimatedMinor")
        actual = raw.get("actualMinor")
        pricing_version = raw.get("pricingVersion")
        if (
            not isinstance(currency, str)
            or not currency.strip()
            or not isinstance(pricing_version, str)
            or not pricing_version.strip()
            or not isinstance(estimated, int)
            or isinstance(estimated, bool)
            or estimated < 0
            or not isinstance(actual, int)
            or isinstance(actual, bool)
            or actual < 0
        ):
            raise ValueError("GENERATION_PROVIDER_CALLBACK_BILLING_INVALID")
        return CostEvidence(
            evidence_id=str(uuid4()),
            task_id=task_id,
            workspace_id=workspace_id,
            project_id=project_id,
            provider_call_evidence_id=provider_evidence_id,
            currency=currency.strip().upper(),
            estimated_minor=estimated,
            actual_minor=actual,
            pricing_version=pricing_version.strip(),
            recorded_at=at,
        )

    async def _project_exists(self, context: TrustedWorkspaceContext, project_id: str) -> bool:
        if self._session_factory is None:
            raise GenerationRuntimeUnavailable(self.unavailable_code or "GENERATION_RUNTIME_UNAVAILABLE")
        try:
            async with self._session_factory() as session:
                return (
                    await session.scalar(
                        select(ProjectRow.id).where(
                            ProjectRow.id == project_id,
                            ProjectRow.tenant_id == context.tenant_id,
                            ProjectRow.workspace_id == context.workspace_id,
                            ProjectRow.deleted_at.is_(None),
                        )
                    )
                ) is not None
        except SQLAlchemyError as error:
            raise GenerationRuntimeUnavailable("GENERATION_RUNTIME_UNAVAILABLE") from error

    async def _tenant_for_project(self, workspace_id: str, project_id: str) -> str:
        if self._session_factory is None:
            raise GenerationRuntimeUnavailable(self.unavailable_code or "GENERATION_RUNTIME_UNAVAILABLE")
        try:
            async with self._session_factory() as session:
                tenant_id = await session.scalar(
                    select(ProjectRow.tenant_id).where(
                        ProjectRow.id == project_id,
                        ProjectRow.workspace_id == workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                )
        except SQLAlchemyError as error:
            raise GenerationRuntimeUnavailable("GENERATION_RUNTIME_UNAVAILABLE") from error
        if tenant_id is None:
            raise TaskScopeNotFound()
        return str(tenant_id)

    async def _validate_input_assets(
        self, context: TrustedWorkspaceContext, project_id: str, request: GenerationRequest
    ) -> None:
        if not request.input_asset_ids:
            return
        if len(set(request.input_asset_ids)) != len(request.input_asset_ids):
            raise ValueError("GENERATION_INPUT_ASSET_DUPLICATE")
        if self._session_factory is None:
            raise GenerationRuntimeUnavailable(self.unavailable_code or "GENERATION_RUNTIME_UNAVAILABLE")
        references = select(ReferenceRow.asset_id).where(
            ReferenceRow.tenant_id == context.tenant_id,
            ReferenceRow.workspace_id == context.workspace_id,
            ReferenceRow.project_id == project_id,
        )
        try:
            async with self._session_factory() as session:
                catalog_ids = set(
                    (
                        await session.scalars(
                            select(AssetRow.asset_id).where(
                                AssetRow.tenant_id == context.tenant_id,
                                AssetRow.workspace_id == context.workspace_id,
                                AssetRow.asset_id.in_(request.input_asset_ids),
                                or_(AssetRow.owner_project_id == project_id, AssetRow.asset_id.in_(references)),
                            )
                        )
                    ).all()
                )
                generated_ids = set(
                    (
                        await session.scalars(
                            select(GeneratedAssetRow.asset_id).where(
                                GeneratedAssetRow.workspace_id == context.workspace_id,
                                GeneratedAssetRow.project_id == project_id,
                                GeneratedAssetRow.asset_id.in_(request.input_asset_ids),
                            )
                        )
                    ).all()
                )
        except SQLAlchemyError as error:
            raise GenerationRuntimeUnavailable("GENERATION_RUNTIME_UNAVAILABLE") from error
        if set(request.input_asset_ids) != catalog_ids | generated_ids:
            raise ValueError("GENERATION_INPUT_ASSET_NOT_ACCESSIBLE")


def create_production_generation_runtime(
    *,
    database_url: str | None = None,
    context_resolver: TrustedContextResolver | None = None,
) -> GenerationRuntime:
    """Create the M06 read/control composition over already-migrated tables."""
    url = (database_url or os.environ.get("XINGJING_GENERATION_DATABASE_URL", "")).strip()
    if not url:
        return _unavailable_runtime("XINGJING_GENERATION_DATABASE_URL_REQUIRED")
    if "+asyncpg" not in url and "+aiosqlite" not in url:
        return _unavailable_runtime("XINGJING_GENERATION_DATABASE_URL_MUST_BE_ASYNC")
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
    except (SQLAlchemyError, ValueError, ModuleNotFoundError):
        return _unavailable_runtime("GENERATION_RUNTIME_DEPENDENCY_UNAVAILABLE")
    factory: SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    provider_gateway = _provider_from_environment()
    artifact_root = os.environ.get("XINGJING_M06_ARTIFACT_ROOT", "").strip()
    provider_project_root = os.environ.get("XINGJING_M06_PROVIDER_PROJECT_ROOT", "").strip()
    callback_secret = os.environ.get("XINGJING_M06_PROVIDER_CALLBACK_SECRET", "").strip()
    billing_callback_secret = os.environ.get("XINGJING_M06_BILLING_CALLBACK_SECRET", "").strip()
    provider_recovery_secret = os.environ.get("XINGJING_M06_PROVIDER_RECOVERY_SECRET", "").strip()
    if callback_secret and len(callback_secret) < 32:
        return _unavailable_runtime("XINGJING_M06_PROVIDER_CALLBACK_SECRET_MINIMUM_32_REQUIRED")
    if billing_callback_secret and len(billing_callback_secret) < 32:
        return _unavailable_runtime("XINGJING_M06_BILLING_CALLBACK_SECRET_MINIMUM_32_REQUIRED")
    if provider_recovery_secret and len(provider_recovery_secret) < 32:
        return _unavailable_runtime("XINGJING_M06_PROVIDER_RECOVERY_SECRET_MINIMUM_32_REQUIRED")
    billing_policy = _billing_policy_from_environment()
    try:
        model_catalog = _model_catalog_from_environment()
    except ValueError as error:
        return _unavailable_runtime(str(error))
    return GenerationRuntime(
        repository=SqlAlchemyGenerationTaskRepository(factory),
        session_factory=factory,
        context_resolver=context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        provider_gateway=provider_gateway,
        artifact_store=LocalGenerationArtifactStore(artifact_root) if artifact_root else None,
        provider_callback_secret=callback_secret or None,
        provider_project_root=provider_project_root or None,
        billing_policy=billing_policy,
        billing_callback_secret=billing_callback_secret or None,
        provider_recovery_secret=provider_recovery_secret or None,
        model_catalog=model_catalog,
        model_route_resolver=ModelRouteResolver(factory),
        close=engine.dispose,
    )


def _unavailable_runtime(code: str) -> GenerationRuntime:
    return GenerationRuntime(
        repository=None,
        session_factory=None,
        context_resolver=None,
        unavailable_code=code,
    )


def _provider_from_environment() -> HttpGenerationProviderGateway | None:
    values = {
        "provider_id": os.environ.get("XINGJING_M06_PROVIDER_ID", "").strip(),
        "default_model_id": os.environ.get("XINGJING_M06_PROVIDER_MODEL_ID", "").strip(),
        "submit_url": os.environ.get("XINGJING_M06_PROVIDER_SUBMIT_URL", "").strip(),
        "cancel_url_template": os.environ.get("XINGJING_M06_PROVIDER_CANCEL_URL", "").strip(),
        "bearer_token": os.environ.get("XINGJING_M06_PROVIDER_TOKEN", "").strip(),
    }
    if not any(values.values()):
        return None
    try:
        return HttpGenerationProviderGateway(**values)
    except ValueError:
        # A partial deployment must keep the application available and fail
        # closed at generation submission instead of breaking application boot.
        return None


def _billing_policy_from_environment() -> GenerationBillingPolicy | None:
    raw_amount = os.environ.get("XINGJING_M06_ESTIMATED_COST_MINOR", "").strip()
    currency = os.environ.get("XINGJING_M06_BILLING_CURRENCY", "").strip().upper()
    pricing_version = os.environ.get("XINGJING_M06_PRICING_VERSION", "").strip()
    raw_max_active = os.environ.get("XINGJING_M06_MAX_ACTIVE_TASKS", "").strip()
    if not any((raw_amount, currency, pricing_version, raw_max_active)):
        return None
    if (
        not raw_amount.isdigit()
        or int(raw_amount) < 1
        or len(currency) != 3
        or not pricing_version
        or not raw_max_active.isdigit()
        or int(raw_max_active) < 1
    ):
        return None
    return GenerationBillingPolicy(
        currency=currency,
        estimated_minor=int(raw_amount),
        pricing_version=pricing_version,
        max_active_tasks=int(raw_max_active),
    )


def _model_catalog_from_environment() -> tuple[GenerationModelDefinition, ...]:
    raw = os.environ.get("XINGJING_M06_MODEL_CATALOG_JSON", "").strip()
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID") from error
    if not isinstance(payload, list) or not payload:
        raise ValueError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
    result: list[GenerationModelDefinition] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
        media_types = item.get("mediaTypes")
        capabilities = item.get("capabilities")
        if not isinstance(media_types, list) or not isinstance(capabilities, list):
            raise ValueError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
        if not all(isinstance(value, str) for value in (*media_types, *capabilities)):
            raise ValueError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
        try:
            definition = GenerationModelDefinition(
                provider_id=str(item["providerId"]).strip(),
                model_id=str(item["modelId"]).strip(),
                display_name=str(item["displayName"]).strip(),
                version=str(item["version"]).strip(),
                region=str(item["region"]).strip(),
                media_types=tuple(value.strip() for value in media_types),
                capabilities=tuple(value.strip() for value in capabilities),
                active=bool(item.get("active", True)),
            )
        except (KeyError, TypeError) as error:
            raise ValueError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID") from error
        if (
            not all(
                (
                    definition.provider_id,
                    definition.model_id,
                    definition.display_name,
                    definition.version,
                    definition.region,
                )
            )
            or not definition.media_types
            or not definition.capabilities
            or not set(definition.media_types) <= {"image", "video"}
        ):
            raise ValueError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
        result.append(definition)
    if len({(item.provider_id, item.model_id) for item in result}) != len(result):
        raise ValueError("XINGJING_M06_MODEL_CATALOG_DUPLICATE")
    return tuple(result)


def _billing_text(body: dict[str, object], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"GENERATION_BILLING_{field.upper()}_INVALID")
    return value.strip()
