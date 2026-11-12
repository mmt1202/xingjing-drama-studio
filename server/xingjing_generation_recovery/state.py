"""Read authoritative task identity and determine recovery eligibility."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.xingjing_generation import GenerationTask, TaskStatus
from server.xingjing_generation_persistence.repository import GenerationBillingHoldRow, TaskRow
from server.xingjing_generation_runtime.native_image.models import NativeImageExecutionRow
from server.xingjing_model_control.runtime import ModelRouteReservationRow
from server.xingjing_platform_persistence.persistence import ProjectRow

from .contracts import ProviderReceipt, RecoveryError

TERMINAL = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED}


def now() -> datetime:
    return datetime.now(UTC)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass
class Target:
    tenant_id: str
    project_name: str
    task: GenerationTask
    hold: GenerationBillingHoldRow | None
    native: NativeImageExecutionRow | None
    reservation: ModelRouteReservationRow | None

    def binding(self) -> dict[str, str]:
        native, reservation, task = self.native, self.reservation, self.task
        if native is not None:
            if native.tenant_id != self.tenant_id or native.request_fingerprint != task.request_fingerprint:
                raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
            value = {
                "executionId": native.execution_id,
                "registryKey": native.registry_key,
                "kind": "native_image",
                "providerId": native.provider_id,
                "modelId": native.model_id,
            }
        elif reservation is not None and reservation.kind == "gateway":
            value = {
                "executionId": reservation.release_key,
                "registryKey": reservation.registry_key,
                "kind": "gateway",
                "providerId": task.request.requested_provider_id or "",
                "modelId": task.request.requested_model_id or "",
            }
        else:
            raise RecoveryError("RECOVERY_LEGACY_IDENTITY_REQUIRED")
        if reservation is not None and (
            reservation.tenant_id != self.tenant_id
            or reservation.release_key != value["executionId"]
            or reservation.registry_key != value["registryKey"]
            or reservation.kind != value["kind"]
            or reservation.request_fingerprint != task.request_fingerprint
        ):
            raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
        return value

    def attention(self) -> bool:
        if self.task.status in TERMINAL:
            return False
        if self.native:
            return self.native.status in {"unknown", "result_ready"}
        return bool(
            self.reservation
            and self.reservation.dispatch_started_at
            and (
                self.task.status in {TaskStatus.QUEUED, TaskStatus.CANCELLING}
                or self.task.status is TaskStatus.RUNNING
                and self.task.timeout_at <= now()
            )
        )

    def actionable(self) -> None:
        self.binding()
        if self.task.status in TERMINAL:
            raise RecoveryError("RECOVERY_TASK_TERMINAL")
        if not self.hold or self.hold.status != "active":
            raise RecoveryError("RECOVERY_BILLING_CHANGED")
        if self.reservation and self.reservation.released_at is not None:
            raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
        if self.native and self.native.status not in {"unknown", "result_ready"}:
            raise RecoveryError("RECOVERY_EXECUTION_ACTIVE")
        if not self.native and (not self.reservation or not self.reservation.dispatch_started_at):
            raise RecoveryError("RECOVERY_EXECUTION_ACTIVE")

    def state_digest(self) -> str:
        hold, native, reservation = self.hold, self.native, self.reservation
        return digest(
            {
                "task": self.task.model_dump(mode="json"),
                "hold": {
                    key: getattr(hold, key)
                    for key in (
                        "currency",
                        "pricing_version",
                        "estimated_minor",
                        "actual_minor",
                        "released_minor",
                        "status",
                        "terminal_event_id",
                    )
                }
                if hold
                else None,
                "native": {
                    "status": native.status,
                    "token": native.claim_token,
                    "result": native.result,
                    "upstream": native.upstream_request_id,
                    "updated": utc(native.updated_at).isoformat(),
                }
                if native
                else None,
                "reservation": {
                    "key": reservation.release_key,
                    "released": reservation.released_at is not None,
                    "dispatched": reservation.dispatch_started_at is not None,
                }
                if reservation
                else None,
            }
        )


async def load_target(
    session: AsyncSession,
    workspace_id: str,
    project_id: str,
    task_id: str,
    *,
    tenant_id: str | None = None,
    lock: bool = False,
    before: GenerationTask | None = None,
) -> Target:
    project = await session.scalar(
        select(ProjectRow).where(
            ProjectRow.id == project_id,
            ProjectRow.workspace_id == workspace_id,
            ProjectRow.deleted_at.is_(None),
            *([ProjectRow.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    )
    row = await session.get(TaskRow, (workspace_id, project_id, task_id), with_for_update=lock)
    if project is None or row is None:
        raise RecoveryError("RECOVERY_TASK_NOT_FOUND", 404)
    try:
        task = before or GenerationTask.model_validate(row.snapshot)
    except ValidationError as error:
        raise RecoveryError("RECOVERY_TASK_INVALID") from error
    if (task.request.workspace_id, task.request.project_id, task.task_id) != (workspace_id, project_id, task_id):
        raise RecoveryError("RECOVERY_TASK_INVALID")
    if before is None and (task.version != row.version or task.status.value != row.status):
        raise RecoveryError("RECOVERY_TASK_INVALID")
    native = await session.scalar(
        select(NativeImageExecutionRow)
        .where(
            NativeImageExecutionRow.workspace_id == workspace_id,
            NativeImageExecutionRow.project_id == project_id,
            NativeImageExecutionRow.task_id == task_id,
        )
        .with_for_update()
        if lock
        else select(NativeImageExecutionRow).where(
            NativeImageExecutionRow.workspace_id == workspace_id,
            NativeImageExecutionRow.project_id == project_id,
            NativeImageExecutionRow.task_id == task_id,
        )
    )
    reservation = await session.scalar(
        select(ModelRouteReservationRow).where(
            ModelRouteReservationRow.workspace_id == workspace_id,
            ModelRouteReservationRow.project_id == project_id,
            ModelRouteReservationRow.task_id == task_id,
        )
    )
    hold = await session.get(GenerationBillingHoldRow, (workspace_id, project_id, task_id))
    return Target(project.tenant_id, project.name, task, hold, native, reservation)


def validate_receipt(target: Target, receipt: ProviderReceipt) -> None:
    task, hold, native = target.task, target.hold, target.native
    binding = target.binding()
    if (
        any(
            binding[key] != receipt.model_dump(by_alias=True)[key]
            for key in ("executionId", "kind", "providerId", "modelId")
        )
        or receipt.request_fingerprint != task.request_fingerprint
    ):
        raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
    if task.provider_job_id and task.provider_job_id != receipt.provider_job_id:
        raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
    if native:
        if native.status in {"queued", "preparing"} or receipt.provider_job_id:
            raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
        if native.upstream_request_id and native.upstream_request_id != receipt.upstream_request_id:
            raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
        if (
            receipt.status == "succeeded"
            and (len(receipt.outputs) != 1 or receipt.actual_minor != native.successful_output_minor)
        ) or (receipt.status == "failed" and receipt.actual_minor):
            raise RecoveryError("RECOVERY_BILLING_CHANGED")
        if native.result and (
            receipt.status != "succeeded" or receipt.outputs[0].sha256 != native.result.get("sha256")
        ):
            raise RecoveryError("RECOVERY_EVIDENCE_CONFLICT")
    elif target.reservation is None or target.reservation.dispatch_started_at is None:
        raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
    expected_attempt = task.attempt or 1
    if receipt.attempt != expected_attempt:
        raise RecoveryError("RECOVERY_IDENTITY_MISMATCH")
    if not hold or (receipt.currency, receipt.estimated_minor, receipt.pricing_version) != (
        hold.currency,
        hold.estimated_minor,
        hold.pricing_version,
    ):
        raise RecoveryError("RECOVERY_BILLING_CHANGED")
    if not task.created_at <= receipt.completed_at <= receipt.observed_at <= now() + timedelta(minutes=5):
        raise RecoveryError("RECOVERY_RECEIPT_INVALID", 422)
