from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .contracts import (
    Asset,
    AssetKind,
    AssetReference,
    AssetSource,
    AssetVersion,
    FrozenScriptSnapshotRef,
    RightsEvidence,
)
from .errors import AssetNotFound, CrossProjectReuseDenied
from .ids import uuid7
from .ports import AssetRepository, ShotImpactQuery


class AssetService:
    def __init__(self, repository: AssetRepository, shot_impacts: ShotImpactQuery) -> None:
        self._repository = repository
        self._shot_impacts = shot_impacts

    def create(
        self,
        *,
        workspace_id: str,
        owner_project_id: str,
        kind: AssetKind,
        name: str,
        content: dict[str, Any],
        source: AssetSource,
        frozen_snapshot: FrozenScriptSnapshotRef,
    ) -> Asset:
        if frozen_snapshot.workspace_id != workspace_id or frozen_snapshot.project_id != owner_project_id:
            raise ValueError("frozen snapshot must belong to the asset workspace and owner project")
        version = self._version(sequence=1, content=content, change_note="initial")
        asset = Asset(
            asset_id=uuid7(),
            workspace_id=workspace_id,
            owner_project_id=owner_project_id,
            kind=kind,
            name=name,
            source=source,
            frozen_snapshot=frozen_snapshot,
            current_version_id=version.version_id,
            revision=1,
            versions=(version,),
        )
        self._repository.save(asset, expected_revision=None)
        return asset

    def get(self, workspace_id: str, asset_id: str) -> Asset:
        asset = self._repository.get(workspace_id, asset_id)
        if asset is None:
            raise AssetNotFound(asset_id)
        return asset

    def revise(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        expected_revision: int,
        content: dict[str, Any],
        change_note: str,
        name: str | None = None,
    ) -> Asset:
        asset = self.get(workspace_id, asset_id)
        version = self._version(
            sequence=len(asset.versions) + 1,
            content=content,
            change_note=change_note,
            derived_from_version_id=asset.current_version_id,
        )
        changed = replace(
            asset,
            name=name.strip() if name is not None else asset.name,
            current_version_id=version.version_id,
            revision=asset.revision + 1,
            versions=(*asset.versions, version),
        )
        self._repository.save(changed, expected_revision=expected_revision)
        return changed

    def rollback(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        target_version_id: str,
        expected_revision: int,
    ) -> Asset:
        asset = self.get(workspace_id, asset_id)
        try:
            target = next(item for item in asset.versions if item.version_id == target_version_id)
        except StopIteration as error:
            raise AssetNotFound(target_version_id) from error
        version = self._version(
            sequence=len(asset.versions) + 1,
            content=target.content,
            change_note=f"rollback:{target_version_id}",
            derived_from_version_id=target_version_id,
        )
        changed = replace(
            asset,
            current_version_id=version.version_id,
            revision=asset.revision + 1,
            versions=(*asset.versions, version),
        )
        self._repository.save(changed, expected_revision=expected_revision)
        return changed

    def record_rights(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        expected_revision: int,
        evidence: RightsEvidence,
    ) -> Asset:
        asset = self.get(workspace_id, asset_id)
        changed = replace(asset, revision=asset.revision + 1, rights=(*asset.rights, evidence))
        self._repository.save(changed, expected_revision=expected_revision)
        return changed

    def reference_project(self, *, workspace_id: str, project_id: str, asset_id: str) -> AssetReference:
        asset = self.get(workspace_id, asset_id)
        if project_id != asset.owner_project_id and not any(item.permits(project_id) for item in asset.rights):
            raise CrossProjectReuseDenied(f"asset {asset_id} is not licensed for project {project_id}")
        existing = next((item for item in asset.references if item.project_id == project_id), None)
        if existing is not None:
            return existing
        reference = AssetReference(
            reference_id=uuid7(),
            workspace_id=workspace_id,
            project_id=project_id,
            asset_id=asset_id,
            asset_version_id=asset.current_version_id,
            created_at=_now(),
        )
        changed = replace(asset, revision=asset.revision + 1, references=(*asset.references, reference))
        self._repository.save(changed, expected_revision=asset.revision)
        return reference

    def affected_shot_ids(self, *, workspace_id: str, project_id: str, asset_id: str) -> tuple[str, ...]:
        self.get(workspace_id, asset_id)
        return self._shot_impacts.affected_shot_ids(workspace_id=workspace_id, project_id=project_id, asset_id=asset_id)

    @staticmethod
    def _version(
        *,
        sequence: int,
        content: dict[str, Any],
        change_note: str,
        derived_from_version_id: str | None = None,
    ) -> AssetVersion:
        serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        detached_content: dict[str, Any] = json.loads(serialized)
        return AssetVersion(
            version_id=uuid7(),
            sequence=sequence,
            content=detached_content,
            content_sha256=hashlib.sha256(serialized).hexdigest(),
            change_note=change_note,
            created_at=_now(),
            derived_from_version_id=derived_from_version_id,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
