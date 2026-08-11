from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from server.xingjing_assets.contracts import (
    AssetKind,
    AssetSource,
    FrozenScriptSnapshotRef,
    ReuseScope,
    RightsEvidence,
    RightsStatus,
)
from server.xingjing_assets.errors import CrossProjectReuseDenied, VersionConflict
from server.xingjing_assets.file_repository import AtomicFileAssetRepository
from server.xingjing_assets.service import AssetService


@dataclass
class ShotImpactIndex:
    shot_ids: tuple[str, ...]

    def affected_shot_ids(self, *, workspace_id: str, project_id: str, asset_id: str) -> tuple[str, ...]:
        return self.shot_ids


def snapshot(project_id: str = "project-a") -> FrozenScriptSnapshotRef:
    return FrozenScriptSnapshotRef(
        snapshot_id="019b0000-0000-7000-8000-000000000001",
        workspace_id="workspace-1",
        project_id=project_id,
        script_version_id="script-v4",
        content_sha256="b" * 64,
        frozen_at="2026-07-15T08:00:00Z",
    )


def source() -> AssetSource:
    return AssetSource(
        source_type="frozen_script_extraction",
        source_id="script-v4:character:lin",
        evidence_object_key="workspace-1/evidence/source.json",
        evidence_sha256="c" * 64,
    )


def service_at(path: Path, shots: tuple[str, ...] = ()) -> AssetService:
    return AssetService(AtomicFileAssetRepository(path), ShotImpactIndex(shots))


def test_create_all_subject_kinds_with_stable_ids_and_content_digest(tmp_path: Path) -> None:
    service = service_at(tmp_path)

    assets = [
        service.create(
            workspace_id="workspace-1",
            owner_project_id="project-a",
            kind=kind,
            name=f"asset-{kind.value}",
            content={"prompt": kind.value, "tags": ["主版本"]},
            source=source(),
            frozen_snapshot=snapshot(),
        )
        for kind in AssetKind
    ]

    assert [asset.kind for asset in assets] == list(AssetKind)
    assert len({asset.asset_id for asset in assets}) == 5
    assert all(len(asset.current_version.content_sha256) == 64 for asset in assets)
    assert service_at(tmp_path).get("workspace-1", assets[0].asset_id) == assets[0]


def test_edit_and_rollback_append_versions_without_changing_asset_id(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    original = service.create(
        workspace_id="workspace-1",
        owner_project_id="project-a",
        kind=AssetKind.CHARACTER,
        name="林默",
        content={"hair": "black"},
        source=source(),
        frozen_snapshot=snapshot(),
    )
    service.revise(
        workspace_id="workspace-1",
        asset_id=original.asset_id,
        expected_revision=1,
        content={"hair": "silver"},
        change_note="造型确认",
    )
    rolled_back = service.rollback(
        workspace_id="workspace-1",
        asset_id=original.asset_id,
        target_version_id=original.current_version_id,
        expected_revision=2,
    )

    assert rolled_back.asset_id == original.asset_id
    assert [version.sequence for version in rolled_back.versions] == [1, 2, 3]
    assert rolled_back.current_version.content == {"hair": "black"}
    assert rolled_back.current_version.derived_from_version_id == original.current_version_id
    with pytest.raises(VersionConflict):
        service.revise(
            workspace_id="workspace-1",
            asset_id=original.asset_id,
            expected_revision=1,
            content={"hair": "red"},
            change_note="过期写入",
        )


def test_cross_project_reference_requires_verified_rights_for_target(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    asset = service.create(
        workspace_id="workspace-1",
        owner_project_id="project-a",
        kind=AssetKind.VOICE,
        name="旁白音色",
        content={"voice_profile": "calm"},
        source=source(),
        frozen_snapshot=snapshot(),
    )

    with pytest.raises(CrossProjectReuseDenied):
        service.reference_project(workspace_id="workspace-1", project_id="project-b", asset_id=asset.asset_id)

    licensed = service.record_rights(
        workspace_id="workspace-1",
        asset_id=asset.asset_id,
        expected_revision=1,
        evidence=RightsEvidence(
            rights_id="rights-1",
            status=RightsStatus.VERIFIED,
            reuse_scope=ReuseScope.PROJECT_ALLOWLIST,
            allowed_project_ids=("project-b",),
            evidence_object_key="workspace-1/rights/contract.pdf",
            evidence_sha256="d" * 64,
            valid_until="2027-07-15T00:00:00Z",
        ),
    )
    reference = service.reference_project(
        workspace_id="workspace-1",
        project_id="project-b",
        asset_id=licensed.asset_id,
    )

    assert reference.asset_id == asset.asset_id
    assert reference.asset_version_id == licensed.current_version_id
    assert service_at(tmp_path).get("workspace-1", asset.asset_id).references == (reference,)


def test_impact_query_is_a_replaceable_downstream_port(tmp_path: Path) -> None:
    service = service_at(tmp_path, ("shot-7", "shot-9"))
    asset = service.create(
        workspace_id="workspace-1",
        owner_project_id="project-a",
        kind=AssetKind.PROP,
        name="旧怀表",
        content={"material": "brass"},
        source=source(),
        frozen_snapshot=snapshot(),
    )

    assert service.affected_shot_ids(workspace_id="workspace-1", project_id="project-a", asset_id=asset.asset_id) == (
        "shot-7",
        "shot-9",
    )


def test_expired_rights_do_not_allow_cross_project_reuse(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    asset = service.create(
        workspace_id="workspace-1",
        owner_project_id="project-a",
        kind=AssetKind.COSTUME,
        name="礼服",
        content={"color": "black"},
        source=source(),
        frozen_snapshot=snapshot(),
    )
    service.record_rights(
        workspace_id="workspace-1",
        asset_id=asset.asset_id,
        expected_revision=1,
        evidence=RightsEvidence(
            rights_id="expired-rights",
            status=RightsStatus.VERIFIED,
            reuse_scope=ReuseScope.WORKSPACE,
            allowed_project_ids=(),
            evidence_object_key="workspace-1/rights/expired.pdf",
            evidence_sha256="e" * 64,
            valid_until="2020-01-01T00:00:00Z",
        ),
    )

    with pytest.raises(CrossProjectReuseDenied):
        service.reference_project(workspace_id="workspace-1", project_id="project-b", asset_id=asset.asset_id)
