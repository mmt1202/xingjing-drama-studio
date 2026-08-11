"""Production-only M04 composition: no memory and no file repository fallback."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Request
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_assets_http import create_asset_router, create_unavailable_asset_router
from server.xingjing_assets_persistence import SqlAlchemyAssetRepository
from server.xingjing_content_persistence.repository import ScriptRow, ScriptVersionRow
from server.xingjing_generation import GenerationRequest, GenerationTask
from server.xingjing_generation_persistence import GeneratedAsset
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import ProjectRow
from server.xingjing_storyboard_persistence import SqlAlchemyStoryboardRepository

SessionFactory = Callable[[], Session]
TrustedContextResolver = Callable[[Request], Awaitable[TrustedWorkspaceContext]]
GenerationSubmitter = Callable[..., Awaitable[GenerationTask]]
GenerationAssetResolver = Callable[..., Awaitable[tuple[GeneratedAsset, str]]]
GenerationTaskResolver = Callable[..., Awaitable[GenerationTask]]


class LocalAssetEvidenceStorage:
    """Scoped, durable evidence storage used before an S3/OSS adapter is supplied."""

    _SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            raise ValueError("ASSET_EVIDENCE_ROOT_MUST_BE_ABSOLUTE")
        self._root = candidate.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, context: TrustedWorkspaceContext, project_id: str, content: bytes) -> tuple[str, str]:
        if not content:
            raise ValueError("RIGHTS_EVIDENCE_EMPTY")
        digest = hashlib.sha256(content).hexdigest()
        key = f"rights/{digest}/content"
        target = self._path(context, project_id, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return key, digest

    def verify(self, context: TrustedWorkspaceContext, project_id: str, key: str, expected_sha256: str) -> bool:
        if not re.fullmatch(r"rights/[0-9a-f]{64}/content", key) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            return False
        try:
            actual = hashlib.sha256(self._path(context, project_id, key).read_bytes()).hexdigest()
        except FileNotFoundError:
            return False
        return actual == expected_sha256

    def _path(self, context: TrustedWorkspaceContext, project_id: str, key: str) -> Path:
        segments = (context.tenant_id, context.workspace_id, project_id)
        if any(not self._SAFE_SEGMENT.fullmatch(value) for value in segments):
            raise ValueError("INVALID_OBJECT_STORAGE_PATH")
        candidate = (self._root / context.tenant_id / context.workspace_id / project_id / key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("INVALID_OBJECT_STORAGE_PATH") from error
        return candidate


class AssetRuntimeConfigurationError(RuntimeError):
    """Raised when M04 cannot use its required production dependencies."""


class AssetRuntime:
    def __init__(
        self,
        *,
        repository: SqlAlchemyAssetRepository | None,
        storyboards: SqlAlchemyStoryboardRepository | None,
        session_factory: SessionFactory | None,
        context_resolver: TrustedContextResolver | None,
        generation_submitter: GenerationSubmitter | None = None,
        generation_asset_resolver: GenerationAssetResolver | None = None,
        generation_task_resolver: GenerationTaskResolver | None = None,
        evidence_storage: LocalAssetEvidenceStorage | None = None,
        unavailable_code: str | None = None,
        close: Callable[[], None] | None = None,
    ) -> None:
        self.repository = repository
        self.storyboards = storyboards
        self.session_factory = session_factory
        self.context_resolver = context_resolver
        self._generation_submitter = generation_submitter
        self._generation_asset_resolver = generation_asset_resolver
        self._generation_task_resolver = generation_task_resolver
        self._evidence_storage = evidence_storage
        self.unavailable_code = unavailable_code
        self._close = close
        self._router = create_unavailable_asset_router(unavailable_code) if unavailable_code else create_asset_router(self)

    @property
    def available(self) -> bool:
        return self.unavailable_code is None

    def router(self) -> APIRouter:
        return self._router

    async def resolve_context(self, request: Request, project_id: str) -> TrustedWorkspaceContext:
        if self.unavailable_code or self.context_resolver is None or self.session_factory is None:
            raise AssetRuntimeConfigurationError(self.unavailable_code or "ASSET_RUNTIME_UNAVAILABLE")
        context = await self.context_resolver(request)
        if not await asyncio.to_thread(self._project_exists, context, project_id):
            raise LookupError("PROJECT_NOT_FOUND")
        return context

    async def resolve_workspace_context(self, request: Request, workspace_id: str) -> TrustedWorkspaceContext:
        """Resolve an active trusted workspace for workspace-wide asset reads."""
        if self.unavailable_code or self.context_resolver is None:
            raise AssetRuntimeConfigurationError(self.unavailable_code or "ASSET_RUNTIME_UNAVAILABLE")
        context = await self.context_resolver(request)
        if context.workspace_id != workspace_id:
            raise LookupError("WORKSPACE_NOT_FOUND")
        return context

    async def submit_generation(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        request: GenerationRequest,
        *,
        idempotency_key: str,
    ) -> GenerationTask:
        if self._generation_submitter is None:
            raise AssetRuntimeConfigurationError("ASSET_GENERATION_SUBMISSION_UNAVAILABLE")
        return await self._generation_submitter(
            context, project_id, request, idempotency_key=idempotency_key
        )

    async def resolve_generated_asset(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        generated_asset_id: str,
        source_asset_id: str,
    ) -> GeneratedAsset:
        if self._generation_asset_resolver is None or self._generation_task_resolver is None:
            raise AssetRuntimeConfigurationError("ASSET_GENERATION_OUTPUT_UNAVAILABLE")
        asset, _path = await self._generation_asset_resolver(context, project_id, generated_asset_id)
        task = await self._generation_task_resolver(context, project_id, asset.task_id)
        if source_asset_id not in task.request.input_asset_ids or generated_asset_id not in task.output_asset_ids:
            raise LookupError("GENERATED_ASSET_NOT_FOUND")
        return asset

    def store_rights_evidence(self, context: TrustedWorkspaceContext, project_id: str, content: bytes) -> tuple[str, str]:
        if self._evidence_storage is None:
            raise AssetRuntimeConfigurationError("ASSET_EVIDENCE_STORAGE_UNAVAILABLE")
        return self._evidence_storage.put(context, project_id, content)

    def verify_rights_evidence(self, context: TrustedWorkspaceContext, project_id: str, key: str, sha256: str) -> bool:
        return self._evidence_storage is not None and self._evidence_storage.verify(context, project_id, key, sha256)

    def _project_exists(self, context: TrustedWorkspaceContext, project_id: str) -> bool:
        assert self.session_factory is not None
        try:
            with self.session_factory() as session:
                return session.scalar(select(ProjectRow.id).where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == context.tenant_id,
                    ProjectRow.workspace_id == context.workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )) is not None
        except SQLAlchemyError as error:
            raise AssetRuntimeConfigurationError("ASSET_RUNTIME_UNAVAILABLE") from error

    def frozen_script(self, context: TrustedWorkspaceContext, project_id: str, snapshot_id: str) -> tuple[str, int, str]:
        """Resolve the stable M03 ``script-id@version`` snapshot reference.

        The version must still be the document's locked version; a client cannot
        turn a draft into an asset source by naming it in the request.
        """
        if "@" not in snapshot_id:
            raise ValueError("FROZEN_SCRIPT_VERSION_ID_INVALID")
        script_id, number_text = snapshot_id.rsplit("@", 1)
        if not script_id.strip() or not number_text.isdigit() or int(number_text) < 1:
            raise ValueError("FROZEN_SCRIPT_VERSION_ID_INVALID")
        assert self.session_factory is not None
        try:
            with self.session_factory() as session:
                script = session.scalar(select(ScriptRow).where(
                    ScriptRow.workspace_id == context.workspace_id,
                    ScriptRow.project_id == project_id,
                    ScriptRow.script_id == script_id,
                    ScriptRow.locked_version_number == int(number_text),
                ))
                if script is None:
                    raise LookupError("FROZEN_SCRIPT_VERSION_NOT_FOUND")
                version = session.scalar(select(ScriptVersionRow).where(
                    ScriptVersionRow.workspace_id == context.workspace_id,
                    ScriptVersionRow.script_id == script.script_id,
                    ScriptVersionRow.number == int(number_text),
                ))
                if version is None:
                    raise LookupError("FROZEN_SCRIPT_VERSION_NOT_FOUND")
                import hashlib
                return script.script_id, int(number_text), hashlib.sha256(version.source_content.encode("utf-8")).hexdigest()
        except SQLAlchemyError as error:
            raise AssetRuntimeConfigurationError("ASSET_DEPENDENCY_UNAVAILABLE") from error

    def frozen_script_source(self, context: TrustedWorkspaceContext, project_id: str, snapshot_id: str) -> str:
        """Return the content of the already-validated, frozen M03 version."""
        if "@" not in snapshot_id:
            raise ValueError("FROZEN_SCRIPT_VERSION_ID_INVALID")
        script_id, number_text = snapshot_id.rsplit("@", 1)
        if not script_id.strip() or not number_text.isdigit() or int(number_text) < 1:
            raise ValueError("FROZEN_SCRIPT_VERSION_ID_INVALID")
        assert self.session_factory is not None
        try:
            with self.session_factory() as session:
                script = session.scalar(select(ScriptRow).where(
                    ScriptRow.workspace_id == context.workspace_id,
                    ScriptRow.project_id == project_id,
                    ScriptRow.script_id == script_id,
                    ScriptRow.locked_version_number == int(number_text),
                ))
                if script is None:
                    raise LookupError("FROZEN_SCRIPT_VERSION_NOT_FOUND")
                version = session.scalar(select(ScriptVersionRow).where(
                    ScriptVersionRow.workspace_id == context.workspace_id,
                    ScriptVersionRow.script_id == script.script_id,
                    ScriptVersionRow.number == int(number_text),
                ))
                if version is None:
                    raise LookupError("FROZEN_SCRIPT_VERSION_NOT_FOUND")
                return version.source_content
        except SQLAlchemyError as error:
            raise AssetRuntimeConfigurationError("ASSET_DEPENDENCY_UNAVAILABLE") from error

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None


def create_production_asset_runtime(
    *,
    database_url: str | None = None,
    context_resolver: TrustedContextResolver | None = None,
    generation_submitter: GenerationSubmitter | None = None,
    generation_asset_resolver: GenerationAssetResolver | None = None,
    generation_task_resolver: GenerationTaskResolver | None = None,
    evidence_storage_root: str | None = None,
) -> AssetRuntime:
    url = (database_url or os.environ.get("XINGJING_ASSET_DATABASE_URL", "")).strip()
    if not url:
        return AssetRuntime(repository=None, storyboards=None, session_factory=None, context_resolver=None, generation_submitter=generation_submitter, generation_asset_resolver=generation_asset_resolver, generation_task_resolver=generation_task_resolver, unavailable_code="XINGJING_ASSET_DATABASE_URL_REQUIRED")
    if "+aiosqlite" in url or "+asyncpg" in url:
        return AssetRuntime(repository=None, storyboards=None, session_factory=None, context_resolver=None, generation_submitter=generation_submitter, generation_asset_resolver=generation_asset_resolver, generation_task_resolver=generation_task_resolver, unavailable_code="XINGJING_ASSET_DATABASE_URL_MUST_BE_SYNC")
    root = (evidence_storage_root or os.environ.get("XINGJING_ASSET_EVIDENCE_ROOT", "")).strip()
    try:
        engine = create_engine(url, pool_pre_ping=True)
        storage = LocalAssetEvidenceStorage(root) if root else None
    except (ModuleNotFoundError, SQLAlchemyError, ValueError) as error:
        raise AssetRuntimeConfigurationError("ASSET_RUNTIME_DEPENDENCY_UNAVAILABLE") from error
    factory: SessionFactory = sessionmaker(engine, expire_on_commit=False)
    return AssetRuntime(
        repository=SqlAlchemyAssetRepository(factory),
        storyboards=SqlAlchemyStoryboardRepository(factory),
        session_factory=factory,
        context_resolver=context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        generation_submitter=generation_submitter,
        generation_asset_resolver=generation_asset_resolver,
        generation_task_resolver=generation_task_resolver,
        evidence_storage=storage,
        close=engine.dispose,
    )
