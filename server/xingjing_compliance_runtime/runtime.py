from __future__ import annotations

from server.xingjing_compliance.export_gate import FormalExportGate
from server.xingjing_compliance.models import (
    AuditContext,
    ExportAuthority,
    ExportAuthorityState,
    ExportGateDecision,
    ExportManifestContext,
    FormalExportRequest,
)

from .contracts import ComplianceAuthorityStore, DeliveryRecord, ExportAuthorityEvidence
from .errors import RuntimeConfigurationError


class _LoadedAuthority(ExportAuthority):
    def __init__(self, evidence: ExportAuthorityEvidence) -> None:
        self._evidence = evidence

    async def load_export_authority(self, request: FormalExportRequest, audit: AuditContext) -> ExportAuthorityState:
        return self._evidence.state


class ComplianceRuntime:
    """正式导出的运行时入口；只信任通过权威存储读取的事实。"""

    def __init__(self, *, authority_store: ComplianceAuthorityStore | None) -> None:
        if authority_store is None:
            raise RuntimeConfigurationError("authority_store is required for formal exports")
        self._authority_store = authority_store

    async def authorize_export(self, request: FormalExportRequest, audit: AuditContext) -> ExportGateDecision:
        """Run a formal-export preflight against authoritative evidence only.

        A successful gate decision is not an exported file or a published
        deliverable.  Delivery evidence is therefore deliberately not written
        here; the future export executor must write it only after it has
        actually produced and persisted the output.
        """
        evidence = await self._authority_store.load_export_evidence(request, audit)
        return await FormalExportGate(_LoadedAuthority(evidence)).authorize(request, audit)

    async def record_completed_delivery(
        self,
        *,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
    ) -> DeliveryRecord:
        """Persist delivery evidence after a real export executor succeeds."""
        return await self._authority_store.record_delivery(manifest, evidence)
