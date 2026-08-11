from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from server.xingjing_compliance import (
    AuditContext,
    ExportBlockCode,
    FormalExportRequest,
    ManualReviewStatus,
    ReviewConclusion,
)
from server.xingjing_compliance_runtime import (
    AuthorityDataMissing,
    ComplianceRuntime,
    RuntimeConfigurationError,
    SqliteComplianceAuthorityStore,
)


def audit() -> AuditContext:
    return AuditContext(
        actor_id="owner-1",
        tenant_id="tenant-1",
        request_id="request-export-1",
        source="direct_api",
        occurred_at=datetime(2026, 7, 16, 12, tzinfo=UTC),
    )


def request() -> FormalExportRequest:
    return FormalExportRequest(project_id="project-1", project_version="v7", target="mp4")


async def persist_complete_authority(store: SqliteComplianceAuthorityStore) -> None:
    await store.save_policy(
        policy_id="content-policy",
        version="2026-07",
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    await store.save_authorization(
        tenant_id="tenant-1",
        project_id="project-1",
        project_version="v7",
        authorization_id="rights-1",
        valid=True,
    )
    await store.save_review(
        tenant_id="tenant-1",
        project_id="project-1",
        project_version="v7",
        policy_id="content-policy",
        policy_version="2026-07",
        conclusion=ReviewConclusion.APPROVED,
        manual_review_status=ManualReviewStatus.NOT_REQUIRED,
    )
    await store.save_export_snapshot(
        tenant_id="tenant-1",
        project_id="project-1",
        current_project_version="v7",
        snapshot_version="v7",
        target="mp4",
        immutable=True,
        export_permitted=True,
        aigc_marking_satisfied=True,
        commercial_export_enabled=True,
    )
    await store.save_billing_settlement(
        tenant_id="tenant-1", project_id="project-1", project_version="v7", settled=True
    )


@pytest.mark.asyncio
async def test_runtime_refuses_to_start_without_an_authoritative_store() -> None:
    with pytest.raises(RuntimeConfigurationError, match="authority_store"):
        ComplianceRuntime(authority_store=None)


@pytest.mark.asyncio
async def test_direct_export_reads_persisted_evidence_and_records_a_delivery(tmp_path: Path) -> None:
    store = SqliteComplianceAuthorityStore(tmp_path / "compliance.sqlite3")
    await store.initialize()
    await persist_complete_authority(store)
    runtime = ComplianceRuntime(authority_store=store)

    decision = await runtime.authorize_export(request(), audit())

    assert decision.allowed is True
    assert decision.manifest_context is not None
    deliveries = await store.list_deliveries(tenant_id="tenant-1", project_id="project-1")
    assert len(deliveries) == 1
    assert deliveries[0].project_version == "v7"
    assert deliveries[0].policy_version == "2026-07"
    assert deliveries[0].manifest_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_caller_cannot_claim_billing_is_settled_when_persisted_authority_disagrees(tmp_path: Path) -> None:
    store = SqliteComplianceAuthorityStore(tmp_path / "compliance.sqlite3")
    await store.initialize()
    await persist_complete_authority(store)
    await store.save_billing_settlement(
        tenant_id="tenant-1", project_id="project-1", project_version="v7", settled=False
    )
    runtime = ComplianceRuntime(authority_store=store)

    decision = await runtime.authorize_export(request(), audit())

    assert decision.allowed is False
    assert ExportBlockCode.BILLING_NOT_SETTLED in decision.block_codes
    assert await store.list_deliveries(tenant_id="tenant-1", project_id="project-1") == ()


@pytest.mark.asyncio
async def test_missing_persisted_policy_is_an_explicit_authority_failure(tmp_path: Path) -> None:
    store = SqliteComplianceAuthorityStore(tmp_path / "compliance.sqlite3")
    await store.initialize()
    await persist_complete_authority(store)
    await store.delete_policies()
    runtime = ComplianceRuntime(authority_store=store)

    with pytest.raises(AuthorityDataMissing, match="policy"):
        await runtime.authorize_export(request(), audit())


@pytest.mark.asyncio
async def test_project_change_in_persisted_snapshot_invalidates_export(tmp_path: Path) -> None:
    store = SqliteComplianceAuthorityStore(tmp_path / "compliance.sqlite3")
    await store.initialize()
    await persist_complete_authority(store)
    await store.save_export_snapshot(
        tenant_id="tenant-1",
        project_id="project-1",
        current_project_version="v8",
        snapshot_version="v7",
        target="mp4",
        immutable=True,
        export_permitted=True,
        aigc_marking_satisfied=True,
        commercial_export_enabled=True,
    )
    runtime = ComplianceRuntime(authority_store=store)

    decision = await runtime.authorize_export(request(), audit())

    assert decision.allowed is False
    assert ExportBlockCode.PROJECT_VERSION_CHANGED in decision.block_codes


@pytest.mark.asyncio
async def test_snapshot_version_mismatch_invalidates_export_even_when_project_head_is_unchanged(tmp_path: Path) -> None:
    store = SqliteComplianceAuthorityStore(tmp_path / "compliance.sqlite3")
    await store.initialize()
    await persist_complete_authority(store)
    await store.save_export_snapshot(
        tenant_id="tenant-1",
        project_id="project-1",
        current_project_version="v7",
        snapshot_version="v8",
        target="mp4",
        immutable=True,
        export_permitted=True,
        aigc_marking_satisfied=True,
        commercial_export_enabled=True,
    )
    runtime = ComplianceRuntime(authority_store=store)

    decision = await runtime.authorize_export(request(), audit())

    assert decision.allowed is False
    assert ExportBlockCode.PROJECT_VERSION_CHANGED in decision.block_codes
