from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from server.xingjing_assets_persistence import AssetScope, SqlAlchemyAssetRepository
from server.xingjing_content.parser import parse_script
from server.xingjing_content_persistence.repository import ScriptRow, ScriptVersionRow
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import ProjectRow
from server.xingjing_storyboard.errors import ContractViolation
from server.xingjing_storyboard.models import AssetKind as StoryboardAssetKind
from server.xingjing_storyboard.models import AssetReference
from server.xingjing_storyboard.ports import (
    AssetReferenceFailure,
    AssetReferenceValidator,
    AuditEvent,
    StoryboardRepository,
)
from server.xingjing_storyboard.service import AccessContext, ShotDraft, StoryboardService
from server.xingjing_storyboard_persistence import (
    SqlAlchemyPromptTemplateRepository,
    SqlAlchemySmartStoryboardRequestRepository,
    SqlAlchemyStoryboardRepository,
    SqlAlchemyStoryboardUploadRepository,
)
from server.xingjing_storyboard_storage import LocalStoryboardObjectStorage, ObjectNotFound

TrustedContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]
SessionFactory = Callable[[], Session]


class StoryboardRuntimeConfigurationError(RuntimeError):
    """生产分镜运行时缺少明确配置时的失败信号。"""


class _SmartShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_scene_index: int = Field(ge=1)
    shot_number: str = Field(min_length=1, max_length=64)
    duration_ms: int = Field(ge=500, le=120_000)
    dialogue: str = Field(max_length=20_000)
    shot_size: str = Field(min_length=1, max_length=128)
    camera_movement: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=12_000)
    cost_tier: str = Field(min_length=1, max_length=64)
    asset_ids: list[str] = Field(default_factory=list, max_length=64)


class _SmartStoryboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shots: list[_SmartShot] = Field(min_length=1, max_length=200)


def _validated_smart_drafts(
    output: _SmartStoryboard,
    *,
    scene_count: int,
    references: dict[str, AssetReference],
    provider: str,
    model: str,
) -> tuple[ShotDraft, ...]:
    expected_scenes = set(range(1, scene_count + 1))
    returned_scenes = {item.source_scene_index for item in output.shots}
    if returned_scenes != expected_scenes:
        raise ContractViolation(
            "SMART_STORYBOARD_SCENE_COVERAGE_INVALID",
            details={
                "missing": sorted(expected_scenes - returned_scenes),
                "unknown": sorted(returned_scenes - expected_scenes),
            },
        )
    shot_numbers = [item.shot_number.strip() for item in output.shots]
    if len(set(shot_numbers)) != len(shot_numbers):
        raise ContractViolation("SMART_STORYBOARD_DUPLICATE_SHOT_NUMBER")
    drafts: list[ShotDraft] = []
    for item in output.shots:
        unknown_assets = sorted(set(item.asset_ids) - references.keys())
        if unknown_assets:
            raise ContractViolation(
                "SMART_STORYBOARD_ASSET_REFERENCE_INVALID",
                details={"assetIds": unknown_assets},
            )
        drafts.append(
            ShotDraft(
                shot_number=item.shot_number.strip(),
                duration_ms=item.duration_ms,
                dialogue=item.dialogue,
                shot_size=item.shot_size,
                camera_movement=item.camera_movement,
                prompt=item.prompt,
                model_strategy=f"{provider}/{model}",
                cost_tier=item.cost_tier,
                asset_references=tuple(
                    references[asset_id] for asset_id in dict.fromkeys(item.asset_ids)
                ),
            )
        )
    return tuple(drafts)


class StoryboardRuntime:
    """组合 M05 领域服务、SQLAlchemy 仓储与可信身份上下文。

    此边界只接受平台会话解析器提供的可信上下文；客户端身份头不参与
    ``AccessContext`` 的构造。HTTP 路由在其他切片中挂载。
    """

    def __init__(
        self,
        *,
        repository: StoryboardRepository,
        asset_validator: AssetReferenceValidator,
        context_resolver: TrustedContextResolver,
        upload_store: ProductionStoryboardUploadStore,
        session_factory: SessionFactory,
        close: Callable[[], None] | None = None,
    ) -> None:
        self.service = StoryboardService(repository, asset_validator)
        self.repository = repository
        self._context_resolver = context_resolver
        self.upload_store = upload_store
        self.prompt_repository = SqlAlchemyPromptTemplateRepository(session_factory)
        self.smart_storyboard_requests = SqlAlchemySmartStoryboardRequestRepository(session_factory)
        self._session_factory = session_factory
        self._close = close

    async def access_context(self, request: Request, *, project_id: str) -> AccessContext:
        trusted = self._context_resolver(request)
        if inspect.isawaitable(trusted):
            trusted = await trusted
        return AccessContext(
            tenant_id=trusted.tenant_id,
            workspace_id=trusted.workspace_id,
            project_id=project_id,
            actor_id=trusted.actor_id,
            request_id=trusted.request_id,
            permissions=trusted.permissions,
        )

    @property
    def trusted_context_resolver(self) -> TrustedContextResolver:
        return self._context_resolver

    def project_scope_authorizer(self, trusted: TrustedWorkspaceContext, project_id: str) -> bool:
        """确认路径项目属于身份服务选定的工作区，失败时不得放行。"""
        try:
            with self._session_factory() as session:
                return (
                    session.scalar(
                        select(ProjectRow.id).where(
                            ProjectRow.id == project_id,
                            ProjectRow.tenant_id == trusted.tenant_id,
                            ProjectRow.workspace_id == trusted.workspace_id,
                            ProjectRow.deleted_at.is_(None),
                        )
                    )
                    is not None
                )
        except Exception as error:
            raise StoryboardRuntimeConfigurationError("PROJECT_SCOPE_AUTHORIZATION_UNAVAILABLE") from error

    async def generate_smart_drafts(
        self,
        context: AccessContext,
        script_version_id: str,
        asset_snapshot_id: str,
        idempotency_key: str,
    ) -> tuple[ShotDraft, ...]:
        """Use the configured text model to derive validated drafts from frozen inputs."""
        if "@" not in script_version_id:
            raise ContractViolation("FROZEN_SCRIPT_VERSION_ID_INVALID")
        script_id, number_text = script_version_id.rsplit("@", 1)
        if not script_id.strip() or not number_text.isdigit() or int(number_text) < 1:
            raise ContractViolation("FROZEN_SCRIPT_VERSION_ID_INVALID")
        if not asset_snapshot_id.strip():
            raise ContractViolation("ASSET_SNAPSHOT_ID_REQUIRED")
        version_number = int(number_text)
        try:
            with self._session_factory() as session:
                script = session.scalar(
                    select(ScriptRow).where(
                        ScriptRow.workspace_id == context.workspace_id,
                        ScriptRow.project_id == context.project_id,
                        ScriptRow.script_id == script_id,
                        ScriptRow.locked_version_number == version_number,
                    )
                )
                if script is None:
                    raise ContractViolation("FROZEN_SCRIPT_VERSION_NOT_FOUND")
                project = session.scalar(
                    select(ProjectRow).where(
                        ProjectRow.id == context.project_id,
                        ProjectRow.tenant_id == context.tenant_id,
                        ProjectRow.workspace_id == context.workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                )
                if project is None:
                    raise ContractViolation("PROJECT_NOT_FOUND")
                version = session.scalar(
                    select(ScriptVersionRow).where(
                        ScriptVersionRow.workspace_id == context.workspace_id,
                        ScriptVersionRow.script_id == script_id,
                        ScriptVersionRow.number == version_number,
                    )
                )
                if version is None:
                    raise ContractViolation("FROZEN_SCRIPT_VERSION_NOT_FOUND")
                parsed = parse_script(
                    version.source_content,
                    version_number=version_number,
                    change_summary="storyboard.smart_draft",
                )
                if parsed.validation_errors:
                    raise ContractViolation(
                        "FROZEN_SCRIPT_INVALID",
                        details={"issues": [item.code for item in parsed.validation_errors]},
                    )
                director_profile = script.director_profile
        except SQLAlchemyError as error:
            raise ContractViolation("SMART_STORYBOARD_SOURCE_UNAVAILABLE") from error

        asset_repository = SqlAlchemyAssetRepository(self._session_factory)
        scope = AssetScope(context.tenant_id, context.workspace_id)
        assets = []
        offset = 0
        while True:
            page = asset_repository.list_for_project(scope, context.project_id, limit=200, offset=offset)
            assets.extend(page)
            offset += len(page)
            if len(page) < 200:
                break
        snapshot_assets = tuple(item for item in assets if item.frozen_snapshot.snapshot_id == asset_snapshot_id)
        if not snapshot_assets:
            raise ContractViolation("ASSET_SNAPSHOT_NOT_FOUND")

        references = {
            item.asset_id: AssetReference(
                item.asset_id, item.current_version_id, StoryboardAssetKind(item.kind.value)
            )
            for item in snapshot_assets
        }
        if not parsed.scenes:
            raise ContractViolation("FROZEN_SCRIPT_HAS_NO_SCENES")
        prompt_input = {
            "directorProfile": director_profile,
            "scenes": [
                {
                    "index": index,
                    "heading": {
                        "location": scene.heading.location,
                        "timeOfDay": scene.heading.time_of_day,
                        "setting": scene.heading.setting,
                    },
                    "paragraphs": [
                        {"speaker": paragraph.speaker, "text": paragraph.text}
                        for paragraph in scene.paragraphs
                    ],
                }
                for index, scene in enumerate(parsed.scenes, 1)
            ],
            "assets": [
                {
                    "id": item.asset_id,
                    "name": item.name,
                    "kind": item.kind.value,
                    "versionId": item.current_version_id,
                }
                for item in snapshot_assets
            ],
        }
        fingerprint = sha256(
            json.dumps(
                {
                    "scriptVersionId": script_version_id,
                    "assetSnapshotId": asset_snapshot_id,
                    "input": prompt_input,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        claim = self.smart_storyboard_requests.claim(
            context.scope,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            now=datetime.now(UTC),
        )
        if claim.status == "succeeded":
            if claim.result_payload is None:
                raise ContractViolation("SMART_STORYBOARD_RESULT_MISSING")
            generated_provider = str(claim.result_payload.get("provider") or "replayed")
            generated_model = str(claim.result_payload.get("model") or "replayed")
            output = _SmartStoryboard.model_validate(
                {
                    key: value
                    for key, value in claim.result_payload.items()
                    if key not in {"provider", "model"}
                }
            )
        elif not claim.claimed:
            raise ContractViolation("SMART_STORYBOARD_REQUEST_IN_PROGRESS")
        else:
            try:
                generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=project.name)
                generated = await generator.generate(
                    TextGenerationRequest(
                        system_prompt=(
                            "你是影视分镜导演。只能使用输入中给出的场景与资产，"
                            "输出严格符合 JSON Schema 的可执行镜头列表。每个输入场景至少一个镜头，"
                            "asset_ids 只能引用输入资产 ID，不得虚构资产或媒体结果。"
                        ),
                        prompt=(
                            "根据以下冻结剧本、导演配置和资产快照生成分镜草案。"
                            "镜头编号必须唯一，提示词应能直接供图片或视频模型使用。\n"
                            + json.dumps(prompt_input, ensure_ascii=False, separators=(",", ":"))
                        ),
                        response_schema=_SmartStoryboard,
                        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                    ),
                    project_name=project.name,
                )
                output = _SmartStoryboard.model_validate_json(strip_json_code_fences(generated.text))
                generated_provider = generated.provider
                generated_model = generated.model
            except ContractViolation:
                raise
            except Exception as error:
                self.smart_storyboard_requests.fail(
                    context.scope,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    failure_reason=str(error),
                    now=datetime.now(UTC),
                )
                raise ContractViolation(
                    "SMART_STORYBOARD_MODEL_FAILED",
                    details={"reason": str(error)[:500]},
                ) from error

        try:
            drafts = _validated_smart_drafts(
                output,
                scene_count=len(parsed.scenes),
                references=references,
                provider=generated_provider,
                model=generated_model,
            )
        except ContractViolation as error:
            if claim.claimed:
                self.smart_storyboard_requests.fail(
                    context.scope,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    failure_reason=str(error),
                    now=datetime.now(UTC),
                )
            raise
        if claim.claimed:
            stored_result = output.model_dump(mode="json")
            stored_result["provider"] = generated_provider
            stored_result["model"] = generated_model
            self.smart_storyboard_requests.succeed(
                context.scope,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                result_payload=stored_result,
                now=datetime.now(UTC),
            )
        return drafts

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None


class PersistentAssetReferenceValidator:
    """Fails closed against the M04 SQLAlchemy asset source of truth."""

    def __init__(self, repository: SqlAlchemyAssetRepository) -> None:
        self._repository = repository

    def validate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        references: tuple[AssetReference, ...],
    ) -> tuple[AssetReferenceFailure, ...]:
        try:
            scope = AssetScope(tenant_id, workspace_id)
            failures: list[AssetReferenceFailure] = []
            for reference in references:
                asset = self._repository.get(scope, reference.asset_id)
                if asset is None:
                    failures.append(AssetReferenceFailure(reference.asset_id, reference.asset_version_id, "ASSET_NOT_FOUND"))
                elif not any(version.version_id == reference.asset_version_id for version in asset.versions):
                    failures.append(AssetReferenceFailure(reference.asset_id, reference.asset_version_id, "ASSET_VERSION_NOT_FOUND"))
                elif asset.owner_project_id != project_id and not any(right.permits(project_id) for right in asset.rights):
                    failures.append(AssetReferenceFailure(reference.asset_id, reference.asset_version_id, "ASSET_NOT_AUTHORIZED"))
            return tuple(failures)
        except Exception as error:
            raise ContractViolation("ASSET_REFERENCE_VALIDATION_UNAVAILABLE") from error


@dataclass(frozen=True, slots=True)
class StoredUpload:
    upload_id: str
    filename: str
    content: bytes


class ProductionStoryboardUploadStore:
    """Coordinates object bytes with persistent M05 upload metadata."""

    def __init__(self, repository: SqlAlchemyStoryboardUploadRepository, storage: LocalStoryboardObjectStorage) -> None:
        self._repository = repository
        self._storage = storage

    def create(
        self,
        context: AccessContext,
        *,
        filename: str,
        media_type: str,
        size_bytes: int,
        content_sha256: str,
        idempotency_key: str,
    ) -> StoredUpload:
        self._require_idempotency_key(idempotency_key)
        upload_id = f"upload-{uuid4()}"
        object_key = f"imports/{upload_id}/content"
        now = datetime.now(UTC)
        metadata, replayed = self._repository.create(
            context.scope,
            upload_id=upload_id,
            object_key=object_key,
            filename=filename,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=content_sha256,
            source="storyboard_import",
            lifecycle="manual",
            idempotency_key=idempotency_key,
            fingerprint=self._fingerprint("create", filename, media_type, str(size_bytes), content_sha256),
            created_at=now,
            audit=self._audit(context, "storyboard.upload_created", upload_id, now),
        )
        if not replayed:
            self._storage.put_bytes(**self._scope(metadata), key=metadata.object_key, content=b"")
        return StoredUpload(metadata.upload_id, metadata.filename, b"")

    def put(self, context: AccessContext, upload_id: str, content: bytes) -> bool:
        metadata = self._repository.get(context.scope, upload_id)
        if metadata is None or metadata.status.value != "pending":
            return False
        if len(content) != metadata.size_bytes or sha256(content).hexdigest() != metadata.sha256:
            raise ContractViolation("UPLOAD_CONTENT_MISMATCH")
        self._storage.put_bytes(**self._scope(metadata), key=metadata.object_key, content=content)
        return True

    def complete(self, context: AccessContext, upload_id: str, *, idempotency_key: str) -> bool:
        self._require_idempotency_key(idempotency_key)
        metadata = self._repository.get(context.scope, upload_id)
        if metadata is None:
            return False
        try:
            stored = self._storage.get_bytes(**self._scope(metadata), key=metadata.object_key)
        except ObjectNotFound:
            return False
        now = datetime.now(UTC)
        self._repository.complete(
            context.scope,
            upload_id,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            idempotency_key=idempotency_key,
            fingerprint=self._fingerprint("complete", upload_id, stored.sha256),
            completed_at=now,
            audit=self._audit(context, "storyboard.upload_completed", upload_id, now),
        )
        return True

    def get(self, context: AccessContext, upload_id: str) -> StoredUpload | None:
        metadata = self._repository.get(context.scope, upload_id)
        if metadata is None or metadata.status.value not in {"completed", "consumed"}:
            return None
        try:
            stored = self._storage.get_bytes(**self._scope(metadata), key=metadata.object_key)
        except ObjectNotFound:
            return None
        if stored.size_bytes != metadata.size_bytes or stored.sha256 != metadata.sha256:
            raise ContractViolation("UPLOAD_OBJECT_INTEGRITY_MISMATCH")
        return StoredUpload(metadata.upload_id, metadata.filename, stored.content)

    def put_export(self, context: AccessContext, content: bytes, media_type: str, filename: str) -> str:
        export_id = sha256(content).hexdigest()
        scope = self._scope_from_context(context)
        self._storage.put_bytes(**scope, key=f"exports/{export_id}/content", content=content)
        self._storage.put_json(
            **scope,
            key=f"exports/{export_id}/metadata.json",
            value={"media_type": media_type, "filename": filename},
        )
        return export_id

    def get_export(self, context: AccessContext, export_id: str) -> tuple[bytes, str, str] | None:
        scope = self._scope_from_context(context)
        try:
            content = self._storage.get_bytes(**scope, key=f"exports/{export_id}/content").content
            metadata = self._storage.get_json(**scope, key=f"exports/{export_id}/metadata.json")
        except (ObjectNotFound, ValueError):
            return None
        if sha256(content).hexdigest() != export_id:
            return None
        return content, metadata["media_type"], metadata["filename"]

    @staticmethod
    def _scope(metadata: object) -> dict[str, str]:
        return {
            "tenant_id": getattr(metadata, "tenant_id"),
            "workspace_id": getattr(metadata, "workspace_id"),
            "project_id": getattr(metadata, "project_id"),
        }

    @staticmethod
    def _scope_from_context(context: AccessContext) -> dict[str, str]:
        return {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "project_id": context.project_id}

    @staticmethod
    def _fingerprint(operation: str, *parts: str) -> str:
        return sha256("\0".join((operation, *parts)).encode()).hexdigest()

    @staticmethod
    def _require_idempotency_key(value: str) -> None:
        if not value.strip():
            raise ContractViolation("IDEMPOTENCY_KEY_REQUIRED")

    @staticmethod
    def _audit(context: AccessContext, action: str, object_id: str, occurred_at: datetime) -> AuditEvent:
        return AuditEvent(
            context.tenant_id,
            context.workspace_id,
            context.project_id,
            context.request_id,
            context.actor_id,
            action,
            object_id,
            "succeeded",
            occurred_at,
            {},
            {},
        )


def create_production_storyboard_runtime(
    asset_validator: AssetReferenceValidator | None = None,
    *,
    database_url: str | None = None,
    context_resolver: TrustedContextResolver | None = None,
    storage_root: str | None = None,
) -> StoryboardRuntime:
    """创建生产组合，不创建 schema，也不回退到内存或文件仓储。"""
    url = (database_url or os.environ.get("XINGJING_STORYBOARD_DATABASE_URL", "")).strip()
    if not url:
        raise StoryboardRuntimeConfigurationError("XINGJING_STORYBOARD_DATABASE_URL_REQUIRED")
    if "+aiosqlite" in url or "+asyncpg" in url:
        raise StoryboardRuntimeConfigurationError("XINGJING_STORYBOARD_DATABASE_URL_MUST_BE_SYNC")

    root = (storage_root or os.environ.get("XINGJING_STORYBOARD_STORAGE_ROOT", "")).strip()
    if not root:
        raise StoryboardRuntimeConfigurationError("XINGJING_STORYBOARD_STORAGE_ROOT_REQUIRED")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        storage = LocalStoryboardObjectStorage(root)
    except (ModuleNotFoundError, OSError, SQLAlchemyError, ValueError) as error:
        raise StoryboardRuntimeConfigurationError("STORYBOARD_RUNTIME_DEPENDENCY_UNAVAILABLE") from error

    factory: SessionFactory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlAlchemyStoryboardRepository(factory)
    uploads = SqlAlchemyStoryboardUploadRepository(factory)
    validator = asset_validator or PersistentAssetReferenceValidator(SqlAlchemyAssetRepository(factory))
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    return StoryboardRuntime(
        repository=repository,
        asset_validator=validator,
        context_resolver=resolver,
        upload_store=ProductionStoryboardUploadStore(uploads, storage),
        session_factory=factory,
        close=engine.dispose,
    )
