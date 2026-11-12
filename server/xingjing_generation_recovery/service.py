"""Verify evidence, preview financial impact and atomically resolve the original task."""

import hashlib
import hmac
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_generation import GenerationTask, TaskStatus
from server.xingjing_generation.contracts import Failure
from server.xingjing_generation_persistence.repository import (
    AuditRow,
    CostEvidence,
    GeneratedAsset,
    ImmutableAuditRecord,
    ProviderCallEvidence,
    ProviderEvidenceRow,
    SqlAlchemyGenerationTaskRepository,
    VersionConflict,
)
from server.xingjing_generation_runtime.artifact_storage import LocalGenerationArtifactStore
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_model_control.runtime import ModelCallEvidenceRow, ModelRouteResolver

from .artifacts import import_outputs, verify_outputs
from .contracts import PreviewCommand, ProviderReceipt, RecoveryError, ResolveCommand
from .models import RecoveryCommandRow, RecoveryPreviewRow, RecoveryReceiptRow
from .state import Target, digest, load_target, now, utc, validate_receipt

READ_PERMISSIONS = frozenset({"admin.business.view", "admin.finance.view"})
MANAGE_PERMISSIONS = READ_PERMISSIONS | {"admin.business.manage", "admin.finance.manage"}


def require(context: TrustedWorkspaceContext, *, manage: bool = False) -> None:
    if not (MANAGE_PERMISSIONS if manage else READ_PERMISSIONS).issubset(context.permissions):
        raise RecoveryError("RECOVERY_PERMISSION_DENIED", 403)


def receipt_payload(row: RecoveryReceiptRow) -> dict[str, object]:
    return {
        "id": row.id,
        **row.payload,
        "verifiedAt": utc(row.created_at).isoformat(),
        "source": "signed_adapter",
        "artifacts": [
            {key: item[key] for key in ("asset_id", "media_type", "content_sha256", "size_bytes", "metadata")}
            for item in row.artifacts
        ],
    }


def asset_from_payload(value: dict[str, object]) -> GeneratedAsset:
    return GeneratedAsset(
        asset_id=str(value["asset_id"]),
        workspace_id=str(value["workspace_id"]),
        project_id=str(value["project_id"]),
        task_id=str(value["task_id"]),
        media_type=str(value["media_type"]),
        object_key=str(value["object_key"]),
        content_sha256=str(value["content_sha256"]),
        size_bytes=int(str(value["size_bytes"])),
        metadata=cast(dict, value["metadata"]),
        created_at=datetime.fromisoformat(str(value["created_at"])),
    )


class GenerationRecoveryService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        repository: SqlAlchemyGenerationTaskRepository,
        routes: ModelRouteResolver,
        *,
        store: LocalGenerationArtifactStore | None,
        provider_root: Path | None,
        native_root: Path | None,
        receipt_secret: str | None,
    ) -> None:
        self.sessions, self.repository, self.routes = sessions, repository, routes
        self.store, self.provider_root, self.native_root, self.receipt_secret = (
            store,
            provider_root,
            native_root,
            receipt_secret,
        )

    async def receive(self, raw: bytes, signature: str) -> dict[str, object]:
        try:
            return await self._receive(raw, signature)
        except IntegrityError:
            # Independent tasks can race for the globally unique receipt identity.
            receipt = ProviderReceipt.model_validate_json(raw)
            async with self.sessions() as session:
                previous = await session.get(RecoveryReceiptRow, str(receipt.receipt_id))
            if previous is None:
                raise
            if previous.fingerprint != digest(receipt.model_dump(mode="json", by_alias=True)):
                raise RecoveryError("RECOVERY_RECEIPT_CONFLICT") from None
            return receipt_payload(previous)

    async def _receive(self, raw: bytes, signature: str) -> dict[str, object]:
        if not self.receipt_secret:
            raise RecoveryError("RECOVERY_RECEIPTS_UNAVAILABLE", 503)
        expected = hmac.new(self.receipt_secret.encode(), b"generation-recovery/v1\n" + raw, hashlib.sha256).hexdigest()
        provided = signature.removeprefix("sha256=")
        if (
            len(raw) > 64_000
            or len(provided) != 64
            or any(c not in "0123456789abcdef" for c in provided)
            or not hmac.compare_digest(provided, expected)
        ):
            raise RecoveryError("RECOVERY_SIGNATURE_INVALID", 401)
        try:
            receipt = ProviderReceipt.model_validate_json(raw)
        except ValidationError as error:
            raise RecoveryError("RECOVERY_RECEIPT_INVALID", 422) from error
        fingerprint = digest(receipt.model_dump(mode="json", by_alias=True))
        async with self.sessions() as session:
            previous = await session.get(RecoveryReceiptRow, str(receipt.receipt_id))
            if previous:
                if previous.fingerprint != fingerprint:
                    raise RecoveryError("RECOVERY_RECEIPT_CONFLICT")
                return receipt_payload(previous)
            target = await load_target(session, receipt.workspace_id, receipt.project_id, receipt.task_id)
            validate_receipt(target, receipt)
        root = (
            (self.native_root / receipt.execution_id if self.native_root else None)
            if receipt.kind == "native_image"
            else (
                self.provider_root / receipt.workspace_id / receipt.project_id / receipt.task_id
                if self.provider_root
                else None
            )
        )
        artifacts = await import_outputs(self.store, root, receipt, media_type=target.task.request.media_type.value)
        payload = receipt.model_dump(mode="json", by_alias=True, exclude={"outputs", "receipt_id"})
        outcome = {key: value for key, value in payload.items() if key not in {"observedAt", "providerReceiptRef"}}
        outcome["outputs"] = [
            {"sha256": item.sha256, "size": item.size_bytes, "mime": item.mime_type} for item in receipt.outputs
        ]
        async with self.sessions.begin() as session:
            target = await load_target(session, receipt.workspace_id, receipt.project_id, receipt.task_id, lock=True)
            validate_receipt(target, receipt)
            previous = await session.get(RecoveryReceiptRow, str(receipt.receipt_id))
            if previous:
                if previous.fingerprint != fingerprint:
                    raise RecoveryError("RECOVERY_RECEIPT_CONFLICT")
                return receipt_payload(previous)
            row = RecoveryReceiptRow(
                id=str(receipt.receipt_id),
                tenant_id=target.tenant_id,
                workspace_id=receipt.workspace_id,
                project_id=receipt.project_id,
                task_id=receipt.task_id,
                fingerprint=fingerprint,
                result_fingerprint=digest(outcome),
                payload=payload,
                artifacts=artifacts,
                created_at=now(),
            )
            session.add(row)
            session.add(
                AuditRow(
                    audit_id=f"recovery-receipt:{row.id}",
                    workspace_id=row.workspace_id,
                    project_id=row.project_id,
                    task_id=row.task_id,
                    request_id=row.id,
                    occurred_at=row.created_at,
                    payload={
                        "audit_id": f"recovery-receipt:{row.id}",
                        "workspace_id": row.workspace_id,
                        "project_id": row.project_id,
                        "task_id": row.task_id,
                        "request_id": row.id,
                        "actor_id": "signed-recovery-adapter",
                        "action": "generation.recovery_evidence_received",
                        "result": receipt.status,
                        "before": {},
                        "after": {"receiptId": row.id, "resultFingerprint": row.result_fingerprint},
                    },
                )
            )
            return receipt_payload(row)

    async def receipts(self, session: AsyncSession, target: Target) -> list[RecoveryReceiptRow]:
        request = target.task.request
        return list(
            (
                await session.scalars(
                    select(RecoveryReceiptRow)
                    .where(
                        RecoveryReceiptRow.tenant_id == target.tenant_id,
                        RecoveryReceiptRow.workspace_id == request.workspace_id,
                        RecoveryReceiptRow.project_id == request.project_id,
                        RecoveryReceiptRow.task_id == target.task.task_id,
                    )
                    .order_by(RecoveryReceiptRow.created_at, RecoveryReceiptRow.id)
                )
            ).all()
        )

    async def _receipt(self, session: AsyncSession, target: Target, identity: str) -> RecoveryReceiptRow:
        rows = await self.receipts(session, target)
        row = next((item for item in rows if item.id == identity), None)
        if row is None:
            raise RecoveryError("RECOVERY_RECEIPT_NOT_FOUND", 404)
        if len({item.result_fingerprint for item in rows}) != 1:
            raise RecoveryError("RECOVERY_EVIDENCE_CONFLICT")
        if row.payload.get("requestFingerprint") != target.task.request_fingerprint:
            raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
        return row

    async def preview(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        task_id: str,
        command: PreviewCommand,
    ) -> dict[str, object]:
        require(context, manage=True)
        async with self.sessions.begin() as session:
            target = await load_target(
                session, context.workspace_id, project_id, task_id, tenant_id=context.tenant_id, lock=True
            )
            target.actionable()
            if target.task.version != command.expected_version:
                raise RecoveryError("RECOVERY_VERSION_CHANGED")
            receipt = await self._receipt(session, target, str(command.receipt_id))
            await verify_outputs(self.store, receipt.artifacts)
            plan = self._plan(target, receipt)
            row = RecoveryPreviewRow(
                id=str(uuid4()),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                task_id=task_id,
                actor_id=context.actor_id,
                receipt_id=receipt.id,
                task_version=target.task.version,
                state_digest=target.state_digest(),
                plan=plan,
                created_at=now(),
                expires_at=now() + timedelta(minutes=10),
            )
            session.add(row)
            return {"previewId": row.id, "expiresAt": row.expires_at.isoformat(), **plan}

    @staticmethod
    def _plan(target: Target, receipt: RecoveryReceiptRow) -> dict[str, object]:
        hold = target.hold
        if hold is None:
            raise RecoveryError("RECOVERY_BILLING_CHANGED")
        payload = receipt.payload
        if (payload["currency"], payload["estimatedMinor"], payload["pricingVersion"]) != (
            hold.currency,
            hold.estimated_minor,
            hold.pricing_version,
        ):
            raise RecoveryError("RECOVERY_BILLING_CHANGED")
        actual = int(str(payload["actualMinor"]))
        if not 0 <= actual <= hold.estimated_minor:
            raise RecoveryError("RECOVERY_BILLING_CHANGED")
        return {
            "projectId": target.task.request.project_id,
            "taskId": target.task.task_id,
            "taskVersion": target.task.version,
            "receiptId": receipt.id,
            "action": "restore_result" if payload["status"] == "succeeded" else "confirm_failure",
            "currency": hold.currency,
            "heldMinor": hold.estimated_minor,
            "chargedMinor": actual,
            "releasedMinor": hold.estimated_minor - actual,
            "pricingVersion": hold.pricing_version,
            "artifactCount": len(receipt.artifacts),
            "newProviderCalls": 0,
        }

    async def _previous(
        self,
        context: TrustedWorkspaceContext,
        key: str,
        fingerprint: str,
    ) -> dict[str, object] | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(RecoveryCommandRow).where(
                    RecoveryCommandRow.tenant_id == context.tenant_id,
                    RecoveryCommandRow.workspace_id == context.workspace_id,
                    RecoveryCommandRow.actor_id == context.actor_id,
                    RecoveryCommandRow.idempotency_key == key,
                )
            )
        if row is None:
            return None
        if row.fingerprint != fingerprint:
            raise RecoveryError("RECOVERY_COMMAND_CONFLICT")
        return row.result

    async def resolve(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        task_id: str,
        command: ResolveCommand,
        *,
        key: str,
    ) -> dict[str, object]:
        require(context, manage=True)
        try:
            return await self._resolve(context, project_id, task_id, command, key=key)
        except RecoveryError as error:
            if error.status == 409:
                fingerprint = digest(
                    {"projectId": project_id, "taskId": task_id, **command.model_dump(mode="json", by_alias=True)}
                )
                previous = await self._previous(context, key, fingerprint)
                if previous is not None:
                    return previous
            raise

    async def _resolve(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        task_id: str,
        command: ResolveCommand,
        *,
        key: str,
    ) -> dict[str, object]:
        require(context, manage=True)
        if (
            not key
            or len(key) > 128
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-" for ch in key)
        ):
            raise RecoveryError("RECOVERY_COMMAND_INVALID", 422)
        fingerprint = digest(
            {"projectId": project_id, "taskId": task_id, **command.model_dump(mode="json", by_alias=True)}
        )
        previous = await self._previous(context, key, fingerprint)
        if previous is not None:
            return previous
        async with self.sessions() as session:
            preview = await session.get(RecoveryPreviewRow, str(command.preview_id))
            if preview is None or (
                preview.tenant_id,
                preview.workspace_id,
                preview.project_id,
                preview.task_id,
                preview.actor_id,
            ) != (context.tenant_id, context.workspace_id, project_id, task_id, context.actor_id):
                raise RecoveryError("RECOVERY_PREVIEW_NOT_FOUND", 404)
            if utc(preview.expires_at) <= now():
                raise RecoveryError("RECOVERY_PREVIEW_EXPIRED")
            target = await load_target(session, context.workspace_id, project_id, task_id, tenant_id=context.tenant_id)
            target.actionable()
            if target.task.version != preview.task_version or target.state_digest() != preview.state_digest:
                raise RecoveryError("RECOVERY_VERSION_CHANGED")
            receipt = await self._receipt(session, target, preview.receipt_id)
        await verify_outputs(self.store, receipt.artifacts)
        plan = self._plan(target, receipt)
        if plan != preview.plan:
            raise RecoveryError("RECOVERY_VERSION_CHANGED")
        binding = target.binding()
        at, command_id = now(), str(uuid4())
        succeeded = receipt.payload["status"] == "succeeded"
        assets = tuple(asset_from_payload(item) for item in receipt.artifacts)
        completed = target.task.model_copy(
            update={
                "status": TaskStatus.SUCCEEDED if succeeded else TaskStatus.FAILED,
                "version": target.task.version + 1,
                "attempt": int(str(receipt.payload["attempt"])),
                "resolved_provider_id": binding["providerId"],
                "resolved_model_id": binding["modelId"],
                "provider_job_id": receipt.payload.get("providerJobId"),
                "completed_at": at,
                "updated_at": at,
                "output_asset_ids": tuple(asset.asset_id for asset in assets),
                "failure": None
                if succeeded
                else Failure(code="RECOVERY_CONFIRMED_FAILURE", message="RECOVERY_CONFIRMED_FAILURE", retryable=False),
            }
        )
        result = {
            "commandId": command_id,
            "receiptId": receipt.id,
            "projectId": project_id,
            "taskId": task_id,
            "status": completed.status.value,
            "taskVersion": completed.version,
            "currency": plan["currency"],
            "chargedMinor": plan["chargedMinor"],
            "releasedMinor": plan["releasedMinor"],
            "completedAt": at.isoformat(),
        }
        provider_evidence_id = f"recovery-call:{receipt.id}"
        cost = CostEvidence(
            evidence_id=f"recovery-cost:{receipt.id}",
            task_id=task_id,
            workspace_id=context.workspace_id,
            project_id=project_id,
            provider_call_evidence_id=provider_evidence_id,
            currency=str(plan["currency"]),
            estimated_minor=int(str(plan["heldMinor"])),
            actual_minor=int(str(plan["chargedMinor"])),
            pricing_version=str(plan["pricingVersion"]),
            recorded_at=at,
        )

        async def prepare(session: AsyncSession, _: GenerationTask) -> None:
            locked = await load_target(
                session,
                context.workspace_id,
                project_id,
                task_id,
                tenant_id=context.tenant_id,
                lock=True,
                before=target.task,
            )
            locked.actionable()
            if utc(preview.expires_at) <= now():
                raise RecoveryError("RECOVERY_PREVIEW_EXPIRED")
            if locked.state_digest() != preview.state_digest:
                raise RecoveryError("RECOVERY_VERSION_CHANGED")
            current_receipt = await self._receipt(session, locked, receipt.id)
            if current_receipt.fingerprint != receipt.fingerprint:
                raise RecoveryError("RECOVERY_EVIDENCE_CONFLICT")
            await verify_outputs(self.store, current_receipt.artifacts)
            session.add(
                RecoveryCommandRow(
                    id=command_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    task_id=task_id,
                    actor_id=context.actor_id,
                    idempotency_key=key,
                    fingerprint=fingerprint,
                    preview_id=preview.id,
                    receipt_id=receipt.id,
                    reason=command.reason,
                    result=result,
                    created_at=at,
                )
            )
            if locked.native:
                locked.native.status = completed.status.value
                locked.native.claim_token, locked.native.lease_until = None, None
                locked.native.upstream_request_id = str(receipt.payload["upstreamRequestId"])
                locked.native.error_code = None if succeeded else "RECOVERY_CONFIRMED_FAILURE"
                locked.native.updated_at = at
            evidence = ProviderCallEvidence(
                evidence_id=provider_evidence_id,
                task_id=task_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                provider_id=binding["providerId"],
                model_id=binding["modelId"],
                provider_job_id=completed.provider_job_id,
                attempt=completed.attempt,
                request_summary={
                    "request_fingerprint": completed.request_fingerprint,
                    "execution_id": binding["executionId"],
                },
                response_summary={
                    "receipt_id": receipt.id,
                    "receipt_fingerprint": receipt.fingerprint,
                    "upstream_request_id": receipt.payload["upstreamRequestId"],
                    "status": completed.status.value,
                    "artifact_sha256": [asset.content_sha256 for asset in assets],
                },
                occurred_at=at,
            )
            session.add(
                ProviderEvidenceRow(
                    evidence_id=evidence.evidence_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    task_id=task_id,
                    occurred_at=at,
                    payload={k: v for k, v in asdict(evidence).items() if k != "occurred_at"},
                )
            )
            audit = ImmutableAuditRecord(
                audit_id=f"recovery:{command_id}",
                workspace_id=context.workspace_id,
                project_id=project_id,
                task_id=task_id,
                request_id=context.request_id,
                actor_id=context.actor_id,
                action="generation.recovery_resolved",
                result=completed.status.value,
                before=target.task.model_dump(mode="json"),
                after={
                    "task": completed.model_dump(mode="json"),
                    "receiptId": receipt.id,
                    "previewId": preview.id,
                    "reason": command.reason,
                    "impact": plan,
                },
                occurred_at=at,
            )
            session.add(
                AuditRow(
                    audit_id=audit.audit_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    task_id=task_id,
                    request_id=context.request_id,
                    occurred_at=at,
                    payload={k: v for k, v in asdict(audit).items() if k != "occurred_at"},
                )
            )
            session.add(
                ModelCallEvidenceRow(
                    id=str(uuid5(NAMESPACE_URL, f"generation-recovery:{receipt.id}")),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    task_id=task_id,
                    registry_key=binding["registryKey"],
                    provider_job_id=completed.provider_job_id,
                    request_digest=completed.request_fingerprint,
                    response_digest=receipt.result_fingerprint,
                    cost_minor=cost.actual_minor,
                    pricing_version=cost.pricing_version,
                    outcome=completed.status.value,
                    created_at=at,
                )
            )

        try:
            if succeeded:
                await self.repository.complete_with_generated_assets(
                    completed,
                    expected_version=target.task.version,
                    assets=assets,
                    cost=cost,
                    billing_event_id=f"recovery:{receipt.id}",
                    completion_preparer=prepare,
                )
            else:
                await self.repository.save_with_billing_release(
                    completed,
                    expected_version=target.task.version,
                    cost=cost,
                    event_id=f"recovery:{receipt.id}",
                    completion_preparer=prepare,
                )
        except (VersionConflict, IntegrityError) as error:
            previous = await self._previous(context, key, fingerprint)
            if previous is not None:
                return previous
            raise RecoveryError("RECOVERY_VERSION_CHANGED") from error
        try:
            await self.routes.release_once(
                registry_key=binding["registryKey"], release_key=binding["executionId"], succeeded=succeeded
            )
        except Exception:
            logging.getLogger(__name__).exception("Recovery committed; model reservation release awaits reconciliation")
        return result
