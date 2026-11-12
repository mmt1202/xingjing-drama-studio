"""Database-backed native image worker; uncertain submissions are never sent again."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage
from server.xingjing_generation import GenerationRequest, GenerationTask, TaskStatus
from server.xingjing_generation.contracts import Failure
from server.xingjing_generation.lifecycle import fail_task
from server.xingjing_generation_persistence import (
    CostEvidence,
    GeneratedAsset,
    ImmutableAuditRecord,
    ProviderCallEvidence,
)
from server.xingjing_generation_persistence.repository import AuditRow as GenerationAuditRow
from server.xingjing_generation_persistence.repository import ProviderSubmissionOutboxRow, TaskRow

from ..input_assets import read_verified_input, validate_input_media
from .credentials import ExactImageCredentialResolver, NativeImageConfigurationError
from .models import NativeImageExecutionRow

if TYPE_CHECKING:
    from server.xingjing_generation_persistence import SqlAlchemyGenerationTaskRepository
    from server.xingjing_model_control import ModelRouteDecision, ModelRouteResolver

    from ..artifact_storage import LocalGenerationArtifactStore

_CAPABILITIES = {"image_generation", "image_edit", "reference_fusion", "character_turnaround", "character_expressions"}
_PARAMETERS = {
    "aspect_ratio",
    "image_size",
    "negative_prompt",
    "storyboard_id",
    "storyboard_version",
    "shot_id",
    "prompt_template_id",
    "prompt_model_adapter_version",
    "duration_ms",
    "asset_id",
    "source_version_id",
    "source_content_sha256",
    "asset_operation",
    "cover_plan_id",
    "retry_of_task_id",
}


def _now() -> datetime:
    return datetime.now(UTC)


class NativeImageExecutor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        repository: SqlAlchemyGenerationTaskRepository,
        credentials: ExactImageCredentialResolver,
        store: LocalGenerationArtifactStore,
        root: Path,
        routes: ModelRouteResolver,
        enabled: bool = True,
    ) -> None:
        self.sessions, self.repository, self.credentials = sessions, repository, credentials
        self.store, self.root, self.routes = store, root.resolve(), routes
        self.enabled = enabled
        self._worker: asyncio.Task[None] | None = None

    @staticmethod
    def supports(route: ModelRouteDecision) -> bool:
        return route.provider_id == "openai" and bool(
            route.credential_ref and route.credential_ref.startswith("secret://provider-credential/")
        )

    async def ready_catalog(
        self, workspace_id: str, records: list[dict[str, object]]
    ) -> tuple[set[str], tuple[str, ...]]:
        from server.xingjing_model_control.management import current_price
        from server.xingjing_model_control.runtime import ModelDefinitionRow

        ready: set[str] = set()
        errors: set[str] = set()
        if not self.enabled:
            return ready, ()
        async with self.sessions() as session:
            for record in records:
                model = await session.get(ModelDefinitionRow, str(record["registryKey"]))
                if (
                    model is None
                    or model.provider_id != "openai"
                    or not (model.credential_ref or "").startswith("secret://provider-credential/")
                ):
                    continue
                price = await current_price(session, model.registry_key)
                if price is None or price.successful_output_minor is None:
                    errors.add("GENERATION_NATIVE_SUCCESS_PRICE_REQUIRED")
                    continue
                try:
                    backend, _ = await self.credentials.backend(
                        workspace_id, model.provider_id, model.provider_model_name, model.credential_ref or ""
                    )
                    await backend.close()
                    ready.add(model.registry_key)
                except NativeImageConfigurationError as error:
                    errors.add(str(error))
        return ready, tuple(sorted(errors))

    async def admission(self, context: Any, request: GenerationRequest, route: ModelRouteDecision):
        if request.media_type.value != "image" or request.capability not in _CAPABILITIES:
            raise NativeImageConfigurationError("GENERATION_NATIVE_MODEL_UNSUPPORTED")
        if route.successful_output_minor is None or not 0 <= route.successful_output_minor <= route.estimated_minor:
            raise NativeImageConfigurationError("GENERATION_NATIVE_SUCCESS_PRICE_REQUIRED")
        if set(request.parameters) - _PARAMETERS:
            raise NativeImageConfigurationError("GENERATION_NATIVE_PARAMETERS_UNSUPPORTED")
        validate_input_media(request)
        if len(request.resolved_input_assets) > 16:
            raise NativeImageConfigurationError("GENERATION_INPUT_COUNT_INVALID")
        backend, revision = await self.credentials.backend(
            context.workspace_id, route.provider_id, route.model_id, route.credential_ref or ""
        )
        try:
            needed = ImageCapability.IMAGE_TO_IMAGE if request.input_asset_ids else ImageCapability.TEXT_TO_IMAGE
            if needed not in backend.capabilities:
                raise NativeImageConfigurationError("GENERATION_NATIVE_MODEL_UNSUPPORTED")
            # Resolve parameters before freezing funds; the worker repeats this validation.
            self.image_request(request, self.root / "validation.png", [])
        finally:
            await backend.close()
        execution_id = str(uuid4())

        async def prepare(session: AsyncSession, task: GenerationTask) -> None:
            session.add(
                NativeImageExecutionRow(
                    execution_id=execution_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=task.request.project_id,
                    task_id=task.task_id,
                    registry_key=route.registry_key,
                    provider_id=route.provider_id,
                    model_id=route.model_id,
                    credential_ref=route.credential_ref,
                    credential_revision=revision,
                    pricing_version=route.pricing_version,
                    currency=route.currency,
                    estimated_minor=route.estimated_minor,
                    successful_output_minor=route.successful_output_minor,
                    request_fingerprint=task.request_fingerprint,
                    status="queued",
                    created_at=task.created_at,
                    updated_at=task.created_at,
                )
            )
            await session.execute(
                update(ProviderSubmissionOutboxRow)
                .where(
                    ProviderSubmissionOutboxRow.workspace_id == task.request.workspace_id,
                    ProviderSubmissionOutboxRow.project_id == task.request.project_id,
                    ProviderSubmissionOutboxRow.task_id == task.task_id,
                )
                .values(status="accepted", updated_at=task.created_at)
            )
            audit = ImmutableAuditRecord(
                audit_id=f"native-admitted:{execution_id}",
                workspace_id=context.workspace_id,
                project_id=task.request.project_id,
                task_id=task.task_id,
                request_id=context.request_id or execution_id,
                actor_id=context.actor_id,
                action="generation.native_queued",
                result="queued",
                before={},
                after={
                    "execution_id": execution_id,
                    "request_fingerprint": task.request_fingerprint,
                    "registry_key": route.registry_key,
                    "pricing_version": route.pricing_version,
                    "currency": route.currency,
                    "successful_output_minor": route.successful_output_minor,
                },
                occurred_at=task.created_at,
            )
            session.add(
                GenerationAuditRow(
                    audit_id=audit.audit_id,
                    workspace_id=audit.workspace_id,
                    project_id=audit.project_id,
                    task_id=audit.task_id,
                    request_id=audit.request_id,
                    payload={key: value for key, value in audit.__dict__.items() if key != "occurred_at"},
                    occurred_at=audit.occurred_at,
                )
            )

        return prepare

    async def _release_success(self, row: NativeImageExecutionRow) -> None:
        from server.xingjing_model_control.runtime import ModelCallEvidenceRow

        data = row.result or {}
        await self.routes.release_once(
            registry_key=row.registry_key,
            release_key=row.execution_id,
            succeeded=True,
            evidence=ModelCallEvidenceRow(
                id=row.execution_id,
                tenant_id=row.tenant_id,
                workspace_id=row.workspace_id,
                task_id=row.task_id,
                registry_key=row.registry_key,
                provider_job_id=None,
                request_digest=row.request_fingerprint,
                response_digest=data.get("sha256"),
                cost_minor=row.successful_output_minor,
                pricing_version=row.pricing_version,
                outcome="succeeded",
                raw_evidence_ref=f"native:{row.execution_id}",
            ),
        )

    @staticmethod
    def image_request(request: GenerationRequest, output: Path, refs: list[ReferenceImage]) -> ImageGenerationRequest:
        from lib.image_backends.openai import _resolve_openai_params

        ratio, size = request.parameters.get("aspect_ratio", "9:16"), request.parameters.get("image_size")
        if not isinstance(ratio, str) or (size is not None and not isinstance(size, str)):
            raise NativeImageConfigurationError("GENERATION_NATIVE_PARAMETERS_UNSUPPORTED")
        try:
            _resolve_openai_params(size, ratio)
        except (ValueError, KeyError) as error:
            raise NativeImageConfigurationError("GENERATION_NATIVE_PARAMETERS_UNSUPPORTED") from error
        negative = request.parameters.get("negative_prompt")
        if negative is not None and not isinstance(negative, str):
            raise NativeImageConfigurationError("GENERATION_NATIVE_PARAMETERS_UNSUPPORTED")
        prompt = request.prompt + (f"\nAvoid: {negative}" if negative else "")
        return ImageGenerationRequest(
            prompt=prompt, output_path=output, reference_images=refs, aspect_ratio=ratio, image_size=size
        )

    async def state(self, workspace: str, project: str, task: str) -> dict[str, object] | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(NativeImageExecutionRow).where(
                    NativeImageExecutionRow.workspace_id == workspace,
                    NativeImageExecutionRow.project_id == project,
                    NativeImageExecutionRow.task_id == task,
                )
            )
            if row is None:
                return None
            return {
                "executionId": row.execution_id,
                "status": row.status,
                "upstreamRequestId": row.upstream_request_id,
                "errorCode": row.error_code,
            }

    async def cancelled(self, task: GenerationTask) -> None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(NativeImageExecutionRow)
                .where(
                    NativeImageExecutionRow.workspace_id == task.request.workspace_id,
                    NativeImageExecutionRow.project_id == task.request.project_id,
                    NativeImageExecutionRow.task_id == task.task_id,
                )
                .with_for_update()
            )
            if row is None or row.status not in {"queued", "preparing"}:
                return
            row.status, row.updated_at = "cancelled", _now()
            key, registry = row.execution_id, row.registry_key
        await self.routes.release_once(registry_key=registry, release_key=key, succeeded=True)

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._loop())

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def _loop(self) -> None:
        import logging

        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Native image queue temporarily unavailable")
                await asyncio.sleep(5)

    async def run_once(self) -> bool:
        from server.xingjing_model_control.runtime import ModelRouteReleaseRow

        async with self.sessions() as session:
            terminal = await session.scalar(
                select(NativeImageExecutionRow)
                .outerjoin(
                    ModelRouteReleaseRow, ModelRouteReleaseRow.release_key == NativeImageExecutionRow.execution_id
                )
                .where(
                    NativeImageExecutionRow.status.in_(("succeeded", "failed", "cancelled")),
                    ModelRouteReleaseRow.release_key.is_(None),
                )
                .limit(1)
            )
        if terminal is not None:
            await self.routes.release_once(
                registry_key=terminal.registry_key,
                release_key=terminal.execution_id,
                succeeded=terminal.status != "failed",
            )
            return True
        now, token = _now(), str(uuid4())
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(NativeImageExecutionRow)
                .where(
                    or_(
                        NativeImageExecutionRow.status == "queued"
                        if self.enabled
                        else NativeImageExecutionRow.status == "never-dispatch",
                        and_(
                            NativeImageExecutionRow.status == "result_ready",
                            or_(
                                NativeImageExecutionRow.claim_token.is_(None),
                                NativeImageExecutionRow.lease_until.is_(None),
                                NativeImageExecutionRow.lease_until < now,
                            ),
                        ),
                        and_(
                            NativeImageExecutionRow.status == "preparing"
                            if self.enabled
                            else NativeImageExecutionRow.status == "never-dispatch",
                            NativeImageExecutionRow.lease_until < now,
                        ),
                        and_(
                            NativeImageExecutionRow.status == "dispatching",
                            NativeImageExecutionRow.lease_until < now,
                        ),
                    )
                )
                .order_by(NativeImageExecutionRow.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return False
            previous = row.status
            if previous == "dispatching":
                row.status, row.error_code, row.updated_at = "unknown", "GENERATION_NATIVE_SUBMISSION_UNKNOWN", now
                return True
            changed = await session.execute(
                update(NativeImageExecutionRow)
                .where(
                    NativeImageExecutionRow.execution_id == row.execution_id,
                    NativeImageExecutionRow.status == previous,
                    NativeImageExecutionRow.claim_token.is_(None)
                    if row.claim_token is None
                    else NativeImageExecutionRow.claim_token == row.claim_token,
                )
                .values(
                    status="result_ready" if previous == "result_ready" else "preparing",
                    claim_token=token,
                    lease_until=now + timedelta(minutes=30),
                    updated_at=now,
                )
            )
            if not changed.rowcount:  # pyright: ignore[reportAttributeAccessIssue]
                return True
            identity = row.execution_id
        await self._execute(identity, token, publish_only=previous == "result_ready")
        return True

    async def _set(self, identity: str, token: str, **changes: Any) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(NativeImageExecutionRow)
                .where(NativeImageExecutionRow.execution_id == identity, NativeImageExecutionRow.claim_token == token)
                .values(**changes, updated_at=_now())
            )

    async def _execute(self, identity: str, token: str, *, publish_only: bool) -> None:
        async with self.sessions() as session:
            row = await session.get(NativeImageExecutionRow, identity)
        if row is None or row.claim_token != token:
            return
        task = await self.repository.get(row.workspace_id, row.project_id, row.task_id)
        if task is None or task.request_fingerprint != row.request_fingerprint:
            await self._set(identity, token, status="unknown", error_code="GENERATION_NATIVE_REQUEST_MISMATCH")
            return
        if task.status in {TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.TIMEOUT}:
            await self.routes.release_once(
                registry_key=row.registry_key,
                release_key=row.execution_id,
                succeeded=task.status is TaskStatus.CANCELLED,
            )
            await self._set(identity, token, status="cancelled" if task.status is TaskStatus.CANCELLED else "failed")
            return
        if task.status is TaskStatus.SUCCEEDED:
            await self._release_success(row)
            await self._set(identity, token, status="succeeded")
            return
        if publish_only:
            try:
                await self._publish(row, task, token)
            except asyncio.CancelledError:
                await asyncio.shield(self._set(identity, token, status="result_ready", lease_until=None))
                raise
            except Exception:
                await self._set(
                    identity,
                    token,
                    status="result_ready",
                    lease_until=None,
                    error_code="GENERATION_NATIVE_PUBLICATION_PENDING",
                )
            return
        backend = None
        dispatched = False
        result_saved = False
        try:
            if _now() >= task.timeout_at:
                raise NativeImageConfigurationError("GENERATION_NATIVE_DEADLINE_EXPIRED")
            backend, _ = await self.credentials.backend(
                row.workspace_id, row.provider_id, row.model_id, row.credential_ref, revision=row.credential_revision
            )
            work = self.root / row.execution_id
            await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
            refs: list[ReferenceImage] = []
            for index, item in enumerate(task.request.resolved_input_assets):
                content = await read_verified_input(self.store, item)
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(item.mime_type)
                if suffix is None:
                    raise NativeImageConfigurationError("GENERATION_INPUT_MEDIA_TYPE_INVALID")
                path = work / f"input-{index}{suffix}"
                await asyncio.to_thread(path.write_bytes, content)
                refs.append(ReferenceImage(str(path), f"input-{index + 1}"))
            image_request = self.image_request(task.request, work / "output.png", refs)
            async with self.sessions.begin() as session:
                stored = await session.get(
                    TaskRow, (row.workspace_id, row.project_id, row.task_id), with_for_update=True
                )
                execution = await session.get(NativeImageExecutionRow, identity, with_for_update=True)
                if (
                    execution is None
                    or execution.claim_token != token
                    or execution.status != "preparing"
                    or stored is None
                    or stored.status != "queued"
                ):
                    return
                task = GenerationTask.model_validate(stored.snapshot)
                started = task.model_copy(
                    update={
                        "status": TaskStatus.RUNNING,
                        "attempt": task.attempt + 1,
                        "version": task.version + 1,
                        "updated_at": _now(),
                        "resolved_provider_id": row.provider_id,
                        "resolved_model_id": row.model_id,
                    }
                )
                stored.status, stored.version, stored.snapshot, stored.updated_at = (
                    "running",
                    started.version,
                    started.model_dump(mode="json"),
                    started.updated_at,
                )
                execution.status, execution.updated_at = "dispatching", _now()
            task, dispatched = started, True
            result = await backend.generate_once(image_request)
            # Receipt is durable before importing/publishing, so local completion can be retried without an API call.
            output = Path(result.image_path).resolve()
            output.relative_to(work.resolve())
            payload = {
                "path": str(output),
                "sha256": hashlib.sha256(await asyncio.to_thread(output.read_bytes)).hexdigest(),
                "upstreamRequestId": result.upstream_request_id,
                "provider": result.provider,
                "model": result.model,
                "completed_at": _now().isoformat(),
            }
            await self._set(
                identity, token, status="result_ready", result=payload, upstream_request_id=result.upstream_request_id
            )
            row.result, row.upstream_request_id = payload, result.upstream_request_id
            result_saved = True
            await self._publish(row, task, token)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._set(
                    identity,
                    token,
                    status="result_ready" if result_saved else "unknown" if dispatched else "queued",
                    error_code="GENERATION_NATIVE_SUBMISSION_UNKNOWN" if dispatched else None,
                    lease_until=None,
                )
            )
            raise
        except Exception as error:
            if result_saved:
                await self._set(
                    identity,
                    token,
                    status="result_ready",
                    error_code="GENERATION_NATIVE_PUBLICATION_PENDING",
                    lease_until=None,
                )
            elif dispatched:
                request_id = getattr(error, "request_id", None)
                await self._set(
                    identity,
                    token,
                    status="unknown",
                    error_code="GENERATION_NATIVE_SUBMISSION_UNKNOWN",
                    upstream_request_id=request_id if isinstance(request_id, str) and len(request_id) <= 255 else None,
                )
            else:
                code = (
                    str(error)
                    if isinstance(error, ValueError)
                    and str(error).startswith("GENERATION_")
                    and str(error).replace("_", "").isalnum()
                    else "GENERATION_NATIVE_PREPARATION_FAILED"
                )
                failed = fail_task(task, failure=Failure(code=code, message=code, retryable=False), at=_now())
                await self.repository.save_with_billing_release(
                    failed, expected_version=task.version, event_id=f"native-failed:{identity}"
                )
                await self._set(identity, token, status="failed", error_code=code)
                await self.routes.release_once(
                    registry_key=row.registry_key, release_key=row.execution_id, succeeded=False
                )
        finally:
            if backend:
                await backend.close()

    async def _publish(self, row: NativeImageExecutionRow, task: GenerationTask, token: str) -> None:
        from PIL import Image

        data = row.result
        if not data or data.get("provider") != row.provider_id or data.get("model") != row.model_id:
            raise NativeImageConfigurationError("GENERATION_NATIVE_OUTPUT_INVALID")
        source = Path(str(data["path"])).resolve()
        source.relative_to((self.root / row.execution_id).resolve())
        content = await asyncio.to_thread(source.read_bytes)
        if hashlib.sha256(content).hexdigest() != data.get("sha256"):
            raise NativeImageConfigurationError("GENERATION_NATIVE_OUTPUT_INVALID")

        def check_image() -> None:
            with Image.open(source) as image:
                if image.format != "PNG":
                    raise NativeImageConfigurationError("GENERATION_NATIVE_OUTPUT_INVALID")
                image.verify()

        await asyncio.to_thread(check_image)
        asset_id = f"native-{row.execution_id}"
        stored = await self.store.import_project_file(
            project_root=self.root / row.execution_id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            task_id=row.task_id,
            asset_id=asset_id,
            media_type="image",
            source_path=source,
        )
        now, evidence_id = datetime.fromisoformat(str(data["completed_at"])), f"native-call:{row.execution_id}"
        await self.repository.append_provider_evidence(
            ProviderCallEvidence(
                evidence_id=evidence_id,
                task_id=row.task_id,
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                provider_id=row.provider_id,
                model_id=row.model_id,
                provider_job_id=None,
                attempt=task.attempt,
                request_summary={"execution_id": row.execution_id, "input_fingerprint": row.request_fingerprint},
                response_summary={
                    "upstream_request_id": row.upstream_request_id,
                    "content_sha256": stored.content_sha256,
                    "settlement_policy": "fixed_success_image/v1",
                },
                occurred_at=now,
            )
        )
        asset = GeneratedAsset(
            asset_id=asset_id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            task_id=row.task_id,
            media_type="image",
            object_key=stored.object_key,
            content_sha256=stored.content_sha256,
            size_bytes=stored.size_bytes,
            metadata={
                "mime_type": stored.mime_type,
                "native_execution_id": row.execution_id,
                "upstream_request_id": row.upstream_request_id,
            },
            created_at=now,
        )
        completed = task.model_copy(
            update={
                "status": TaskStatus.SUCCEEDED,
                "version": task.version + 1,
                "completed_at": now,
                "updated_at": now,
                "output_asset_ids": (asset_id,),
            }
        )
        await self.repository.complete_with_generated_assets(
            completed,
            expected_version=task.version,
            assets=(asset,),
            cost=CostEvidence(
                evidence_id=f"native-cost:{row.execution_id}",
                task_id=row.task_id,
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                provider_call_evidence_id=evidence_id,
                currency=row.currency,
                estimated_minor=row.estimated_minor,
                actual_minor=row.successful_output_minor,
                pricing_version=row.pricing_version,
                recorded_at=now,
            ),
            billing_event_id=f"native-settled:{row.execution_id}",
        )
        await self._release_success(row)
        await self.repository.append_audit(
            ImmutableAuditRecord(
                audit_id=f"native-success:{row.execution_id}",
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                task_id=row.task_id,
                request_id=row.execution_id,
                actor_id="native-image-worker",
                action="generation.native_completed",
                result="succeeded",
                before={},
                after={
                    "execution_id": row.execution_id,
                    "asset_id": asset_id,
                    "pricing_version": row.pricing_version,
                    "actual_minor": row.successful_output_minor,
                },
                occurred_at=now,
            )
        )
        await self._set(row.execution_id, token, status="succeeded", error_code=None)
