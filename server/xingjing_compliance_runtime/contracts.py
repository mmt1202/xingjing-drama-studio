from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from server.xingjing_compliance.models import (
    AuditContext,
    ExportAuthorityState,
    ExportManifestContext,
    FormalExportRequest,
)


@dataclass(frozen=True, slots=True)
class ExportAuthorityEvidence:
    """一次导出判定使用的持久化权威快照。"""

    state: ExportAuthorityState
    policy_id: str
    policy_version: str
    authorization_ids: tuple[str, ...]
    reviewed_at: datetime
    review_version: int
    billing_summary: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    tenant_id: str
    project_id: str
    project_version: str
    request_id: str
    policy_version: str
    manifest_digest: str
    created_at: datetime
    target: str | None = None
    status: str = "succeeded"
    format: str | None = None
    object_key: str | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None
    download_path: str | None = None


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    format: str
    object_key: str
    content_sha256: str
    size_bytes: int
    download_path: str


class ComplianceAuthorityStore(Protocol):
    async def load_export_evidence(
        self, request: FormalExportRequest, audit: AuditContext
    ) -> ExportAuthorityEvidence: ...

    async def record_delivery(
        self,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
        artifact: ExportArtifact | None = None,
    ) -> DeliveryRecord: ...
