from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.xingjing_assets.contracts import AssetKind
from server.xingjing_assets.file_repository import AtomicFileAssetRepository

from .test_asset_service import service_at, snapshot, source


def test_aggregate_contract_round_trips_as_plain_json(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    asset = service.create(
        workspace_id="workspace-1",
        owner_project_id="project-a",
        kind=AssetKind.SCENE,
        name="雨夜车站",
        content={"weather": "rain"},
        source=source(),
        frozen_snapshot=snapshot(),
    )

    encoded = json.dumps(asset.to_dict(), ensure_ascii=False)

    assert "雨夜车站" in encoded
    assert service.get("workspace-1", asset.asset_id).to_dict() == asset.to_dict()


def test_failed_atomic_replace_preserves_last_complete_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = service_at(tmp_path)
    asset = service.create(
        workspace_id="workspace-1",
        owner_project_id="project-a",
        kind=AssetKind.PROP,
        name="信件",
        content={"seal": "intact"},
        source=source(),
        frozen_snapshot=snapshot(),
    )

    def fail_replace(source_path: str | Path, target_path: str | Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        service.revise(
            workspace_id="workspace-1",
            asset_id=asset.asset_id,
            expected_revision=1,
            content={"seal": "broken"},
            change_note="拆封",
        )

    persisted = AtomicFileAssetRepository(tmp_path).get("workspace-1", asset.asset_id)
    assert persisted == asset
    assert not list(tmp_path.rglob("*.tmp"))
