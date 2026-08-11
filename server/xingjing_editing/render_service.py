from __future__ import annotations

from .contracts import (
    AccessContext,
    AuditEvent,
    AuditOutcome,
    Permission,
    build_preview_summary,
    canonical_sha256,
    validate_idempotency_key,
)
from .errors import IdempotencyConflict, PermissionDenied, RenderTaskNotFound, TimelineNotFound, VersionConflict
from .ports import (
    AuditRecorder,
    Clock,
    RenderBillingPolicy,
    RendererGateway,
    RenderObjectStorage,
    RenderRepository,
    StableIdGenerator,
)
from .rendering import (
    FinalVideoSelection,
    FinalVideoVersion,
    OutputSummary,
    RenderCallback,
    RenderCallbackDisposition,
    RenderCallbackOutcome,
    RenderCallbackResult,
    RenderFailure,
    RenderStatus,
    RenderTask,
    RequestRenderCommand,
    cancel_render_task_from_callback,
    fail_render_task,
    render_request_fingerprint,
    request_cancel_render_task,
    retry_render_task,
    start_render_task,
    succeed_render_task,
    timeout_render_task,
)


class RenderService:
    def __init__(
        self,
        *,
        repository: RenderRepository,
        renderer: RendererGateway,
        object_storage: RenderObjectStorage,
        audit: AuditRecorder,
        ids: StableIdGenerator,
        clock: Clock,
        billing_policy: RenderBillingPolicy,
    ) -> None:
        self._repository = repository
        self._renderer = renderer
        self._object_storage = object_storage
        self._audit = audit
        self._ids = ids
        self._clock = clock
        self._billing_policy = billing_policy

    async def _require_manage(self, context: AccessContext, *, action: str, object_id: str) -> None:
        if Permission.FINAL_MANAGE in context.permissions:
            return
        await self._audit.record(
            AuditEvent(
                event_id=self._ids.new_id("audit"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                action=action,
                object_type="render_task",
                object_id=object_id,
                outcome=AuditOutcome.DENIED,
                occurred_at=self._clock.now(),
            )
        )
        raise PermissionDenied()

    async def request_render(
        self,
        context: AccessContext,
        command: RequestRenderCommand,
        *,
        idempotency_key: str,
    ) -> RenderTask:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="render.request", object_id=command.timeline_id)
        timeline = await self._repository.get_timeline_version(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=command.timeline_id,
            version_id=command.timeline_version_id,
        )
        if timeline is None or timeline.project_id != command.project_id:
            raise TimelineNotFound()
        if timeline.revision != command.expected_timeline_revision:
            raise VersionConflict(
                expected_version=command.expected_timeline_revision,
                current_version=timeline.revision,
            )
        now = self._clock.now()
        if command.deadline_at <= now:
            raise ValueError("渲染截止时间必须晚于当前时间")
        preview = build_preview_summary(timeline)
        fingerprint = render_request_fingerprint(command, preview)
        existing = await self._repository.get_render_task_by_idempotency(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflict()
            return existing
        candidate = RenderTask.queued(
            task_id=self._ids.new_id("render_task"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=command.project_id,
            timeline_id=timeline.timeline_id,
            timeline_version_id=timeline.version_id,
            timeline_revision=timeline.revision,
            final_video_id=timeline.final_video_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            preview=preview,
            profile=command.profile,
            billing_currency=self._billing_policy.currency,
            billing_estimated_minor=self._billing_policy.estimate_minor(
                duration_ms=preview.duration_ms,
                width=command.profile.width,
                height=command.profile.height,
            ),
            billing_pricing_version=self._billing_policy.pricing_version,
            max_attempts=command.max_attempts,
            created_at=now,
            deadline_at=command.deadline_at,
        )
        stored, created = await self._repository.create_render_task(
            candidate,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        if created:
            await self._audit.record(
                AuditEvent(
                    event_id=self._ids.new_id("audit"),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=stored.project_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action="render.request",
                    object_type="render_task",
                    object_id=stored.task_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    occurred_at=now,
                    after_sha256=stored.request_fingerprint,
                    details={
                        "timeline_version_id": stored.timeline_version_id,
                        "input_snapshot_sha256": stored.preview.input_snapshot_sha256,
                    },
                )
            )
        return stored

    async def start_render(
        self,
        context: AccessContext,
        *,
        task_id: str,
        expected_task_revision: int,
    ) -> RenderTask:
        await self._require_manage(context, action="render.start", object_id=task_id)
        current = await self._repository.get_render_task(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=task_id,
        )
        if current is None:
            raise RenderTaskNotFound()
        if current.task_revision != expected_task_revision:
            raise VersionConflict(
                expected_version=expected_task_revision,
                current_version=current.task_revision,
            )
        timeline = await self._repository.get_timeline_version(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=current.timeline_id,
            version_id=current.timeline_version_id,
        )
        if timeline is None or timeline.project_id != current.project_id:
            raise TimelineNotFound()
        submission = await self._renderer.submit(
            task_id=current.task_id,
            attempt=current.attempt + 1,
            input_snapshot_sha256=current.preview.input_snapshot_sha256,
            timeline=timeline,
            profile=current.profile,
            deadline_at=current.deadline_at,
        )
        running = start_render_task(
            current,
            renderer_job_id=submission.renderer_job_id,
            accepted_at=submission.accepted_at,
        )
        stored = await self._repository.save_render_task(running, expected_revision=expected_task_revision)
        await self._audit.record(
            AuditEvent(
                event_id=self._ids.new_id("audit"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=stored.project_id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                action="render.start",
                object_type="render_task",
                object_id=stored.task_id,
                outcome=AuditOutcome.SUCCEEDED,
                occurred_at=self._clock.now(),
                before_sha256=current.request_fingerprint,
                after_sha256=stored.request_fingerprint,
                details={
                    "attempt": stored.attempt,
                    "renderer_job_id": stored.renderer_job_id or "",
                    "from_status": current.status.value,
                    "to_status": stored.status.value,
                },
            )
        )
        return stored

    async def handle_callback(
        self,
        context: AccessContext,
        *,
        task_id: str,
        expected_task_revision: int,
        callback: RenderCallback,
    ) -> RenderCallbackResult:
        current = await self._repository.get_render_task(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=task_id,
        )
        if current is None:
            raise RenderTaskNotFound()
        if callback.event_id in current.processed_callback_event_ids:
            return RenderCallbackResult(task=current, disposition=RenderCallbackDisposition.DUPLICATE)
        if current.task_revision != expected_task_revision:
            raise VersionConflict(
                expected_version=expected_task_revision,
                current_version=current.task_revision,
            )
        if current.status in {RenderStatus.SUCCEEDED, RenderStatus.FAILED, RenderStatus.CANCELLED}:
            return RenderCallbackResult(task=current, disposition=RenderCallbackDisposition.IGNORED_LATE)
        if current.status is RenderStatus.RETRYING:
            return RenderCallbackResult(task=current, disposition=RenderCallbackDisposition.IGNORED_STALE_ATTEMPT)
        if callback.attempt != current.attempt:
            return RenderCallbackResult(task=current, disposition=RenderCallbackDisposition.IGNORED_STALE_ATTEMPT)
        if callback.renderer_job_id != current.renderer_job_id:
            return RenderCallbackResult(task=current, disposition=RenderCallbackDisposition.REJECTED_JOB_MISMATCH)
        if current.status is RenderStatus.CANCELLING and callback.outcome is not RenderCallbackOutcome.CANCELLED:
            return RenderCallbackResult(task=current, disposition=RenderCallbackDisposition.IGNORED_LATE)
        if callback.outcome is RenderCallbackOutcome.CANCELLED:
            cancelled = cancel_render_task_from_callback(
                current,
                event_id=callback.event_id,
                occurred_at=callback.occurred_at,
            )
            stored = await self._repository.save_render_task(cancelled, expected_revision=current.task_revision)
            await self._record_callback_audit(context, current=current, stored=stored, action="render.cancelled")
            return RenderCallbackResult(task=stored, disposition=RenderCallbackDisposition.APPLIED)
        if callback.outcome is RenderCallbackOutcome.FAILED:
            assert callback.failure is not None
            failed = fail_render_task(
                current,
                failure=callback.failure,
                event_id=callback.event_id,
                occurred_at=callback.occurred_at,
            )
            stored = await self._repository.save_render_task(failed, expected_revision=current.task_revision)
            await self._record_callback_audit(context, current=current, stored=stored, action="render.failed")
            return RenderCallbackResult(task=stored, disposition=RenderCallbackDisposition.APPLIED)

        assert callback.output is not None
        if (
            callback.output.rendered_input_snapshot_sha256 != current.preview.input_snapshot_sha256
            or callback.output.rendered_composition_sha256 != current.preview.composition_sha256
        ):
            return await self._fail_unverified_output(
                context, current=current, callback=callback, code="RENDER_SUMMARY_MISMATCH"
            )
        stored_object = await self._object_storage.stat(
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            object_key=callback.output.object_key,
        )
        if stored_object is None or stored_object.content_sha256 != callback.output.content_sha256:
            return await self._fail_unverified_output(
                context, current=current, callback=callback, code="RENDER_OBJECT_UNVERIFIED"
            )
        output = OutputSummary(
            object_key=stored_object.object_key,
            content_sha256=stored_object.content_sha256,
            size_bytes=stored_object.size_bytes,
            duration_ms=stored_object.duration_ms,
            input_snapshot_sha256=callback.output.rendered_input_snapshot_sha256,
            composition_sha256=callback.output.rendered_composition_sha256,
        )
        deduplication_key = canonical_sha256(
            {"request_fingerprint": current.request_fingerprint, "content_sha256": stored_object.content_sha256}
        )
        candidate_version = FinalVideoVersion(
            version_id=self._ids.new_id("final_video_version"),
            final_video_id=current.final_video_id,
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            project_id=current.project_id,
            timeline_id=current.timeline_id,
            timeline_version_id=current.timeline_version_id,
            render_task_id=current.task_id,
            render_attempt=current.attempt,
            profile=current.profile,
            preview=current.preview,
            output=output,
            deduplication_key=deduplication_key,
            created_at=callback.occurred_at,
        )
        succeeded = succeed_render_task(
            current,
            output_version_id=candidate_version.version_id,
            event_id=callback.event_id,
            occurred_at=callback.occurred_at,
        )
        completion = await self._repository.complete_render(
            succeeded,
            candidate_version,
            expected_revision=current.task_revision,
        )
        await self._record_callback_audit(
            context,
            current=current,
            stored=completion.task,
            action="render.succeeded",
        )
        return RenderCallbackResult(
            task=completion.task,
            disposition=RenderCallbackDisposition.APPLIED,
            version=completion.version,
        )

    async def cancel_render(
        self,
        context: AccessContext,
        *,
        task_id: str,
        expected_task_revision: int,
        reason: str,
    ) -> RenderTask:
        await self._require_manage(context, action="render.cancel", object_id=task_id)
        current = await self._repository.get_render_task(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=task_id,
        )
        if current is None:
            raise RenderTaskNotFound()
        if current.task_revision != expected_task_revision:
            raise VersionConflict(
                expected_version=expected_task_revision,
                current_version=current.task_revision,
            )
        requested_at = max(self._clock.now(), current.updated_at)
        cancelling = request_cancel_render_task(current, reason=reason, requested_at=requested_at)
        stored = await self._repository.save_render_task(cancelling, expected_revision=current.task_revision)
        await self._record_callback_audit(context, current=current, stored=stored, action="render.cancel_requested")
        if stored.status is RenderStatus.CANCELLING:
            assert stored.renderer_job_id is not None
            await self._renderer.cancel(renderer_job_id=stored.renderer_job_id)
        return stored

    async def retry_render(
        self,
        context: AccessContext,
        *,
        task_id: str,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> RenderTask:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="render.retry", object_id=task_id)
        current = await self._repository.get_render_task(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=task_id,
        )
        if current is None:
            raise RenderTaskNotFound()
        if current.retry_idempotency_key == idempotency_key:
            return current
        if current.task_revision != expected_task_revision:
            raise VersionConflict(
                expected_version=expected_task_revision,
                current_version=current.task_revision,
            )
        retried = retry_render_task(
            current,
            idempotency_key=idempotency_key,
            retried_at=max(self._clock.now(), current.updated_at),
        )
        stored = await self._repository.save_render_task(retried, expected_revision=current.task_revision)
        await self._record_callback_audit(context, current=current, stored=stored, action="render.retry")
        return stored

    async def reconcile_timeout(
        self,
        context: AccessContext,
        *,
        task_id: str,
    ) -> RenderTask:
        current = await self._repository.get_render_task(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=task_id,
        )
        if current is None:
            raise RenderTaskNotFound()
        now = max(self._clock.now(), current.updated_at)
        if current.status not in {
            RenderStatus.QUEUED,
            RenderStatus.RUNNING,
            RenderStatus.RETRYING,
            RenderStatus.CANCELLING,
        } or now < current.deadline_at:
            return current
        failed = timeout_render_task(
            current,
            event_id=f"timeout-{current.task_id}-{current.attempt}",
            occurred_at=now,
        )
        stored = await self._repository.save_render_task(
            failed,
            expected_revision=current.task_revision,
        )
        await self._record_callback_audit(
            context,
            current=current,
            stored=stored,
            action="render.timed_out",
        )
        return stored

    async def select_final_version(
        self,
        context: AccessContext,
        *,
        project_id: str,
        final_video_id: str,
        version_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> FinalVideoSelection:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="final_video.version.select", object_id=final_video_id)
        selected_at = self._clock.now()
        selection = FinalVideoSelection(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=project_id,
            final_video_id=final_video_id,
            selected_version_id=version_id,
            revision=expected_revision + 1,
            selected_by=context.actor_id,
            selected_at=selected_at,
        )
        fingerprint = canonical_sha256(
            {
                "project_id": project_id,
                "final_video_id": final_video_id,
                "version_id": version_id,
                "expected_revision": expected_revision,
            }
        )
        stored, created = await self._repository.select_final_video_version(
            selection,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        if created:
            await self._audit.record(
                AuditEvent(
                    event_id=self._ids.new_id("audit"),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action="final_video.version.select",
                    object_type="final_video",
                    object_id=final_video_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    occurred_at=selected_at,
                    after_sha256=fingerprint,
                    details={
                        "selected_version_id": version_id,
                        "selection_revision": stored.revision,
                    },
                )
            )
        return stored

    async def _fail_unverified_output(
        self,
        context: AccessContext,
        *,
        current: RenderTask,
        callback: RenderCallback,
        code: str,
    ) -> RenderCallbackResult:
        failed = fail_render_task(
            current,
            failure=RenderFailure(code=code, message="渲染产物未通过对象存储或摘要校验", retryable=True),
            event_id=callback.event_id,
            occurred_at=callback.occurred_at,
        )
        stored = await self._repository.save_render_task(failed, expected_revision=current.task_revision)
        await self._record_callback_audit(context, current=current, stored=stored, action="render.output_unverified")
        return RenderCallbackResult(task=stored, disposition=RenderCallbackDisposition.APPLIED)

    async def _record_callback_audit(
        self,
        context: AccessContext,
        *,
        current: RenderTask,
        stored: RenderTask,
        action: str,
    ) -> None:
        await self._audit.record(
            AuditEvent(
                event_id=self._ids.new_id("audit"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=stored.project_id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                action=action,
                object_type="render_task",
                object_id=stored.task_id,
                outcome=AuditOutcome.SUCCEEDED,
                occurred_at=stored.updated_at,
                before_sha256=current.request_fingerprint,
                after_sha256=stored.request_fingerprint,
                details={"from_status": current.status.value, "to_status": stored.status.value},
            )
        )
