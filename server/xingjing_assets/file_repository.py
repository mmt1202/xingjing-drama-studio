from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .contracts import Asset
from .errors import VersionConflict

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AtomicFileAssetRepository:
    """可替换持久化端口的原子 JSON 文件适配器。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def get(self, workspace_id: str, asset_id: str) -> Asset | None:
        value = self._read(workspace_id, asset_id)
        return None if value is None else Asset.from_dict(value)

    def save(self, asset: Asset, *, expected_revision: int | None) -> None:
        existing = self._read(asset.workspace_id, asset.asset_id)
        actual_revision = None if existing is None else int(existing["revision"])
        if actual_revision != expected_revision:
            raise VersionConflict(
                f"asset {asset.asset_id} expected revision {expected_revision}, actual {actual_revision}"
            )
        self._atomic_write(self._path(asset.workspace_id, asset.asset_id), asset.to_dict())

    def _path(self, workspace_id: str, asset_id: str) -> Path:
        if not _SAFE_ID.fullmatch(workspace_id) or not _SAFE_ID.fullmatch(asset_id):
            raise ValueError("workspace_id or asset_id contains unsafe path characters")
        return self._root / workspace_id / f"{asset_id}.json"

    def _read(self, workspace_id: str, asset_id: str) -> dict[str, Any] | None:
        path = self._path(workspace_id, asset_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        if value.get("workspace_id") != workspace_id or value.get("asset_id") != asset_id:
            raise ValueError("invalid asset repository document")
        return value

    @staticmethod
    def _atomic_write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
