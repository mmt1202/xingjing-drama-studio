from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from server.xingjing_editing_storage import (
    LocalRenderObjectStorage,
    MediaDurationUnavailable,
    MediaProbeFailed,
    ObjectStorageConfigurationError,
    ObjectStorageKeyError,
)


class ProbeStub:
    def __init__(self, duration_ms: int | None = 4_250) -> None:
        self.duration_ms = duration_ms
        self.paths: list[Path] = []

    async def probe_duration_ms(self, path: Path) -> int | None:
        self.paths.append(path)
        return self.duration_ms


class FailingProbe:
    async def probe_duration_ms(self, path: Path) -> int | None:
        del path
        raise RuntimeError("ffprobe exited unexpectedly")


@pytest.mark.asyncio
async def test_stat_reads_real_file_metadata_within_tenant_workspace_scope(tmp_path: Path) -> None:
    root = tmp_path / "render-objects"
    output = root / "tenant-1" / "workspace-1" / "renders" / "output.mp4"
    output.parent.mkdir(parents=True)
    content = b"real rendered bytes"
    output.write_bytes(content)
    probe = ProbeStub()
    storage = LocalRenderObjectStorage(root=root, media_probe=probe)

    result = await storage.stat(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        object_key="tenant-1/workspace-1/renders/output.mp4",
    )

    assert result is not None
    assert result.object_key == "tenant-1/workspace-1/renders/output.mp4"
    assert result.size_bytes == len(content)
    assert result.content_sha256 == hashlib.sha256(content).hexdigest()
    assert result.duration_ms == 4_250
    assert probe.paths == [output]


@pytest.mark.asyncio
async def test_stat_returns_none_when_scoped_object_does_not_exist(tmp_path: Path) -> None:
    root = tmp_path / "render-objects"
    root.mkdir()
    storage = LocalRenderObjectStorage(root=root, media_probe=ProbeStub())

    result = await storage.stat(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        object_key="tenant-1/workspace-1/renders/missing.mp4",
    )

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "object_key",
    [
        "tenant-2/workspace-1/renders/output.mp4",
        "tenant-1/workspace-2/renders/output.mp4",
        "tenant-1/workspace-1/../other/output.mp4",
        "/tenant-1/workspace-1/renders/output.mp4",
        "tenant-1\\workspace-1\\renders\\output.mp4",
    ],
)
async def test_stat_rejects_wrong_scope_and_path_traversal(tmp_path: Path, object_key: str) -> None:
    root = tmp_path / "render-objects"
    root.mkdir()
    storage = LocalRenderObjectStorage(root=root, media_probe=ProbeStub())

    with pytest.raises(ObjectStorageKeyError):
        await storage.stat(tenant_id="tenant-1", workspace_id="workspace-1", object_key=object_key)


@pytest.mark.asyncio
async def test_stat_rejects_a_workspace_directory_symlinked_outside_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "render-objects"
    root.mkdir()
    outside = tmp_path / "outside"
    output = outside / "renders" / "output.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"must not be reachable")
    workspace_path = root / "tenant-1" / "workspace-1"
    workspace_path.parent.mkdir()
    try:
        workspace_path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建目录符号链接")
    storage = LocalRenderObjectStorage(root=root, media_probe=ProbeStub())

    with pytest.raises(ObjectStorageKeyError):
        await storage.stat(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            object_key="tenant-1/workspace-1/renders/output.mp4",
        )


@pytest.mark.asyncio
async def test_stat_rejects_unknown_duration_instead_of_fabricating_zero(tmp_path: Path) -> None:
    root = tmp_path / "render-objects"
    output = root / "tenant-1" / "workspace-1" / "renders" / "output.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"rendered bytes")
    storage = LocalRenderObjectStorage(root=root, media_probe=ProbeStub(duration_ms=None))

    with pytest.raises(MediaDurationUnavailable) as exc_info:
        await storage.stat(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            object_key="tenant-1/workspace-1/renders/output.mp4",
        )

    assert exc_info.value.code == "MEDIA_DURATION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_stat_reports_media_probe_failures_with_a_stable_error_code(tmp_path: Path) -> None:
    root = tmp_path / "render-objects"
    output = root / "tenant-1" / "workspace-1" / "renders" / "output.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"rendered bytes")
    storage = LocalRenderObjectStorage(root=root, media_probe=FailingProbe())

    with pytest.raises(MediaProbeFailed) as exc_info:
        await storage.stat(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            object_key="tenant-1/workspace-1/renders/output.mp4",
        )

    assert exc_info.value.code == "MEDIA_PROBE_FAILED"


def test_constructor_requires_an_existing_directory_for_the_storage_root(tmp_path: Path) -> None:
    with pytest.raises(ObjectStorageConfigurationError):
        LocalRenderObjectStorage(root=tmp_path / "missing", media_probe=ProbeStub())
