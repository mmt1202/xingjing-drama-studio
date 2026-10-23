"""Read immutable release selections and verify the bytes embedded in delivery ZIPs."""

from __future__ import annotations

import hashlib
import json
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_compliance_runtime.contracts import ExportAuthorityEvidence
from server.xingjing_cover.models import CoverSelectionRow
from server.xingjing_editing import FinalVideoVersion
from server.xingjing_editing.contracts import TimelineVersion
from server.xingjing_generation_persistence.repository import GeneratedAssetRow, TaskRow

PACKAGE_FORMATS = frozenset({"material_package", "project_archive", "jianying_draft", "publish_package"})
_MIME_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MAX_COVER_BYTES = 32 * 1024 * 1024


class ReleaseCreativeError(ValueError):
    pass


def creative_digest(snapshot: dict[str, object]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    )


def provider_review_facts(
    version: FinalVideoVersion,
    target: str,
    snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    facts: dict[str, object] = {
        "exportTarget": target,
        "objectKey": version.output.object_key,
        "contentSha256": version.output.content_sha256,
        "sizeBytes": version.output.size_bytes,
        "durationMs": version.output.duration_ms,
        "timelineId": version.timeline_id,
        "timelineVersionId": version.timeline_version_id,
        "container": version.profile.container,
        "videoCodec": version.profile.video_codec,
        "audioCodec": version.profile.audio_codec,
        "width": version.profile.width,
        "height": version.profile.height,
    }
    if snapshot is not None:
        facts["releaseCreative"] = snapshot
        facts["releaseCreativeDigest"] = creative_digest(snapshot)
    return facts


def review_input_digest(facts: dict[str, object]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assert_creative_review(
    evidence: ExportAuthorityEvidence,
    version: FinalVideoVersion,
    target: str,
    snapshot: dict[str, object],
) -> None:
    expected = creative_digest(snapshot)
    if (
        evidence.release_creative_digest != expected
        or evidence.reviewed_creative_digest != expected
        or evidence.input_digest != review_input_digest(provider_review_facts(version, target, snapshot))
    ):
        raise ReleaseCreativeError("COVER_COMPLIANCE_REVIEW_REQUIRED")


def creative_review_summary(evidence: ExportAuthorityEvidence) -> dict[str, object]:
    return {
        "inputDigest": evidence.input_digest,
        "reviewedCreativeDigest": evidence.reviewed_creative_digest,
        "manualEvaluation": "editorial-only-not-compliance-approval",
        "generationProvenance": "source-record-not-license-grant",
        "coverTitleLayout": "metadata-not-baked-into-image",
    }


async def load_release_creative(
    sessions: async_sessionmaker[AsyncSession],
    *,
    version: FinalVideoVersion,
    timeline: TimelineVersion,
    target: str,
    selection_id: str | None,
) -> dict[str, object] | None:
    async with sessions() as session:
        connection = await session.connection()
        available = await connection.run_sync(lambda sync: inspect(sync).has_table(CoverSelectionRow.__tablename__))
        if not available:
            if selection_id is not None:
                raise ReleaseCreativeError("FORMAL_EXPORT_COVER_SELECTION_NOT_FOUND")
            return None
        statement = select(CoverSelectionRow).where(
            CoverSelectionRow.tenant_id == version.tenant_id,
            CoverSelectionRow.workspace_id == version.workspace_id,
            CoverSelectionRow.project_id == version.project_id,
            CoverSelectionRow.final_version_id == version.version_id,
            CoverSelectionRow.episode_id == timeline.episode_id,
            CoverSelectionRow.target == target,
        )
        if selection_id is not None:
            statement = statement.where(CoverSelectionRow.id == selection_id)
        row = await session.scalar(
            statement.order_by(CoverSelectionRow.created_at.desc(), CoverSelectionRow.id.desc()).limit(1)
        )
        if row is None:
            if selection_id is not None:
                raise ReleaseCreativeError("FORMAL_EXPORT_COVER_SELECTION_NOT_FOUND")
            return None
        snapshot: dict[str, object] = json.loads(json.dumps(row.snapshot))
        expected = {
            "schema": "xingjing.release-creative/v1",
            "selectionId": row.id,
            "planId": row.plan_id,
            "planVersion": row.plan_version,
            "variantId": row.variant_id,
            "finalVersionId": version.version_id,
            "episodeId": timeline.episode_id,
            "target": target,
        }
        if any(snapshot.get(key) != value for key, value in expected.items()):
            raise ReleaseCreativeError("FORMAL_EXPORT_COVER_SELECTION_SCOPE_MISMATCH")
        cover = snapshot.get("cover")
        if not isinstance(cover, dict):
            raise ReleaseCreativeError("FORMAL_EXPORT_COVER_METADATA_INVALID")
        asset = await session.scalar(
            select(GeneratedAssetRow)
            .join(
                TaskRow,
                (TaskRow.workspace_id == GeneratedAssetRow.workspace_id)
                & (TaskRow.project_id == GeneratedAssetRow.project_id)
                & (TaskRow.task_id == GeneratedAssetRow.task_id),
            )
            .where(
                GeneratedAssetRow.workspace_id == version.workspace_id,
                GeneratedAssetRow.project_id == version.project_id,
                GeneratedAssetRow.asset_id == cover.get("assetId"),
                GeneratedAssetRow.task_id == cover.get("taskId"),
                GeneratedAssetRow.media_type == "image",
                TaskRow.status == "succeeded",
            )
        )
        if asset is None or any(
            cover.get(key) != value
            for key, value in {
                "objectKey": asset.object_key,
                "contentSha256": asset.content_sha256,
                "sizeBytes": asset.size_bytes,
            }.items()
        ):
            raise ReleaseCreativeError("FORMAL_EXPORT_COVER_SOURCE_MISMATCH")
    return snapshot


def verified_cover_bytes(
    snapshot: dict[str, object],
    *,
    root: Path | None,
    workspace_id: str,
    project_id: str,
) -> tuple[bytes, str]:
    if root is None:
        raise ReleaseCreativeError("FORMAL_EXPORT_COVER_STORAGE_NOT_CONFIGURED")
    cover = snapshot.get("cover")
    if not isinstance(cover, dict):
        raise ReleaseCreativeError("FORMAL_EXPORT_COVER_METADATA_INVALID")
    key = cover.get("objectKey")
    if not isinstance(key, str) or "\\" in key:
        raise ReleaseCreativeError("FORMAL_EXPORT_COVER_OBJECT_SCOPE_DENIED")
    parts = key.split("/")
    task_id, asset_id = cover.get("taskId"), cover.get("assetId")
    if (
        len(parts) != 4
        or parts[:3] != [workspace_id, project_id, task_id]
        or any(part in {"", ".", ".."} for part in parts)
        or Path(parts[-1]).stem != asset_id
    ):
        raise ReleaseCreativeError("FORMAL_EXPORT_COVER_OBJECT_SCOPE_DENIED")
    expected_size = cover.get("sizeBytes")
    if type(expected_size) is not int or not 0 < expected_size <= _MAX_COVER_BYTES:
        raise ReleaseCreativeError("FORMAL_EXPORT_COVER_METADATA_INVALID")
    scoped_root = (root / workspace_id / project_id).resolve()
    source = (root / key).resolve()
    if not source.is_relative_to(scoped_root):
        raise ReleaseCreativeError("FORMAL_EXPORT_COVER_OBJECT_SCOPE_DENIED")
    try:
        with source.open("rb") as content:
            payload = content.read(_MAX_COVER_BYTES + 1)
        if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != cover.get("contentSha256"):
            raise ReleaseCreativeError("FORMAL_EXPORT_COVER_DIGEST_MISMATCH")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format or "")
                if (
                    mime not in _MIME_EXTENSIONS
                    or image.width * image.height > 40_000_000
                    or image.width != cover.get("width")
                    or image.height != cover.get("height")
                    or mime != cover.get("mimeType")
                ):
                    raise ReleaseCreativeError("FORMAL_EXPORT_COVER_METADATA_MISMATCH")
                image.verify()
        return payload, f"media/cover.{_MIME_EXTENSIONS[mime]}"
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ReleaseCreativeError("FORMAL_EXPORT_COVER_MEDIA_UNAVAILABLE") from error
