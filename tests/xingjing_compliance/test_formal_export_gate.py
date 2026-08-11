from datetime import UTC, datetime

import pytest

from server.xingjing_compliance import (
    AuditContext,
    ExportAuthorityState,
    ExportBlockCode,
    FormalExportGate,
    FormalExportRequest,
    ManualReviewStatus,
    ReviewConclusion,
)


class StubAuthority:
    def __init__(self, state: ExportAuthorityState) -> None:
        self.state = state

    async def load_export_authority(self, request: FormalExportRequest, audit: AuditContext) -> ExportAuthorityState:
        return self.state


def authority_state(**overrides: object) -> ExportAuthorityState:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "current_project_version": "v7",
        "snapshot_is_immutable": True,
        "has_export_permission": True,
        "compliance_conclusion": ReviewConclusion.APPROVED,
        "compliance_project_version": "v7",
        "manual_review_status": ManualReviewStatus.NOT_REQUIRED,
        "authorizations_complete": True,
        "billing_settled": True,
        "aigc_marking_satisfied": True,
        "commercial_export_enabled": True,
    }
    values.update(overrides)
    return ExportAuthorityState(**values)  # type: ignore[arg-type]


def request() -> FormalExportRequest:
    return FormalExportRequest(project_id="project-1", project_version="v7", target="mp4")


def audit() -> AuditContext:
    return AuditContext(
        actor_id="owner-1",
        tenant_id="tenant-1",
        request_id="request-export-1",
        source="direct_api",
        occurred_at=datetime(2026, 7, 15, 12, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_direct_api_cannot_export_when_compliance_is_blocked() -> None:
    gate = FormalExportGate(StubAuthority(authority_state(compliance_conclusion=ReviewConclusion.BLOCKED)))

    decision = await gate.authorize(request(), audit())

    assert decision.allowed is False
    assert ExportBlockCode.COMPLIANCE_NOT_APPROVED in decision.block_codes
    assert decision.audit.request_id == "request-export-1"


@pytest.mark.asyncio
async def test_project_change_invalidates_old_compliance_result() -> None:
    gate = FormalExportGate(
        StubAuthority(authority_state(current_project_version="v8", compliance_project_version="v7"))
    )

    decision = await gate.authorize(request(), audit())

    assert decision.allowed is False
    assert ExportBlockCode.PROJECT_VERSION_CHANGED in decision.block_codes


@pytest.mark.asyncio
async def test_export_requires_all_server_side_gates_and_emits_manifest_context() -> None:
    gate = FormalExportGate(StubAuthority(authority_state()))

    decision = await gate.authorize(request(), audit())

    assert decision.allowed is True
    assert decision.block_codes == ()
    assert decision.manifest_context is not None
    assert decision.manifest_context.project_version == "v7"
    assert decision.manifest_context.request_id == "request-export-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"has_export_permission": False}, ExportBlockCode.PERMISSION_DENIED),
        ({"authorizations_complete": False}, ExportBlockCode.AUTHORIZATION_INCOMPLETE),
        ({"billing_settled": False}, ExportBlockCode.BILLING_NOT_SETTLED),
        ({"aigc_marking_satisfied": False}, ExportBlockCode.AIGC_MARKING_MISSING),
        ({"commercial_export_enabled": False}, ExportBlockCode.COMMERCIAL_EXPORT_DISABLED),
        ({"snapshot_is_immutable": False}, ExportBlockCode.SNAPSHOT_NOT_IMMUTABLE),
    ],
)
async def test_each_authoritative_gate_blocks_export(override: dict[str, object], code: ExportBlockCode) -> None:
    gate = FormalExportGate(StubAuthority(authority_state(**override)))

    decision = await gate.authorize(request(), audit())

    assert decision.allowed is False
    assert code in decision.block_codes
