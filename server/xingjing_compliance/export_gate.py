from __future__ import annotations

from .models import (
    AuditContext,
    ExportAuthority,
    ExportBlockCode,
    ExportGateDecision,
    ExportManifestContext,
    FormalExportRequest,
    ManualReviewStatus,
    ReviewConclusion,
)


class FormalExportGate:
    def __init__(self, authority: ExportAuthority) -> None:
        self._authority = authority

    async def authorize(self, request: FormalExportRequest, audit: AuditContext) -> ExportGateDecision:
        state = await self._authority.load_export_authority(request, audit)
        blocks: list[ExportBlockCode] = []
        if state.tenant_id != audit.tenant_id:
            blocks.append(ExportBlockCode.TENANT_MISMATCH)
        if not state.has_export_permission:
            blocks.append(ExportBlockCode.PERMISSION_DENIED)
        if (
            state.current_project_version != request.project_version
            or state.compliance_project_version != request.project_version
        ):
            blocks.append(ExportBlockCode.PROJECT_VERSION_CHANGED)
        if not state.snapshot_is_immutable:
            blocks.append(ExportBlockCode.SNAPSHOT_NOT_IMMUTABLE)
        if state.compliance_conclusion is not ReviewConclusion.APPROVED:
            blocks.append(ExportBlockCode.COMPLIANCE_NOT_APPROVED)
        if state.manual_review_status not in (ManualReviewStatus.NOT_REQUIRED, ManualReviewStatus.APPROVED):
            blocks.append(ExportBlockCode.MANUAL_REVIEW_INCOMPLETE)
        if not state.authorizations_complete:
            blocks.append(ExportBlockCode.AUTHORIZATION_INCOMPLETE)
        if not state.billing_settled:
            blocks.append(ExportBlockCode.BILLING_NOT_SETTLED)
        if not state.aigc_marking_satisfied:
            blocks.append(ExportBlockCode.AIGC_MARKING_MISSING)
        if not state.commercial_export_enabled:
            blocks.append(ExportBlockCode.COMMERCIAL_EXPORT_DISABLED)

        manifest = None
        if not blocks:
            manifest = ExportManifestContext(
                project_id=request.project_id,
                project_version=request.project_version,
                target=request.target,
                tenant_id=audit.tenant_id,
                actor_id=audit.actor_id,
                request_id=audit.request_id,
                authorized_at=audit.occurred_at,
            )
        return ExportGateDecision(
            allowed=not blocks,
            block_codes=tuple(blocks),
            audit=audit,
            manifest_context=manifest,
        )
