from __future__ import annotations

from typing import Protocol

from .contracts import Asset


class AssetRepository(Protocol):
    def get(self, workspace_id: str, asset_id: str) -> Asset | None: ...

    def save(self, asset: Asset, *, expected_revision: int | None) -> None: ...


class ShotImpactQuery(Protocol):
    def affected_shot_ids(self, *, workspace_id: str, project_id: str, asset_id: str) -> tuple[str, ...]: ...
