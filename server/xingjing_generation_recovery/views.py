"""Scoped recovery projections do not expose storage paths or credential references."""

from pydantic import ValidationError
from sqlalchemy import and_, exists, func, or_, select

from server.xingjing_generation import GenerationTask
from server.xingjing_generation_persistence.repository import GenerationBillingHoldRow, TaskRow
from server.xingjing_generation_runtime.native_image.models import NativeImageExecutionRow
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_model_control.runtime import ModelRouteReservationRow
from server.xingjing_platform_persistence.persistence import ProjectRow

from .contracts import RecoveryError
from .models import RecoveryCommandRow, RecoveryReceiptRow
from .service import MANAGE_PERMISSIONS, GenerationRecoveryService, receipt_payload, require
from .state import load_target, now, utc


async def list_tasks(
    service: GenerationRecoveryService,
    context: TrustedWorkspaceContext,
    *,
    search: str,
    state: str,
    offset: int,
    limit: int,
) -> dict[str, object]:
    require(context)
    scope = [
        TaskRow.workspace_id == context.workspace_id,
        ProjectRow.tenant_id == context.tenant_id,
        ProjectRow.workspace_id == context.workspace_id,
        ProjectRow.deleted_at.is_(None),
    ]
    receipt_scope = [
        RecoveryReceiptRow.workspace_id == TaskRow.workspace_id,
        RecoveryReceiptRow.project_id == TaskRow.project_id,
        RecoveryReceiptRow.task_id == TaskRow.task_id,
    ]
    count_receipts = (
        select(func.count()).select_from(RecoveryReceiptRow).where(*receipt_scope).correlate(TaskRow).scalar_subquery()
    )
    conflict = (
        select(func.count(func.distinct(RecoveryReceiptRow.result_fingerprint)))
        .where(*receipt_scope)
        .correlate(TaskRow)
        .scalar_subquery()
        > 1
    )
    handled = exists(
        select(RecoveryCommandRow.id).where(
            RecoveryCommandRow.workspace_id == TaskRow.workspace_id,
            RecoveryCommandRow.project_id == TaskRow.project_id,
            RecoveryCommandRow.task_id == TaskRow.task_id,
        )
    ).correlate(TaskRow)
    attention = or_(
        conflict,
        and_(
            GenerationBillingHoldRow.status == "active",
            ModelRouteReservationRow.release_key.is_(None),
            NativeImageExecutionRow.execution_id.is_(None),
            or_(
                TaskRow.status.in_(["succeeded", "failed", "timeout", "cancelled"]),
                TaskRow.snapshot["timeout_at"].as_string() < now().isoformat(),
            ),
        ),
        and_(
            TaskRow.status.not_in(["succeeded", "failed", "timeout", "cancelled"]),
            or_(
                count_receipts > 0,
                NativeImageExecutionRow.status.in_(["unknown", "result_ready"]),
                and_(
                    ModelRouteReservationRow.kind == "gateway",
                    ModelRouteReservationRow.dispatch_started_at.is_not(None),
                    or_(
                        TaskRow.status.in_(["queued", "cancelling"]),
                        and_(
                            TaskRow.status == "running", TaskRow.snapshot["timeout_at"].as_string() < now().isoformat()
                        ),
                    ),
                ),
            ),
        ),
    )
    if state == "attention":
        scope.append(attention)
    elif state == "handled":
        scope.append(handled)
    if search:
        scope.append(
            or_(
                TaskRow.task_id.contains(search, autoescape=True),
                func.lower(ProjectRow.name).contains(search.lower(), autoescape=True),
            )
        )
    statement = select(
        TaskRow,
        ProjectRow.name,
        NativeImageExecutionRow.status,
        GenerationBillingHoldRow.currency,
        GenerationBillingHoldRow.estimated_minor,
        GenerationBillingHoldRow.status,
        count_receipts.label("receipt_count"),
        conflict.label("conflict"),
        attention.label("attention"),
    ).join(ProjectRow, and_(ProjectRow.id == TaskRow.project_id, ProjectRow.workspace_id == TaskRow.workspace_id))
    for model in (NativeImageExecutionRow, ModelRouteReservationRow, GenerationBillingHoldRow):
        statement = statement.outerjoin(
            model,
            and_(
                model.workspace_id == TaskRow.workspace_id,
                model.project_id == TaskRow.project_id,
                model.task_id == TaskRow.task_id,
            ),
        )
    statement = statement.where(*scope)
    async with service.sessions() as session:
        total = await session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await session.execute(
                statement.order_by(TaskRow.updated_at.desc(), TaskRow.task_id, TaskRow.project_id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    items = []
    for (
        row,
        project_name,
        native_state,
        currency,
        amount,
        hold_state,
        receipt_count,
        has_conflict,
        needs_attention,
    ) in rows:
        try:
            task = GenerationTask.model_validate(row.snapshot)
            prompt, media = task.request.prompt[:160], task.request.media_type.value
        except ValidationError:
            prompt, media = "", "unknown"
        items.append(
            {
                "id": row.task_id,
                "projectId": row.project_id,
                "projectName": project_name,
                "status": row.status,
                "version": row.version,
                "promptExcerpt": prompt,
                "mediaType": media,
                "executionState": native_state,
                "currency": currency,
                "heldMinor": amount if hold_state == "active" else 0,
                "receiptCount": receipt_count,
                "conflict": bool(has_conflict),
                "attention": bool(needs_attention),
                "updatedAt": utc(row.updated_at).isoformat(),
            }
        )
    return {
        "items": items,
        "total": total or 0,
        "offset": offset,
        "limit": limit,
        "context": {
            "actorId": context.actor_id,
            "workspaceId": context.workspace_id,
            "role": context.role,
            "canManage": MANAGE_PERMISSIONS.issubset(context.permissions),
        },
        "receiptChannelConfigured": bool(service.receipt_secret),
    }


async def task_detail(
    service: GenerationRecoveryService,
    context: TrustedWorkspaceContext,
    project_id: str,
    task_id: str,
) -> dict[str, object]:
    require(context)
    async with service.sessions() as session:
        target = await load_target(session, context.workspace_id, project_id, task_id, tenant_id=context.tenant_id)
        receipts = await service.receipts(session, target)
        commands = (
            await session.scalars(
                select(RecoveryCommandRow)
                .where(
                    RecoveryCommandRow.tenant_id == context.tenant_id,
                    RecoveryCommandRow.workspace_id == context.workspace_id,
                    RecoveryCommandRow.project_id == project_id,
                    RecoveryCommandRow.task_id == task_id,
                )
                .order_by(RecoveryCommandRow.created_at)
            )
        ).all()
    task, hold, native, reservation = target.task, target.hold, target.native, target.reservation
    blocked: str | None = None
    try:
        target.actionable()
    except RecoveryError as error:
        blocked = error.code
    conflict = len({item.result_fingerprint for item in receipts}) > 1
    if conflict:
        blocked = "RECOVERY_EVIDENCE_CONFLICT"
    elif not receipts and blocked is None:
        blocked = "RECOVERY_RECEIPT_REQUIRED"
    return {
        "task": {
            "id": task.task_id,
            "projectId": project_id,
            "projectName": target.project_name,
            "status": task.status.value,
            "version": task.version,
            "prompt": task.request.prompt,
            "capability": task.request.capability,
            "mediaType": task.request.media_type.value,
            "requestFingerprint": task.request_fingerprint,
            "providerId": native.provider_id if native else task.request.requested_provider_id,
            "modelId": native.model_id if native else task.request.requested_model_id,
            "providerJobId": task.provider_job_id,
            "attempt": task.attempt,
            "createdAt": task.created_at.isoformat(),
            "timeoutAt": task.timeout_at.isoformat(),
            "inputs": [
                {
                    "assetId": item.asset_id,
                    "versionId": item.asset_version_id,
                    "sha256": item.content_sha256,
                    "mediaType": item.media_type,
                }
                for item in task.request.resolved_input_assets
            ],
            "budgetScope": task.request.budget_scope.model_dump(mode="json") if task.request.budget_scope else None,
        },
        "execution": {
            "kind": "native_image" if native else "gateway",
            "state": native.status if native else task.status.value,
            "id": native.execution_id if native else reservation.release_key if reservation else None,
            "upstreamRequestId": native.upstream_request_id if native else task.provider_job_id,
            "submittedAt": utc(reservation.dispatch_started_at).isoformat()
            if reservation and reservation.dispatch_started_at
            else None,
            "capacityReserved": reservation.released_at is None if reservation else None,
        },
        "billing": {
            "currency": hold.currency,
            "heldMinor": hold.estimated_minor if hold.status == "active" else 0,
            "estimatedMinor": hold.estimated_minor,
            "chargedMinor": hold.actual_minor,
            "releasedMinor": hold.released_minor,
            "pricingVersion": hold.pricing_version,
            "status": hold.status,
        }
        if hold
        else None,
        "receipts": [receipt_payload(item) for item in receipts],
        "conflict": conflict,
        "blockedCode": blocked,
        "history": [
            {
                "id": item.id,
                "actorId": item.actor_id,
                "reason": item.reason,
                "receiptId": item.receipt_id,
                "result": item.result,
                "createdAt": utc(item.created_at).isoformat(),
            }
            for item in commands
        ],
        "canManage": MANAGE_PERMISSIONS.issubset(context.permissions),
    }
