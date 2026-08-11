"""Trusted HTTP boundary for M04 assets.

Only operations backed by the M04 SQLAlchemy repository are accepted here.
Generation, marketplace licensing and external proof uploads intentionally fail
closed until their own providers are wired; returning a fabricated task would
make the asset pages look finished while losing production data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from server.xingjing_assets.contracts import (
    Asset,
    AssetKind,
    AssetSource,
    FrozenScriptSnapshotRef,
    ReuseScope,
    RightsEvidence,
    RightsStatus,
)
from server.xingjing_assets.errors import AssetDomainError, AssetNotFound, CrossProjectReuseDenied, VersionConflict
from server.xingjing_assets.ports import AssetRepository, ShotImpactQuery
from server.xingjing_assets.service import AssetService
from server.xingjing_assets_persistence import AssetAuditEvent, AssetScope, SqlAlchemyAssetRepository
from server.xingjing_content.parser import parse_script
from server.xingjing_generation import GenerationRequest, GenerationTask, MediaType
from server.xingjing_generation_runtime import GenerationRuntimeUnavailable
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_storyboard.ports import StoryboardScope

if TYPE_CHECKING:
    from server.xingjing_assets_runtime.runtime import AssetRuntime


class AssetHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})
        super().__init__(code)


class AssetContractRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> JSONResponse:
            try:
                return cast(JSONResponse, await original(request))
            except AssetHttpError as error:
                return _error(request, error.code, error.status_code, error.details)
            except LookupError as error:
                return _error(request, str(error) or "NOT_FOUND", 404)
            except VersionConflict as error:
                return _error(request, str(error) or "VERSION_CONFLICT", 409)
            except AssetNotFound:
                return _error(request, "ASSET_NOT_FOUND", 404)
            except CrossProjectReuseDenied:
                return _error(request, "ASSET_REUSE_NOT_AUTHORIZED", 403)
            except AssetDomainError as error:
                return _error(request, str(error) or "ASSET_OPERATION_FAILED", 400)
            except ValueError as error:
                return _error(request, str(error) or "INVALID_REQUEST", 400)
            except Exception as error:  # noqa: BLE001
                # A production dependency is unavailable; it must not be
                # misreported as an empty library or a successful generation.
                return _error(request, "ASSET_RUNTIME_UNAVAILABLE", 503, {"reason": type(error).__name__})

        return handler


class ContextAssetRepository(AssetRepository):
    """Binds one HTTP command to the persistent repository transaction."""

    def __init__(
        self,
        repository: SqlAlchemyAssetRepository,
        scope: AssetScope,
        context: TrustedWorkspaceContext,
        *,
        action: str,
        idempotency_key: str,
        request_payload: Mapping[str, object],
    ) -> None:
        self._repository = repository
        self._scope = scope
        self._context = context
        self._action = action
        self._idempotency_key = idempotency_key
        self._request_payload = dict(request_payload)

    def get(self, workspace_id: str, asset_id: str) -> Asset | None:
        if workspace_id != self._scope.workspace_id:
            return None
        return self._repository.get(self._scope, asset_id)

    def save(self, asset: Asset, *, expected_revision: int | None) -> None:
        before = self._repository.get(self._scope, asset.asset_id)
        # The first create generates a server-side asset ID.  That ID must not
        # participate in the command fingerprint, otherwise retrying the same
        # idempotency key would look like a different request.
        fingerprint = _digest({"action": self._action, "payload": self._request_payload})
        now = datetime.now(UTC)
        self._repository.commit(
            self._scope,
            asset,
            expected_revision=expected_revision,
            idempotency_key=self._idempotency_key,
            fingerprint=fingerprint,
            audit=AssetAuditEvent(
                request_id=self._context.request_id,
                actor_id=self._context.actor_id,
                action=f"asset.{self._action}",
                asset_id=asset.asset_id,
                result="succeeded",
                occurred_at=now,
                before_payload=before.to_dict() if before else {},
                after_payload=asset.to_dict(),
            ),
        )


class StoryboardImpactQuery(ShotImpactQuery):
    def __init__(self, runtime: AssetRuntime, context: TrustedWorkspaceContext, project_id: str) -> None:
        self._runtime = runtime
        self._context = context
        self._project_id = project_id

    def affected_shot_ids(self, *, workspace_id: str, project_id: str, asset_id: str) -> tuple[str, ...]:
        if workspace_id != self._context.workspace_id or project_id != self._project_id:
            return ()
        if self._runtime.storyboards is None:
            raise AssetHttpError("STORYBOARD_DEPENDENCY_UNAVAILABLE", 503)
        scope = StoryboardScope(self._context.tenant_id, workspace_id, project_id)
        return tuple(
            shot.shot_id
            for storyboard in self._runtime.storyboards.list(scope)
            for shot in storyboard.shots
            if any(reference.asset_id == asset_id for reference in shot.asset_references)
        )


def create_asset_router(runtime: AssetRuntime) -> APIRouter:
    router = APIRouter(route_class=AssetContractRoute)

    @router.get("/workspaces/{workspace_id}/assets")
    async def list_workspace_assets(request: Request, workspace_id: str) -> JSONResponse:
        context = await _workspace_context(runtime, request, workspace_id, "asset.view")
        scope = AssetScope(context.tenant_id, context.workspace_id)
        query = request.query_params
        offset = _page_offset(query)
        limit = _integer(query.get("pageSize"), default=50, minimum=1, maximum=200, code="INVALID_PAGE_SIZE")
        kind_value = query.get("assetType")
        kind = _kind(kind_value) if kind_value else None
        assets = _repository(runtime).list_for_workspace(
            scope, search=query.get("search"), kind=kind, offset=offset, limit=limit + 1
        )
        return _asset_page(context, runtime, assets, offset=offset, limit=limit)

    @router.get("/projects/{project_id}/assets")
    async def list_assets(request: Request, project_id: str) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.view")
        scope = AssetScope(context.tenant_id, context.workspace_id)
        repository = _repository(runtime)
        query = request.query_params
        offset = _page_offset(query)
        limit = _integer(query.get("pageSize"), default=50, minimum=1, maximum=200, code="INVALID_PAGE_SIZE")
        kind_value = query.get("assetType")
        kind = _kind(kind_value) if kind_value else None
        assets = repository.list_for_project(scope, project_id, search=query.get("search"), kind=kind, offset=offset, limit=limit + 1)
        rights = query.get("rightsStatus")
        if rights:
            assets = tuple(item for item in assets if _rights_status(item) == rights)
        return _asset_page(context, runtime, assets, offset=offset, limit=limit)

    @router.get("/projects/{project_id}/assets/{asset_id}/audit")
    async def list_asset_audit(request: Request, project_id: str, asset_id: str) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.view")
        scope = AssetScope(context.tenant_id, context.workspace_id)
        repository = _repository(runtime)
        asset = repository.get(scope, asset_id)
        if asset is None or (
            asset.owner_project_id != project_id
            and not any(reference.project_id == project_id for reference in asset.references)
        ):
            raise AssetHttpError("ASSET_NOT_FOUND", 404)
        query = request.query_params
        events = repository.list_audit(
            scope,
            asset_id=asset_id,
            request_id=query.get("requestId"),
            actor_id=query.get("actorId"),
            action=query.get("action"),
            limit=_integer(query.get("pageSize"), default=100, minimum=1, maximum=200, code="INVALID_PAGE_SIZE"),
            offset=_page_offset(query),
        )
        return _success(context, [{
            "requestId": event.request_id,
            "actorId": event.actor_id,
            "action": event.action,
            "assetId": event.asset_id,
            "result": event.result,
            "occurredAt": event.occurred_at.isoformat(),
            "before": event.before_payload,
            "after": event.after_payload,
        } for event in events])

    @router.post("/projects/{project_id}/assets/{asset_id}/rights-evidence")
    async def upload_rights_evidence(request: Request, project_id: str, asset_id: str, file: UploadFile = File(...)) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.manage")
        if not request.headers.get("Idempotency-Key", "").strip():
            raise AssetHttpError("IDEMPOTENCY_KEY_REQUIRED")
        scope = AssetScope(context.tenant_id, context.workspace_id)
        if _repository(runtime).get(scope, asset_id) is None:
            raise AssetHttpError("ASSET_NOT_FOUND", 404)
        if file.content_type not in {"application/pdf", "image/jpeg", "image/png", "image/webp"}:
            raise AssetHttpError("RIGHTS_EVIDENCE_MEDIA_TYPE_INVALID")
        content = await file.read()
        if len(content) > 25 * 1024 * 1024:
            raise AssetHttpError("RIGHTS_EVIDENCE_TOO_LARGE")
        key, digest = runtime.store_rights_evidence(context, project_id, content)
        return _success(context, {
            "objectKey": key,
            "sha256": digest,
            "fileName": file.filename or "rights-evidence",
            "mediaType": file.content_type,
        })

    @router.post("/projects/{project_id}/assets/actions")
    async def asset_action(request: Request, project_id: str) -> JSONResponse:
        body = await _body(request)
        action = _text(body, "action")
        context = await _context(runtime, request, project_id, "asset.manage")
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise AssetHttpError("IDEMPOTENCY_KEY_REQUIRED")
        payload = _mapping(body.get("payload"), "INVALID_ACTION_PAYLOAD")
        target_id = _optional_text(body.get("targetId"))
        repository = _repository(runtime)
        scope = AssetScope(context.tenant_id, context.workspace_id)
        service = AssetService(
            ContextAssetRepository(repository, scope, context, action=action, idempotency_key=key, request_payload=body),
            StoryboardImpactQuery(runtime, context, project_id),
        )
        if action == "createAsset":
            frozen = _snapshot(runtime, context, project_id, _text(payload, "frozenSnapshotId"))
            asset = service.create(
                workspace_id=context.workspace_id,
                owner_project_id=project_id,
                kind=_kind(_text(payload, "kind")),
                name=_text(payload, "name"),
                content=_content(payload),
                source=AssetSource("frozen_script", frozen.snapshot_id, f"content://{frozen.snapshot_id}", frozen.content_sha256),
                frozen_snapshot=frozen,
            )
            return _success(context, {"asset": _asset_item(runtime, context, project_id, asset)})
        if action in {"updateAsset", "createVersion"}:
            asset_id = _required_target(target_id)
            current = service.get(context.workspace_id, asset_id)
            expected = _if_match(request)
            content = _content(payload, base=current.current_version.content)
            changed = service.revise(
                workspace_id=context.workspace_id,
                asset_id=asset_id,
                expected_revision=expected,
                content=content,
                change_note=_optional_text(payload.get("changeNote")) or ("asset.updated" if action == "updateAsset" else "asset.version_created"),
                name=_optional_text(payload.get("name")) if action == "updateAsset" else None,
            )
            return _success(context, {"asset": _asset_item(runtime, context, project_id, changed)})
        if action in {"setPrimaryVersion", "restoreVersion"}:
            changed = service.rollback(
                workspace_id=context.workspace_id,
                asset_id=_required_target(target_id),
                target_version_id=_text(payload, "versionId"),
                expected_revision=_if_match(request),
            )
            return _success(context, {"asset": _asset_item(runtime, context, project_id, changed)})
        if action == "bindReference":
            reference = service.reference_project(
                workspace_id=context.workspace_id,
                project_id=project_id,
                asset_id=_required_target(target_id),
            )
            asset = service.get(context.workspace_id, reference.asset_id)
            return _success(context, {"asset": _asset_item(runtime, context, project_id, asset)})
        if action == "saveRights":
            asset_id = _required_target(target_id)
            object_key = _text(payload, "proofObjectKey")
            evidence_sha256 = _text(payload, "proofSha256")
            if not runtime.verify_rights_evidence(context, project_id, object_key, evidence_sha256):
                raise AssetHttpError("RIGHTS_EVIDENCE_NOT_VERIFIED", 409)
            scope_value = _optional_text(payload.get("licenseScope")) or ReuseScope.OWNER_PROJECT_ONLY.value
            try:
                reuse_scope = ReuseScope(scope_value)
            except ValueError as error:
                raise AssetHttpError("INVALID_REUSE_SCOPE") from error
            allowed_project_ids = _text_list(payload.get("allowedProjectIds"), "INVALID_ALLOWED_PROJECT_IDS") if "allowedProjectIds" in payload else ()
            if reuse_scope is ReuseScope.PROJECT_ALLOWLIST and not allowed_project_ids:
                raise AssetHttpError("ALLOWED_PROJECT_IDS_REQUIRED")
            if reuse_scope is not ReuseScope.PROJECT_ALLOWLIST and allowed_project_ids:
                raise AssetHttpError("ALLOWED_PROJECT_IDS_NOT_PERMITTED")
            evidence = RightsEvidence(
                rights_id=str(uuid4()),
                status=RightsStatus.PENDING,
                reuse_scope=reuse_scope,
                allowed_project_ids=allowed_project_ids,
                evidence_object_key=object_key,
                evidence_sha256=evidence_sha256,
                holder=_optional_text(payload.get("holder")),
                valid_until=_optional_text(payload.get("validTo")),
            )
            changed = service.record_rights(
                workspace_id=context.workspace_id,
                asset_id=asset_id,
                expected_revision=_if_match(request),
                evidence=evidence,
            )
            return _success(context, {"asset": _asset_item(runtime, context, project_id, changed)})
        if action == "adoptGenerationOutput":
            asset_id = _required_target(target_id)
            current = service.get(context.workspace_id, asset_id)
            generated = await runtime.resolve_generated_asset(
                context,
                project_id,
                _text(payload, "generatedAssetId"),
                asset_id,
            )
            content = dict(current.current_version.content)
            content.update({
                "generatedAssetId": generated.asset_id,
                "generationTaskId": generated.task_id,
                "generatedObjectKey": generated.object_key,
                "generatedMediaType": generated.media_type,
                "generationMetadata": dict(generated.metadata),
            })
            changed = service.revise(
                workspace_id=context.workspace_id,
                asset_id=asset_id,
                expected_revision=_if_match(request),
                content=content,
                change_note="asset.generation_output_adopted",
            )
            return _success(context, {"asset": _asset_item(runtime, context, project_id, changed)})
        if action in {"generateTurnaround", "generateExpressions"}:
            task = await _submit_character_derivative(
                runtime,
                context,
                project_id,
                service.get(context.workspace_id, _required_target(target_id)),
                action=action,
                payload=payload,
                idempotency_key=key,
            )
            return _success(context, {"task": _generation_task_receipt(task, context)})
        if action == "extractAssets":
            frozen_snapshot_id = _text(payload, "scriptVersionId")
            frozen = _snapshot(runtime, context, project_id, frozen_snapshot_id)
            batch = _extract_assets_from_frozen_script(
                runtime=runtime,
                context=context,
                project_id=project_id,
                scope=scope,
                repository=repository,
                request_body=body,
                idempotency_key=key,
                frozen=frozen,
                requested_kinds=tuple(_kind(item) for item in _text_list(payload.get("assetTypes"), "ASSET_TYPES_REQUIRED")),
            )
            return _success(context, {"batch": batch})
        if action in {"licenseMarketAsset", "confirmAssets", "batchUpdate", "retryFailedItems"}:
            raise AssetHttpError("ASSET_ACTION_DEPENDENCY_UNAVAILABLE", 503)
        raise AssetHttpError("UNSUPPORTED_ASSET_ACTION")

    return router


def create_unavailable_asset_router(code: str | None) -> APIRouter:
    router = APIRouter(route_class=AssetContractRoute)
    unavailable = code or "ASSET_RUNTIME_UNAVAILABLE"

    @router.get("/workspaces/{workspace_id}/assets")
    async def workspace_assets_unavailable(request: Request, workspace_id: str) -> JSONResponse:
        _ = (request, workspace_id)
        raise AssetHttpError(unavailable, 503)

    @router.get("/projects/{project_id}/assets", operation_id="unavailable_project_assets_get")
    async def assets_unavailable(request: Request, project_id: str) -> JSONResponse:
        _ = (request, project_id)
        raise AssetHttpError(unavailable, 503)

    router.add_api_route(
        "/projects/{project_id}/assets",
        assets_unavailable,
        methods=["POST"],
        operation_id="unavailable_project_assets_post",
    )

    @router.post("/projects/{project_id}/assets/actions")
    async def asset_actions_unavailable(request: Request, project_id: str) -> JSONResponse:
        _ = (request, project_id)
        raise AssetHttpError(unavailable, 503)

    return router


async def _context(runtime: AssetRuntime, request: Request, project_id: str, permission: str) -> TrustedWorkspaceContext:
    context = await runtime.resolve_context(request, project_id)
    if permission not in context.permissions:
        raise AssetHttpError("PERMISSION_DENIED", 403, {"permission": permission})
    return context


async def _workspace_context(
    runtime: AssetRuntime, request: Request, workspace_id: str, permission: str
) -> TrustedWorkspaceContext:
    context = await runtime.resolve_workspace_context(request, workspace_id)
    if permission not in context.permissions:
        raise AssetHttpError("PERMISSION_DENIED", 403, {"permission": permission})
    return context


def _repository(runtime: AssetRuntime) -> SqlAlchemyAssetRepository:
    if runtime.repository is None:
        raise AssetHttpError(runtime.unavailable_code or "ASSET_RUNTIME_UNAVAILABLE", 503)
    return runtime.repository


def _snapshot(runtime: AssetRuntime, context: TrustedWorkspaceContext, project_id: str, snapshot_id: str) -> FrozenScriptSnapshotRef:
    script_id, number, digest = runtime.frozen_script(context, project_id, snapshot_id)
    return FrozenScriptSnapshotRef(snapshot_id=snapshot_id, workspace_id=context.workspace_id, project_id=project_id, script_version_id=f"{script_id}@{number}", content_sha256=digest, frozen_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))


def _asset_item(runtime: AssetRuntime, context: TrustedWorkspaceContext, project_id: str, asset: Asset) -> dict[str, object]:
    rights = asset.rights[-1] if asset.rights else None
    rights_status = rights.status.value if rights else "missing"
    impacted = StoryboardImpactQuery(runtime, context, project_id).affected_shot_ids(workspace_id=context.workspace_id, project_id=project_id, asset_id=asset.asset_id)
    content = dict(asset.current_version.content)
    tags = content.get("tags", [])
    return {
        "id": asset.asset_id,
        "projectId": asset.owner_project_id,
        "type": asset.kind.value,
        "name": asset.name,
        "tags": [item for item in tags if isinstance(item, str)] if isinstance(tags, list) else [],
        "source": asset.source.source_type,
        "status": "draft",
        "rightsStatus": rights_status,
        "currentVersionId": asset.current_version_id,
        "version": asset.revision,
        "referenceCount": len(asset.references),
        "updatedAt": asset.current_version.created_at,
        "description": content.get("description") if isinstance(content.get("description"), str) else None,
        "metadata": content,
        "versions": [{"id": version.version_id, "versionNo": version.sequence, "status": "draft", "source": asset.source.source_type, "createdAt": version.created_at} for version in asset.versions],
        "rights": None if rights is None else {
            "id": rights.rights_id,
            "holder": rights.holder,
            "licenseScope": rights.reuse_scope.value,
            "proofUploadId": rights.evidence_object_key,
            "validTo": rights.valid_until,
            "status": rights.status.value,
        },
        "affectedShotIds": list(impacted),
    }


def _rights_status(asset: Asset) -> str:
    return asset.rights[-1].status.value if asset.rights else "missing"


async def _submit_character_derivative(
    runtime: AssetRuntime,
    context: TrustedWorkspaceContext,
    project_id: str,
    asset: Asset,
    *,
    action: str,
    payload: Mapping[str, object],
    idempotency_key: str,
) -> GenerationTask:
    if asset.kind is not AssetKind.CHARACTER:
        raise AssetHttpError("CHARACTER_ASSET_REQUIRED", 400)
    source_version_id = _text(payload, "sourceVersionId")
    if source_version_id != asset.current_version_id:
        raise AssetHttpError("ASSET_VERSION_CONFLICT", 409)
    items = _text_list(payload.get("items"), "DERIVATIVE_ITEMS_REQUIRED")
    capability = "character_turnaround" if action == "generateTurnaround" else "character_expressions"
    request = GenerationRequest(
        workspace_id=context.workspace_id,
        project_id=project_id,
        media_type=MediaType.IMAGE,
        capability=capability,
        prompt=f"为角色 {asset.name} 生成{('三视图' if action == 'generateTurnaround' else '表情集')}：{', '.join(items)}",
        parameters={
            "asset_id": asset.asset_id,
            "source_version_id": source_version_id,
            "items": list(items),
        },
        input_asset_ids=(asset.asset_id,),
    )
    try:
        return await runtime.submit_generation(
            context, project_id, request, idempotency_key=idempotency_key
        )
    except (GenerationRuntimeUnavailable, RuntimeError) as error:
        raise AssetHttpError(str(error), 503) from error


def _generation_task_receipt(task: GenerationTask, context: TrustedWorkspaceContext) -> dict[str, object]:
    return {
        "taskId": task.task_id,
        "projectId": task.request.project_id,
        "status": task.status.value,
        "statusUrl": f"/api/v1/generation-tasks/{task.task_id}?projectId={task.request.project_id}",
        "requestId": context.request_id,
    }


def _extract_assets_from_frozen_script(
    *,
    runtime: AssetRuntime,
    context: TrustedWorkspaceContext,
    project_id: str,
    scope: AssetScope,
    repository: SqlAlchemyAssetRepository,
    request_body: Mapping[str, object],
    idempotency_key: str,
    frozen: FrozenScriptSnapshotRef,
    requested_kinds: tuple[AssetKind, ...],
) -> dict[str, object]:
    """Persist the deterministic subset of M03 assets without inventing AI output.

    Frozen M03 documents carry reliable scene headings and dialogue speakers.
    Props, costumes and voices require a semantic model or a reviewed human
    source, so they are reported as skipped instead of being guessed.
    """
    version_number = int(frozen.script_version_id.rsplit("@", 1)[1])
    parsed = parse_script(
        runtime.frozen_script_source(context, project_id, frozen.snapshot_id),
        version_number=version_number,
        change_summary="asset extraction",
    )
    candidates: list[tuple[AssetKind, str, dict[str, object]]] = []
    if AssetKind.CHARACTER in requested_kinds:
        character_sources: dict[str, list[str]] = {}
        for scene in parsed.scenes:
            for paragraph in scene.paragraphs:
                if paragraph.speaker:
                    character_sources.setdefault(paragraph.speaker, []).append(paragraph.paragraph_id)
        candidates.extend(
            (AssetKind.CHARACTER, name, {"sourceScriptVersionId": frozen.script_version_id, "sourceParagraphIds": identifiers, "tags": ["script-extracted"]})
            for name, identifiers in character_sources.items()
        )
    if AssetKind.SCENE in requested_kinds:
        scene_sources: dict[str, list[str]] = {}
        scene_metadata: dict[str, dict[str, object]] = {}
        for scene in parsed.scenes:
            scene_sources.setdefault(scene.heading.location, []).append(scene.scene_id)
            scene_metadata[scene.heading.location] = {
                "sourceScriptVersionId": frozen.script_version_id,
                "sourceSceneIds": scene_sources[scene.heading.location],
                "timeOfDay": scene.heading.time_of_day,
                "setting": scene.heading.setting,
                "tags": ["script-extracted"],
            }
        candidates.extend((AssetKind.SCENE, name, scene_metadata[name]) for name in scene_sources)

    fingerprint = _digest({"action": "extractAssets", "payload": request_body})
    result_items: list[dict[str, object]] = []
    for kind in requested_kinds:
        if kind in {AssetKind.PROP, AssetKind.COSTUME, AssetKind.VOICE}:
            result_items.append({
                "id": f"{kind.value}:semantic-extraction",
                "status": "skipped",
                "code": "SEMANTIC_EXTRACTION_REQUIRES_PROVIDER",
                "message": f"{kind.value} 需要语义模型或人工标注，未猜测创建资产。",
            })

    for kind, name, content in candidates:
        candidate_key = f"{idempotency_key}:extract:{kind.value}:{_digest({'name': name})[:16]}"
        replayed = repository.replay(scope, idempotency_key=candidate_key, fingerprint=fingerprint)
        if replayed is not None:
            result_items.append({"id": replayed.asset_id, "status": "succeeded", "version": replayed.revision, "message": "幂等返回已创建资产。"})
            continue
        service = AssetService(
            ContextAssetRepository(
                repository,
                scope,
                context,
                action="extractAssets",
                idempotency_key=candidate_key,
                request_payload=request_body,
            ),
            StoryboardImpactQuery(runtime, context, project_id),
        )
        asset = service.create(
            workspace_id=context.workspace_id,
            owner_project_id=project_id,
            kind=kind,
            name=name,
            content=content,
            source=AssetSource("frozen_script", frozen.snapshot_id, f"content://{frozen.snapshot_id}", frozen.content_sha256),
            frozen_snapshot=frozen,
        )
        result_items.append({"id": asset.asset_id, "status": "succeeded", "version": asset.revision})

    if not candidates and not result_items:
        raise AssetHttpError("NO_EXTRACTABLE_ASSETS", 422)
    succeeded = sum(item["status"] == "succeeded" for item in result_items)
    failed = sum(item["status"] == "failed" for item in result_items)
    return {
        "operationId": f"asset-extract:{idempotency_key}",
        "items": result_items,
        "succeeded": succeeded,
        "failed": failed,
        "requestId": context.request_id,
    }


def _asset_page(
    context: TrustedWorkspaceContext,
    runtime: AssetRuntime,
    assets: tuple[Asset, ...],
    *,
    offset: int,
    limit: int,
) -> JSONResponse:
    page = assets[:limit]
    return JSONResponse(
        {
            "data": [_asset_item(runtime, context, item.owner_project_id, item) for item in page],
            "meta": {
                "requestId": context.request_id,
                "page": {"nextToken": str(offset + limit) if len(assets) > limit else None},
            },
        },
        headers={"X-Request-Id": context.request_id},
    )


async def _body(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise AssetHttpError("INVALID_JSON") from error
    return _mapping(value, "INVALID_REQUEST_BODY")


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AssetHttpError(code)
    return cast(Mapping[str, object], value)


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise AssetHttpError(f"{key.upper()}_REQUIRED")
    return item.strip()


def _text_list(value: object, code: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AssetHttpError(code)
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if not items or len(items) != len(value) or len(set(items)) != len(items):
        raise AssetHttpError(code)
    return items


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_target(value: str | None) -> str:
    if not value:
        raise AssetHttpError("TARGET_ID_REQUIRED")
    return value


def _integer(value: str | None, *, default: int, minimum: int, maximum: int = 2**31 - 1, code: str) -> int:
    if value is None:
        return default
    if not value.isdigit() or not minimum <= int(value) <= maximum:
        raise AssetHttpError(code)
    return int(value)


def _page_offset(query: Mapping[str, str]) -> int:
    raw = query.get("pageToken") or query.get("offset")
    return _integer(raw, default=0, minimum=0, code="INVALID_PAGE_TOKEN")


def _if_match(request: Request) -> int:
    return _integer(request.headers.get("If-Match", "").strip().strip('"'), default=-1, minimum=1, code="EXPECTED_VERSION_REQUIRED")


def _kind(value: str) -> AssetKind:
    try:
        return AssetKind(value)
    except ValueError as error:
        raise AssetHttpError("INVALID_ASSET_KIND") from error


def _content(payload: Mapping[str, object], *, base: Mapping[str, object] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = dict(base or {})
    metadata = payload.get("metadata", payload.get("content", {}))
    if not isinstance(metadata, Mapping):
        raise AssetHttpError("INVALID_ASSET_CONTENT")
    result.update(cast(Mapping[str, Any], metadata))
    description = payload.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise AssetHttpError("INVALID_ASSET_CONTENT")
        result["description"] = description
    return json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _success(context: TrustedWorkspaceContext, data: object) -> JSONResponse:
    return JSONResponse({"data": data, "meta": {"requestId": context.request_id}}, headers={"X-Request-Id": context.request_id})


def _error(request: Request, code: str, status_code: int, details: Mapping[str, object] | None = None) -> JSONResponse:
    request_id = (request.headers.get("X-Request-Id") or str(uuid4())).strip()
    return JSONResponse({"error": {"code": code, "message": code, "details": dict(details or {})}, "meta": {"requestId": request_id}}, status_code=status_code, headers={"X-Request-Id": request_id})
