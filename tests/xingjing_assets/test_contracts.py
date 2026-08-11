from __future__ import annotations

import json
import uuid

from server.xingjing_assets.contracts import AssetKind, FrozenScriptSnapshotRef
from server.xingjing_assets.ids import uuid7


def test_snapshot_reference_and_uuid7_are_wire_safe() -> None:
    asset_id = uuid7()
    snapshot = FrozenScriptSnapshotRef(
        snapshot_id=str(uuid7()),
        workspace_id="workspace-1",
        project_id="project-1",
        script_version_id="script-version-7",
        content_sha256="a" * 64,
        frozen_at="2026-07-15T08:00:00Z",
    )

    assert uuid.UUID(asset_id).version == 7
    assert AssetKind.CHARACTER.value == "character"
    assert json.loads(json.dumps(snapshot.to_dict())) == snapshot.to_dict()
    assert FrozenScriptSnapshotRef.from_dict(snapshot.to_dict()) == snapshot
