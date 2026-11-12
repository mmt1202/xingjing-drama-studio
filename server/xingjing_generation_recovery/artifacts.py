"""Import existing results without calling a model or trusting arbitrary file paths."""

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from PIL import Image

from server.xingjing_generation_runtime.artifact_storage import LocalGenerationArtifactStore

from .contracts import ProviderReceipt, RecoveryError


def inspect_file(path: Path, *, sha256: str, size: int, mime: str) -> dict[str, object]:
    try:
        digest, length = hashlib.sha256(), 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                length += len(chunk)
                if length > size:
                    raise RecoveryError("RECOVERY_ARTIFACT_CHANGED")
                digest.update(chunk)
        if length != size or digest.hexdigest() != sha256:
            raise RecoveryError("RECOVERY_ARTIFACT_CHANGED")
        if mime.startswith("image/"):
            with Image.open(path) as image:
                if image.format != {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}.get(mime):
                    raise RecoveryError("RECOVERY_ARTIFACT_INVALID")
                width, height = image.size
                image.verify()
            return {"mime_type": mime, "width": width, "height": height}
        probe = shutil.which("ffprobe")
        if not probe:
            raise RecoveryError("RECOVERY_MEDIA_PROBE_UNAVAILABLE", 503)
        completed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type:format=duration,format_name",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        data = json.loads(completed.stdout)
        formats = set(data["format"]["format_name"].split(","))
        expected = {"webm"} if mime == "video/webm" else {"mov", "mp4"}
        if not formats.intersection(expected):
            raise RecoveryError("RECOVERY_ARTIFACT_INVALID")
        duration = float(data["format"]["duration"])
        if not any(item.get("codec_type") == "video" for item in data["streams"]):
            raise RecoveryError("RECOVERY_ARTIFACT_INVALID")
        if not math.isfinite(duration) or duration <= 0:
            raise RecoveryError("RECOVERY_ARTIFACT_INVALID")
        return {"mime_type": mime, "duration_ms": max(1, round(duration * 1000))}
    except RecoveryError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        Image.DecompressionBombError,
        subprocess.SubprocessError,
    ) as error:
        raise RecoveryError("RECOVERY_ARTIFACT_INVALID") from error


async def import_outputs(
    store: LocalGenerationArtifactStore | None, root: Path | None, receipt: ProviderReceipt, *, media_type: str
) -> list[dict[str, object]]:
    if not receipt.outputs:
        return []
    if store is None or root is None:
        raise RecoveryError("RECOVERY_STORAGE_UNAVAILABLE", 503)
    result: list[dict[str, object]] = []
    for index, output in enumerate(receipt.outputs):
        if output.mime_type.split("/")[0] != media_type:
            raise RecoveryError("RECOVERY_ARTIFACT_INVALID")
        source = Path(output.source_path)
        try:
            if source.is_absolute() or ".." in source.parts:
                raise ValueError("relative result required")
            source = (root / source).resolve(strict=True)
            source.relative_to(root.resolve())
            if not source.is_file() or source.stat().st_size != output.size_bytes:
                raise RecoveryError("RECOVERY_ARTIFACT_CHANGED")
        except (OSError, ValueError) as error:
            raise RecoveryError("RECOVERY_ARTIFACT_INVALID") from error
        # Each import has its own object name; a changed source on replay cannot overwrite accepted evidence.
        stored = await store.import_project_file(
            project_root=root,
            workspace_id=receipt.workspace_id,
            project_id=receipt.project_id,
            task_id=receipt.task_id,
            asset_id=f"receipt-{uuid4()}",
            media_type=media_type,
            source_path=source,
        )
        if (
            stored.content_sha256 != output.sha256
            or stored.size_bytes != output.size_bytes
            or stored.mime_type != output.mime_type
        ):
            raise RecoveryError("RECOVERY_ARTIFACT_CHANGED")
        metadata = await asyncio.to_thread(
            inspect_file,
            store.resolve_object_key(stored.object_key),
            sha256=output.sha256,
            size=output.size_bytes,
            mime=output.mime_type,
        )
        result.append(
            {
                "asset_id": f"recovered-{receipt.receipt_id}-{index}",
                "workspace_id": receipt.workspace_id,
                "project_id": receipt.project_id,
                "task_id": receipt.task_id,
                "media_type": media_type,
                "object_key": stored.object_key,
                "content_sha256": stored.content_sha256,
                "size_bytes": stored.size_bytes,
                "metadata": {**metadata, "recovery_receipt_id": str(receipt.receipt_id)},
                "created_at": receipt.completed_at.isoformat(),
            }
        )
    return result


async def verify_outputs(store: LocalGenerationArtifactStore | None, artifacts: list[dict[str, object]]) -> None:
    if artifacts and store is None:
        raise RecoveryError("RECOVERY_STORAGE_UNAVAILABLE", 503)
    for artifact in artifacts:
        assert store is not None
        metadata = artifact["metadata"]
        if not isinstance(metadata, dict):
            raise RecoveryError("RECOVERY_ARTIFACT_INVALID")
        await asyncio.to_thread(
            inspect_file,
            store.resolve_object_key(str(artifact["object_key"])),
            sha256=str(artifact["content_sha256"]),
            size=int(str(artifact["size_bytes"])),
            mime=str(metadata["mime_type"]),
        )
