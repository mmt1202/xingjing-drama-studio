"""Resolve pinned catalog versions to verified, immutable generated media."""

from __future__ import annotations

import asyncio
import hashlib
import json

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_assets.contracts import RightsEvidence
from server.xingjing_assets_persistence.repository import AssetRow, AssetVersionRow, ReferenceRow, RightsRow
from server.xingjing_generation.contracts import GenerationRequest, ResolvedGenerationInput
from server.xingjing_generation_persistence.repository import GeneratedAssetRow, TaskRow
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence.persistence import ProjectRow

from .artifact_storage import GeneratedArtifactStorageError, LocalGenerationArtifactStore

MAX_INPUT_FILE_BYTES = 64 * 1024 * 1024
MAX_INPUT_TOTAL_BYTES = 128 * 1024 * 1024


def validate_input_media(request: GenerationRequest) -> None:
    """Operation input roles; provider/model limits remain owned by model routing."""
    if request.capability == "inpaint":
        raise ValueError("GENERATION_INPUT_ROLES_UNSUPPORTED")
    inputs = request.resolved_input_assets
    if request.input_asset_ids and tuple(item.asset_id for item in inputs) != request.input_asset_ids:
        raise ValueError("GENERATION_INPUT_ASSET_UNVERIFIED")
    if (
        any(item.size_bytes > MAX_INPUT_FILE_BYTES for item in inputs)
        or sum(item.size_bytes for item in inputs) > MAX_INPUT_TOTAL_BYTES
    ):
        raise ValueError("GENERATION_INPUT_TOO_LARGE")
    image_operations = {
        "image_to_video",
        "first_last_video",
        "image_edit",
        "image_upscale",
        "reference_fusion",
        "character_turnaround",
        "character_expressions",
    }
    video_operations = {"video_extend", "video_stabilize", "video_upscale"}
    required_kind = (
        "image"
        if request.capability in image_operations
        else "video"
        if request.capability in video_operations
        else None
    )
    if required_kind is not None:
        if not inputs:
            raise ValueError("GENERATION_INPUT_REQUIRED")
        if any(item.media_type != required_kind for item in inputs):
            raise ValueError("GENERATION_INPUT_MEDIA_TYPE_INVALID")
    if request.capability == "first_last_video" and len(inputs) != 2:
        raise ValueError("GENERATION_INPUT_COUNT_INVALID")
    if request.capability in {"image_to_video", "image_upscale", *video_operations} and len(inputs) != 1:
        raise ValueError("GENERATION_INPUT_COUNT_INVALID")


async def read_verified_input(store: LocalGenerationArtifactStore | None, item: ResolvedGenerationInput) -> bytes:
    if item.size_bytes > MAX_INPUT_FILE_BYTES:
        raise ValueError("GENERATION_INPUT_TOO_LARGE")
    if store is None:
        raise ValueError("GENERATION_INPUT_ASSET_MEDIA_NOT_FOUND")

    def read() -> bytes:
        try:
            path = store.resolve_object_key(item.object_key)
            if path.stat().st_size != item.size_bytes:
                raise ValueError("GENERATION_INPUT_ASSET_MEDIA_MISMATCH")
            with path.open("rb") as source:
                content = source.read(item.size_bytes + 1)
        except (OSError, GeneratedArtifactStorageError) as error:
            raise ValueError("GENERATION_INPUT_ASSET_MEDIA_NOT_FOUND") from error
        if len(content) != item.size_bytes or hashlib.sha256(content).hexdigest() != item.content_sha256:
            raise ValueError("GENERATION_INPUT_ASSET_MEDIA_MISMATCH")
        return content

    return await asyncio.to_thread(read)


class GenerationInputResolver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: LocalGenerationArtifactStore | None) -> None:
        self._sessions = sessions
        self._store = store

    async def resolve(
        self, context: TrustedWorkspaceContext, project_id: str, request: GenerationRequest
    ) -> GenerationRequest:
        if not request.input_asset_ids:
            validate_input_media(request)
            return request
        if len(set(request.input_asset_ids)) != len(request.input_asset_ids):
            raise ValueError("GENERATION_INPUT_ASSET_DUPLICATE")
        selectors = {item.asset_id: item for item in request.input_asset_references}
        resolved: list[ResolvedGenerationInput] = []
        async with self._sessions() as session:
            project = await session.scalar(
                select(ProjectRow.id).where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == context.tenant_id,
                    ProjectRow.workspace_id == context.workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
            )
            if project is None:
                raise ValueError("GENERATION_INPUT_ASSET_NOT_ACCESSIBLE")
            shared = select(ReferenceRow.asset_id).where(
                ReferenceRow.tenant_id == context.tenant_id,
                ReferenceRow.workspace_id == context.workspace_id,
                ReferenceRow.project_id == project_id,
            )
            for asset_id in request.input_asset_ids:
                catalog = await session.scalar(
                    select(AssetRow).where(
                        AssetRow.tenant_id == context.tenant_id,
                        AssetRow.workspace_id == context.workspace_id,
                        AssetRow.asset_id == asset_id,
                        or_(AssetRow.owner_project_id == project_id, AssetRow.asset_id.in_(shared)),
                    )
                )
                selector = selectors.get(asset_id)
                version = None
                generated_id = asset_id
                source_project_id = project_id
                if catalog is not None:
                    if catalog.owner_project_id != project_id:
                        rights = await session.scalars(
                            select(RightsRow.payload).where(
                                RightsRow.tenant_id == context.tenant_id,
                                RightsRow.workspace_id == context.workspace_id,
                                RightsRow.asset_id == asset_id,
                            )
                        )
                        if not any(RightsEvidence.from_dict(value).permits(project_id) for value in rights):
                            raise ValueError("GENERATION_INPUT_ASSET_NOT_ACCESSIBLE")
                    if selector is None:
                        raise ValueError("GENERATION_INPUT_ASSET_VERSION_REQUIRED")
                    version = await session.get(
                        AssetVersionRow,
                        (context.tenant_id, context.workspace_id, asset_id, selector.asset_version_id),
                    )
                    if version is None:
                        raise ValueError("GENERATION_INPUT_ASSET_VERSION_NOT_FOUND")
                    digest = hashlib.sha256(
                        json.dumps(version.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    if digest != version.content_sha256 or (
                        selector.asset_content_sha256 is not None and selector.asset_content_sha256 != digest
                    ):
                        raise ValueError("GENERATION_INPUT_ASSET_CONTENT_MISMATCH")
                    generated_id = version.content.get("generatedAssetId")
                    if not isinstance(generated_id, str) or not generated_id.strip():
                        raise ValueError("GENERATION_INPUT_ASSET_MEDIA_REQUIRED")
                    source_project_id = catalog.owner_project_id
                elif selector is not None:
                    raise ValueError("GENERATION_INPUT_ASSET_NOT_ACCESSIBLE")
                media = await session.scalar(
                    select(GeneratedAssetRow)
                    .join(ProjectRow, ProjectRow.id == GeneratedAssetRow.project_id)
                    .where(
                        GeneratedAssetRow.asset_id == generated_id,
                        GeneratedAssetRow.workspace_id == context.workspace_id,
                        GeneratedAssetRow.project_id == source_project_id,
                        ProjectRow.tenant_id == context.tenant_id,
                        ProjectRow.workspace_id == context.workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                )
                if media is None:
                    raise ValueError("GENERATION_INPUT_ASSET_MEDIA_NOT_FOUND")
                source_task = await session.get(TaskRow, (context.workspace_id, source_project_id, media.task_id))
                if (
                    source_task is None
                    or source_task.status != "succeeded"
                    or media.asset_id not in source_task.snapshot.get("output_asset_ids", ())
                ):
                    raise ValueError("GENERATION_INPUT_ASSET_MEDIA_NOT_FOUND")
                if version is not None and any(
                    expected is not None and expected != actual
                    for expected, actual in (
                        (version.content.get("generatedObjectKey"), media.object_key),
                        (version.content.get("generatedMediaType"), media.media_type),
                        (version.content.get("generationTaskId"), media.task_id),
                        (version.content.get("generatedContentSha256"), media.content_sha256),
                        (version.content.get("generatedSizeBytes"), media.size_bytes),
                    )
                ):
                    raise ValueError("GENERATION_INPUT_ASSET_MEDIA_MISMATCH")
                mime = media.artifact_metadata.get("mime_type")
                if not isinstance(mime, str) or not mime.startswith(f"{media.media_type}/"):
                    raise ValueError("GENERATION_INPUT_ASSET_MEDIA_MISMATCH")
                resolved.append(
                    ResolvedGenerationInput(
                        asset_id=asset_id,
                        asset_version_id=version.version_id if version else None,
                        asset_content_sha256=version.content_sha256 if version else None,
                        generated_asset_id=media.asset_id,
                        source_project_id=source_project_id,
                        source_task_id=media.task_id,
                        object_key=media.object_key,
                        media_type=media.media_type,  # pyright: ignore[reportArgumentType]
                        mime_type=mime,
                        content_sha256=media.content_sha256,
                        size_bytes=media.size_bytes,
                    )
                )
        pinned = tuple(resolved)
        if request.resolved_input_assets and pinned != request.resolved_input_assets:
            raise ValueError("GENERATION_INPUT_ASSET_MEDIA_MISMATCH")
        result = request.model_copy(update={"resolved_input_assets": pinned})
        validate_input_media(result)
        for item in pinned:
            await read_verified_input(self._store, item)
        return result
