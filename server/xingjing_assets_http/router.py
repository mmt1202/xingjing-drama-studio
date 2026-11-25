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

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from lib.episode_ledger import ordered_episode_entries
from lib.i18n import get_locale, get_translator
from lib.project_manager import get_project_manager
from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from server.xingjing_assets.contracts import (
    Asset,
    AssetKind,
    AssetSource,
    FrozenScriptSnapshotRef,
    ReuseScope,
    RightsEvidence,
    RightsStatus,
    rights_expiry,
)
from server.xingjing_assets.errors import AssetDomainError, AssetNotFound, CrossProjectReuseDenied, VersionConflict
from server.xingjing_assets.ports import AssetRepository, ShotImpactQuery
from server.xingjing_assets.service import AssetService
from server.xingjing_assets_persistence import AssetAuditEvent, AssetScope, SqlAlchemyAssetRepository
from server.xingjing_content.parser import parse_script
from server.xingjing_content.recognition import is_reading_script
from server.xingjing_generation import GenerationRequest, GenerationTask, MediaType
from server.xingjing_generation.contracts import GenerationAssetReference
from server.xingjing_generation_runtime import GenerationRuntimeUnavailable
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_storyboard.ports import StoryboardScope

from .profile_draft import PROFILE_TYPES, generate_profile, reference_image_prompt, script_excerpt, turnaround_prompt

if TYPE_CHECKING:
    from server.xingjing_assets_runtime.runtime import AssetRuntime


class AssetHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})
        super().__init__(code)


class _SemanticAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AssetKind
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    source_scene_ids: list[str] = Field(default_factory=list, max_length=128)


class _SemanticAssets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assets: list[_SemanticAsset] = Field(default_factory=list, max_length=300)


class AssetContractRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> JSONResponse:
            try:
                return cast(JSONResponse, await original(request))
            except AssetHttpError as error:
                return _error(request, error.code, error.status_code, error.details)
            except GenerationRuntimeUnavailable as error:
                details = {"submissionState": "rejected"} if error.submission_rejected else {}
                return _error(request, error.code, 503, details)
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, Mapping) else {}
                code = detail.get("code")
                return _error(request, code if isinstance(code, str) else "REQUEST_REJECTED", error.status_code)
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
        return _asset_page(context, runtime, request.query_params)

    @router.get("/projects/{project_id}/assets")
    async def list_assets(request: Request, project_id: str) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.view")
        return _asset_page(context, runtime, request.query_params, project_id)

    @router.get("/projects/{project_id}/assets/sources/frozen-scripts")
    async def frozen_scripts(request: Request, project_id: str) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.view")
        if "script.view" not in context.permissions:
            raise AssetHttpError("PERMISSION_DENIED", 403, {"permission": "script.view"})
        return _success(
            context,
            {
                "scripts": runtime.list_frozen_scripts(context, project_id),
                "canManage": "asset.manage" in context.permissions,
            },
        )

    @router.get("/projects/{project_id}/assets/extractions")
    async def extraction_history(request: Request, project_id: str) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.view")
        if "script.view" not in context.permissions:
            raise AssetHttpError("PERMISSION_DENIED", 403, {"permission": "script.view"})
        version = _text(request.query_params, "scriptVersionId")
        history = _repository(runtime).extraction_history(
            AssetScope(context.tenant_id, context.workspace_id),
            project_id,
            version,
            request.query_params.get("operationId"),
        )
        return _success(context, history)

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
        return _success(
            context,
            [
                {
                    "requestId": event.request_id,
                    "actorId": event.actor_id,
                    "action": event.action,
                    "assetId": event.asset_id,
                    "result": event.result,
                    "occurredAt": event.occurred_at.isoformat(),
                    "before": event.before_payload,
                    "after": event.after_payload,
                }
                for event in events
            ],
        )

    @router.get("/projects/{project_id}/assets/{asset_id}")
    async def get_project_asset(request: Request, project_id: str, asset_id: str) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.view")
        asset = _repository(runtime).get(AssetScope(context.tenant_id, context.workspace_id), asset_id)
        if asset is None or (
            asset.owner_project_id != project_id
            and not any(reference.project_id == project_id for reference in asset.references)
        ):
            raise AssetHttpError("ASSET_NOT_FOUND", 404)
        return _success(
            context, {"asset": _asset_item(runtime, context, project_id, asset, include_profile_history=True)}
        )

    @router.get("/workspaces/{workspace_id}/assets/{asset_id}")
    async def get_workspace_asset(request: Request, workspace_id: str, asset_id: str) -> JSONResponse:
        context = await _workspace_context(runtime, request, workspace_id, "asset.view")
        asset = _repository(runtime).get(AssetScope(context.tenant_id, context.workspace_id), asset_id)
        if asset is None:
            raise AssetHttpError("ASSET_NOT_FOUND", 404)
        return _success(
            context,
            {"asset": _asset_item(runtime, context, asset.owner_project_id, asset, include_profile_history=True)},
        )

    @router.post("/projects/{project_id}/assets/{asset_id}/rights-evidence")
    async def upload_rights_evidence(
        request: Request, project_id: str, asset_id: str, file: UploadFile = File(...)
    ) -> JSONResponse:
        context = await _context(runtime, request, project_id, "asset.manage")
        if not request.headers.get("Idempotency-Key", "").strip():
            raise AssetHttpError("IDEMPOTENCY_KEY_REQUIRED")
        scope = AssetScope(context.tenant_id, context.workspace_id)
        _owned_asset(_repository(runtime), scope, project_id, asset_id)
        if file.content_type not in {"application/pdf", "image/jpeg", "image/png", "image/webp"}:
            raise AssetHttpError("RIGHTS_EVIDENCE_MEDIA_TYPE_INVALID")
        content = await file.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise AssetHttpError("RIGHTS_EVIDENCE_TOO_LARGE")
        key, digest = runtime.store_rights_evidence(context, project_id, content)
        return _success(
            context,
            {
                "objectKey": key,
                "sha256": digest,
                "fileName": file.filename or "rights-evidence",
                "mediaType": file.content_type,
            },
        )

    @router.get("/projects/{project_id}/assets/{asset_id}/rights-evidence/{rights_id}")
    async def download_rights_evidence(request: Request, project_id: str, asset_id: str, rights_id: str) -> Response:
        context = await _context(runtime, request, project_id, "asset.manage")
        asset = _owned_asset(
            _repository(runtime), AssetScope(context.tenant_id, context.workspace_id), project_id, asset_id
        )
        record = next((record for record in asset.rights if record.rights_id == rights_id), None)
        if record is None or record.evidence_object_key is None or record.evidence_sha256 is None:
            raise AssetHttpError("RIGHTS_EVIDENCE_NOT_FOUND", 404)
        content = runtime.read_rights_evidence(context, project_id, record.evidence_object_key, record.evidence_sha256)
        if content is None:
            raise AssetHttpError("RIGHTS_EVIDENCE_NOT_FOUND", 404)
        media, ext = "application/octet-stream", "bin"
        for prefix, mime, suffix in (
            (b"%PDF-", "application/pdf", "pdf"),
            (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
            (b"\xff\xd8\xff", "image/jpeg", "jpg"),
        ):
            if content.startswith(prefix):
                media, ext = mime, suffix
                break
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            media, ext = "image/webp", "webp"
        return Response(
            content,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="evidence.{ext}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Request-Id": context.request_id,
            },
        )

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
        if action == "draftAssetProfile":
            asset = _owned_asset(repository, scope, project_id, _required_target(target_id))
            if asset.revision != _if_match(request):
                raise AssetHttpError("ASSET_VERSION_CONFLICT", 409)
            return _success(
                context,
                {
                    "profileDraft": await _draft_profile(
                        runtime, repository, context, scope, asset, payload, key, get_locale(request)
                    )
                },
            )
        service = AssetService(
            ContextAssetRepository(
                repository, scope, context, action=action, idempotency_key=key, request_payload=body
            ),
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
                source=AssetSource(
                    "frozen_script", frozen.snapshot_id, f"content://{frozen.snapshot_id}", frozen.content_sha256
                ),
                frozen_snapshot=frozen,
            )
            return _success(context, {"asset": _asset_item(runtime, context, project_id, asset)})
        if action in {"updateAsset", "createVersion"}:
            asset_id = _required_target(target_id)
            current = _owned_asset(repository, scope, project_id, asset_id)
            expected = _if_match(request)
            content = _content(payload, base=current.current_version.content)
            changed = service.revise(
                workspace_id=context.workspace_id,
                asset_id=asset_id,
                expected_revision=expected,
                content=content,
                change_note=_optional_text(payload.get("changeNote"))
                or ("asset.updated" if action == "updateAsset" else "asset.version_created"),
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
        if action in {"saveRights", "confirmRights"}:
            asset_id = _required_target(target_id)
            current = _owned_asset(repository, scope, project_id, asset_id)
            replay = repository.replay(
                scope, idempotency_key=key, fingerprint=_digest({"action": action, "payload": body})
            )
            if replay is not None:
                if replay.asset_id != asset_id:
                    raise VersionConflict("VERSION_CONFLICT")
                return _success(context, {"asset": _asset_item(runtime, context, project_id, current)})
            if current.revision != _if_match(request):
                raise VersionConflict("VERSION_CONFLICT")
            if action == "confirmRights":
                latest = current.rights[-1] if current.rights else None
                if not latest or latest.rights_id != _text(payload, "rightsRecordId"):
                    raise VersionConflict("VERSION_CONFLICT")
                if not _rights_attachment_valid(
                    runtime, context, project_id, latest.evidence_object_key, latest.evidence_sha256
                ):
                    raise AssetHttpError("RIGHTS_EVIDENCE_NOT_VERIFIED", 409)
                service.confirm_rights(
                    workspace_id=context.workspace_id, asset_id=asset_id, expected_revision=current.revision
                )
                return _success(
                    context,
                    {
                        "asset": _asset_item(
                            runtime, context, project_id, _owned_asset(repository, scope, project_id, asset_id)
                        )
                    },
                )
            raw_end = payload.get("validTo")
            if raw_end is not None and not isinstance(raw_end, str):
                raise AssetHttpError("RIGHTS_EXPIRY_INVALID")
            valid_to = _optional_text(raw_end)
            try:
                expires = rights_expiry(valid_to)
            except (ValueError, OverflowError) as error:
                raise AssetHttpError("RIGHTS_EXPIRY_INVALID") from error
            if expires is not None and expires <= datetime.now(UTC):
                raise AssetHttpError("RIGHTS_EVIDENCE_EXPIRED")
            object_key = _text(payload, "proofObjectKey") if payload.get("proofObjectKey") is not None else None
            evidence_sha256 = _text(payload, "proofSha256") if payload.get("proofSha256") is not None else None
            if not _rights_attachment_valid(runtime, context, project_id, object_key, evidence_sha256):
                raise AssetHttpError("RIGHTS_EVIDENCE_NOT_VERIFIED", 409)
            scope_value = _optional_text(payload.get("licenseScope")) or ReuseScope.OWNER_PROJECT_ONLY.value
            try:
                reuse_scope = ReuseScope(scope_value)
            except ValueError as error:
                raise AssetHttpError("INVALID_REUSE_SCOPE") from error
            allowed_project_ids = (
                _text_list(payload.get("allowedProjectIds"), "INVALID_ALLOWED_PROJECT_IDS")
                if "allowedProjectIds" in payload
                else ()
            )
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
                holder=_text(payload, "holder") if object_key is None else _optional_text(payload.get("holder")),
                valid_until=valid_to,
            )
            service.record_rights(
                workspace_id=context.workspace_id,
                asset_id=asset_id,
                expected_revision=_if_match(request),
                evidence=evidence,
            )
            return _success(
                context,
                {
                    "asset": _asset_item(
                        runtime, context, project_id, _owned_asset(repository, scope, project_id, asset_id)
                    )
                },
            )
        if action == "adoptGenerationOutput":
            asset_id = _required_target(target_id)
            current = service.get(context.workspace_id, asset_id)
            if current.owner_project_id != project_id:
                raise AssetHttpError("ASSET_NOT_FOUND", 404)
            generated = await runtime.resolve_generated_asset(
                context,
                project_id,
                _text(payload, "generatedAssetId"),
                asset_id,
            )
            content = dict(current.current_version.content)
            content.update(
                {
                    "generatedAssetId": generated.asset_id,
                    "generationTaskId": generated.task_id,
                    "generatedObjectKey": generated.object_key,
                    "generatedMediaType": generated.media_type,
                    "generatedContentSha256": generated.content_sha256,
                    "generatedSizeBytes": generated.size_bytes,
                    "generationMetadata": dict(generated.metadata),
                }
            )
            changed = service.revise(
                workspace_id=context.workspace_id,
                asset_id=asset_id,
                expected_revision=_if_match(request),
                content=content,
                change_note="asset.generation_output_adopted",
            )
            return _success(context, {"asset": _asset_item(runtime, context, project_id, changed)})
        if action == "generateReferenceImage":
            current = service.get(context.workspace_id, _required_target(target_id))
            if current.kind is AssetKind.VOICE:
                raise AssetHttpError("ASSET_REFERENCE_IMAGE_UNSUPPORTED")
            if current.owner_project_id != project_id:
                raise AssetHttpError("ASSET_NOT_FOUND", 404)
            if current.revision != _if_match(request):
                raise AssetHttpError("ASSET_VERSION_CONFLICT", 409)
            if _text(payload, "sourceVersionId") != current.current_version_id:
                raise AssetHttpError("ASSET_VERSION_CONFLICT", 409)
            task = await runtime.submit_generation(
                context,
                project_id,
                GenerationRequest(
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    media_type=MediaType.IMAGE,
                    capability="image_generation",
                    prompt=reference_image_prompt(current),
                    parameters={
                        "asset_id": current.asset_id,
                        "source_version_id": current.current_version_id,
                        "source_content_sha256": current.current_version.content_sha256,
                        "asset_operation": "reference_image",
                    },
                ),
                idempotency_key=key,
            )
            return _success(context, {"task": _generation_task_receipt(task, context)})
        if action in {"generateTurnaround", "generateExpressions"}:
            current = _owned_asset(repository, scope, project_id, _required_target(target_id))
            task = await _submit_character_derivative(
                runtime,
                context,
                project_id,
                current,
                action=action,
                payload=payload,
                idempotency_key=key,
            )
            return _success(context, {"task": _generation_task_receipt(task, context)})
        if action == "extractAssets":
            frozen_snapshot_id = _text(payload, "scriptVersionId")
            frozen = _snapshot(runtime, context, project_id, frozen_snapshot_id)
            interpret_narration = payload.get("interpretNarration", False)
            if not isinstance(interpret_narration, bool):
                raise AssetHttpError("INVALID_EXTRACTION_MODE")
            batch = await _extract_assets_from_frozen_script(
                runtime=runtime,
                context=context,
                project_id=project_id,
                scope=scope,
                repository=repository,
                request_body=body,
                idempotency_key=key,
                frozen=frozen,
                interpret_narration=interpret_narration,
                requested_kinds=tuple(
                    _kind(item) for item in _text_list(payload.get("assetTypes"), "ASSET_TYPES_REQUIRED")
                ),
            )
            return _success(context, {"batch": batch})
        if action in {"confirmAssets", "retryFailedItems"}:
            item_ids = tuple(dict.fromkeys(_text_list(payload.get("itemIds"), "ASSET_IDS_REQUIRED")))
            if len(item_ids) > 100:
                raise AssetHttpError("ASSET_BATCH_TOO_LARGE")
            records = _mapping(payload["records"], "INVALID_RIGHTS_SELECTION") if "records" in payload else {}
            items: list[dict[str, object]] = []
            for asset_id in item_ids:
                try:
                    expected = records.get(asset_id)
                    selection = {"assetId": asset_id, **({"record": expected} if expected is not None else {})}
                    item_key = f"{key}:{asset_id}"
                    item_repository = ContextAssetRepository(
                        repository,
                        scope,
                        context,
                        action="confirmRights",
                        idempotency_key=item_key,
                        request_payload=selection,
                    )
                    item_service = AssetService(item_repository, StoryboardImpactQuery(runtime, context, project_id))
                    current = item_service.get(context.workspace_id, asset_id)
                    if current.owner_project_id != project_id:
                        raise AssetNotFound("ASSET_NOT_FOUND")
                    replay = repository.replay(
                        scope,
                        idempotency_key=item_key,
                        fingerprint=_digest({"action": "confirmRights", "payload": selection}),
                    )
                    if replay is not None:
                        items.append({"id": asset_id, "name": current.name, "status": "succeeded"})
                        continue
                    if records and (
                        not isinstance(expected, Mapping)
                        or not current.rights
                        or (expected.get("version"), expected.get("rightsId"))
                        != (current.revision, current.rights[-1].rights_id)
                    ):
                        raise ValueError("RIGHTS_SELECTION_CHANGED")
                    if current.rights and current.rights[-1].effective_status is RightsStatus.PENDING:
                        latest = current.rights[-1]
                        if not _rights_attachment_valid(
                            runtime, context, project_id, latest.evidence_object_key, latest.evidence_sha256
                        ):
                            raise ValueError("RIGHTS_EVIDENCE_NOT_VERIFIED")
                    item_service.confirm_rights(
                        workspace_id=context.workspace_id,
                        asset_id=asset_id,
                        expected_revision=current.revision,
                    )
                    items.append({"id": asset_id, "name": current.name, "status": "succeeded"})
                except (AssetDomainError, ValueError) as error:
                    code = str(error) or type(error).__name__
                    items.append(
                        {
                            "id": asset_id,
                            "status": "failed",
                            "code": code,
                            "message": code,
                            "retryable": code in {"VERSION_CONFLICT", "ASSET_RUNTIME_UNAVAILABLE"},
                        }
                    )
            failed = sum(item["status"] == "failed" for item in items)
            return _success(
                context,
                {
                    "batch": {
                        "operationId": context.request_id,
                        "succeeded": len(items) - failed,
                        "failed": failed,
                        "items": items,
                    }
                },
            )
        if action in {"licenseMarketAsset", "batchUpdate"}:
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

    @router.get("/projects/{project_id}/assets/sources/frozen-scripts")
    @router.get("/projects/{project_id}/assets/extractions")
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

    @router.get("/projects/{project_id}/assets/{asset_id}")
    async def project_asset_unavailable(request: Request, project_id: str, asset_id: str) -> JSONResponse:
        _ = (request, project_id, asset_id)
        raise AssetHttpError(unavailable, 503)

    @router.get("/workspaces/{workspace_id}/assets/{asset_id}")
    async def workspace_asset_unavailable(request: Request, workspace_id: str, asset_id: str) -> JSONResponse:
        _ = (request, workspace_id, asset_id)
        raise AssetHttpError(unavailable, 503)

    @router.post("/projects/{project_id}/assets/actions")
    async def asset_actions_unavailable(request: Request, project_id: str) -> JSONResponse:
        _ = (request, project_id)
        raise AssetHttpError(unavailable, 503)

    return router


async def _context(
    runtime: AssetRuntime, request: Request, project_id: str, permission: str
) -> TrustedWorkspaceContext:
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


def _snapshot(
    runtime: AssetRuntime, context: TrustedWorkspaceContext, project_id: str, snapshot_id: str
) -> FrozenScriptSnapshotRef:
    script_id, number, digest = runtime.frozen_script(context, project_id, snapshot_id)
    return FrozenScriptSnapshotRef(
        snapshot_id=snapshot_id,
        workspace_id=context.workspace_id,
        project_id=project_id,
        script_version_id=f"{script_id}@{number}",
        content_sha256=digest,
        frozen_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _owned_asset(repository: SqlAlchemyAssetRepository, scope: AssetScope, project_id: str, asset_id: str) -> Asset:
    asset = repository.get(scope, asset_id)
    if asset is None or asset.owner_project_id != project_id:
        raise AssetHttpError("ASSET_NOT_FOUND", 404)
    return asset


def _asset_item(
    runtime: AssetRuntime,
    context: TrustedWorkspaceContext,
    project_id: str,
    asset: Asset,
    impacted: tuple[str, ...] | None = None,
    *,
    include_profile_history: bool = False,
) -> dict[str, object]:
    rights = asset.rights[-1] if asset.rights else None
    rights_status = _rights_status(asset)
    if impacted is None:
        impacted = StoryboardImpactQuery(runtime, context, project_id).affected_shot_ids(
            workspace_id=context.workspace_id, project_id=project_id, asset_id=asset.asset_id
        )
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
        "referenceCount": len(impacted),
        "updatedAt": asset.current_version.created_at,
        "description": content.get("description") if isinstance(content.get("description"), str) else None,
        "metadata": content,
        "versions": [
            {
                "id": version.version_id,
                "versionNo": version.sequence,
                "status": "draft",
                "source": asset.source.source_type,
                "createdAt": version.created_at,
                **(
                    {
                        "profile": {
                            field: text
                            for field in PROFILE_TYPES[asset.kind].model_fields
                            if field != "name"
                            and isinstance((text := version.content.get(field)), str)
                            and text.strip()
                        }
                    }
                    if include_profile_history
                    else {}
                ),
                "assetContentSha256": version.content_sha256,
                "imageCapability": (version.content.get("generationMetadata") or {}).get("capability")
                if isinstance(version.content.get("generationMetadata"), dict)
                else None,
                **(
                    {"generatedAssetId": version.content["generatedAssetId"]}
                    if isinstance(version.content.get("generatedAssetId"), str)
                    else {}
                ),
                **(
                    {"generatedMediaType": version.content["generatedMediaType"]}
                    if version.content.get("generatedMediaType") in {"image", "video"}
                    else {}
                ),
            }
            for version in asset.versions
        ],
        "rights": None
        if rights is None
        else {
            "id": rights.rights_id,
            "holder": rights.holder,
            "licenseScope": rights.reuse_scope.value,
            **({"allowedProjectIds": list(rights.allowed_project_ids)} if rights.allowed_project_ids else {}),
            **({"proofUploadId": rights.evidence_object_key} if rights.evidence_object_key else {}),
            "validTo": rights.valid_until,
            "status": rights_status,
        },
        "affectedShotIds": list(impacted),
    }


def _rights_attachment_valid(
    runtime: AssetRuntime, context: TrustedWorkspaceContext, project_id: str, key: str | None, digest: str | None
) -> bool:
    if key is None or digest is None:
        return key is None and digest is None
    return runtime.verify_rights_evidence(context, project_id, key, digest)


def _rights_status(asset: Asset) -> str:
    if not asset.rights:
        return "missing"
    status = asset.rights[-1].effective_status
    return "blocked" if status is RightsStatus.REJECTED else status.value


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
    prompt = f"为角色 {asset.name} 生成表情集：{', '.join(items)}"
    if action == "generateTurnaround":
        direction = payload.get("direction", "")
        if not isinstance(direction, str) or len(direction) > 1000:
            raise AssetHttpError("INVALID_ASSET_CONTENT")
        prompt = turnaround_prompt(asset, items, direction.strip())
    request = GenerationRequest(
        workspace_id=context.workspace_id,
        project_id=project_id,
        media_type=MediaType.IMAGE,
        capability=capability,
        prompt=prompt,
        parameters={
            "asset_id": asset.asset_id,
            "source_version_id": source_version_id,
            "items": list(items),
            **({"aspect_ratio": "3:2"} if action == "generateTurnaround" else {}),
        },
        input_asset_ids=(asset.asset_id,),
        input_asset_references=(
            GenerationAssetReference(
                asset_id=asset.asset_id,
                asset_version_id=source_version_id,
                asset_content_sha256=asset.current_version.content_sha256,
            ),
        ),
    )
    try:
        return await runtime.submit_generation(context, project_id, request, idempotency_key=idempotency_key)
    except GenerationRuntimeUnavailable:
        raise
    except RuntimeError as error:
        raise AssetHttpError(str(error), 503) from error


def _generation_task_receipt(task: GenerationTask, context: TrustedWorkspaceContext) -> dict[str, object]:
    return {
        "taskId": task.task_id,
        "projectId": task.request.project_id,
        "status": task.status.value,
        "capability": task.request.capability,
        "sourceVersionId": task.request.parameters.get("source_version_id"),
        "statusUrl": f"/api/v1/generation-tasks/{task.task_id}?projectId={task.request.project_id}",
        "requestId": context.request_id,
    }


async def _draft_profile(
    runtime: AssetRuntime,
    repository: SqlAlchemyAssetRepository,
    context: TrustedWorkspaceContext,
    scope: AssetScope,
    asset: Asset,
    payload: Mapping[str, object],
    key: str,
    locale: str,
) -> dict[str, object]:
    schema = PROFILE_TYPES[asset.kind]
    fields = _mapping(payload.get("fields"), "ASSET_PROFILE_INPUT_INVALID")
    direction = payload.get("direction", "")
    if (
        not isinstance(direction, str)
        or len(direction) > 600
        or fields.keys() - schema.model_fields.keys()
        or any(not isinstance(value, str) or len(value) > 4000 for value in fields.values())
    ):
        raise AssetHttpError("ASSET_PROFILE_INPUT_INVALID")
    current = {field: value.strip() for field, value in cast(Mapping[str, str], fields).items()}
    operation = "asset.profile"
    claim_key = hashlib.sha256(key.encode()).hexdigest()
    fingerprint = _digest(
        {
            "asset": asset.asset_id,
            "version": asset.revision,
            "actor": context.actor_id,
            "scriptAccess": "script.view" in context.permissions,
            "fields": current,
            "direction": direction,
            "locale": locale,
        }
    )
    claim = repository.claim_operation(
        scope,
        operation_kind=operation,
        idempotency_key=claim_key,
        fingerprint=fingerprint,
        owner_id=asset.asset_id,
        now=datetime.now(UTC),
    )
    if claim.status == "succeeded" and claim.result is not None:
        return dict(claim.result)
    if not claim.claimed:
        raise AssetHttpError("ASSET_PROFILE_IN_PROGRESS", 409)
    try:
        source = runtime.asset_source_text(context, asset.owner_project_id, asset.frozen_snapshot)
        excerpt = script_excerpt(source, asset)
        suggestions = await generate_profile(asset, current, direction, excerpt, locale)
        result: dict[str, object] = {"fields": suggestions, "usedScript": bool(excerpt), "assetVersion": asset.revision}
    except Exception as error:
        repository.fail_operation(
            scope,
            operation_kind=operation,
            idempotency_key=claim_key,
            fingerprint=fingerprint,
            reason=type(error).__name__,
            now=datetime.now(UTC),
        )
        raise AssetHttpError("ASSET_PROFILE_UNAVAILABLE", 503) from error
    repository.finish_operation(
        scope,
        operation_kind=operation,
        idempotency_key=claim_key,
        fingerprint=fingerprint,
        result=result,
        now=datetime.now(UTC),
    )
    return result


async def _semantic_asset_candidates(
    *,
    repository: SqlAlchemyAssetRepository,
    scope: AssetScope,
    idempotency_key: str,
    fingerprint: str,
    project_id: str,
    script_source: str,
    requested_kinds: tuple[AssetKind, ...],
) -> _SemanticAssets:
    operation_key = f"semantic:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    claim = repository.claim_operation(
        scope,
        operation_kind="semantic.extract",
        idempotency_key=operation_key,
        fingerprint=fingerprint,
        owner_id=project_id,
        now=datetime.now(UTC),
    )
    if claim.status == "succeeded":
        if claim.result is None:
            raise AssetHttpError("SEMANTIC_EXTRACTION_RESULT_MISSING", 500)
        return _SemanticAssets.model_validate(claim.result)
    if not claim.claimed:
        raise AssetHttpError("SEMANTIC_EXTRACTION_IN_PROGRESS", 409)
    try:
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=project_id)
        generated = await generator.generate(
            TextGenerationRequest(
                system_prompt=(
                    "你是影视资产拆解师。按请求类型从冻结剧本中提取有文本依据的人物、实际地点、道具、服装或声音。"
                    "称呼和别名合并为同一人物。旁白和朗读格式标题不是人物或地点。"
                    "不得虚构人物、物品或权利信息。输出必须严格符合 JSON Schema。"
                ),
                prompt=(
                    f"仅提取这些类型：{', '.join(kind.value for kind in requested_kinds)}。"
                    "同类型同名称合并，source_scene_ids 仅填写剧本中可确认的场景标识；无法确认时留空。\n"
                    + script_source
                ),
                response_schema=_SemanticAssets,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            project_name=project_id,
        )
        output = _SemanticAssets.model_validate_json(strip_json_code_fences(generated.text))
        requested = set(requested_kinds)
        seen: set[tuple[AssetKind, str]] = set()
        for item in output.assets:
            identity = (item.kind, item.name.strip().casefold())
            if item.kind not in requested:
                raise ValueError("SEMANTIC_EXTRACTION_KIND_NOT_REQUESTED")
            if identity in seen:
                raise ValueError("SEMANTIC_EXTRACTION_DUPLICATE_ASSET")
            seen.add(identity)
    except Exception as error:
        repository.fail_operation(
            scope,
            operation_kind="semantic.extract",
            idempotency_key=operation_key,
            fingerprint=fingerprint,
            reason=str(error),
            now=datetime.now(UTC),
        )
        raise AssetHttpError("SEMANTIC_EXTRACTION_MODEL_FAILED", 502, {"reason": str(error)[:500]}) from error
    repository.finish_operation(
        scope,
        operation_kind="semantic.extract",
        idempotency_key=operation_key,
        fingerprint=fingerprint,
        result=cast(dict[str, object], output.model_dump(mode="json")),
        now=datetime.now(UTC),
    )
    return output


async def _extract_assets_from_frozen_script(
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
    interpret_narration: bool = False,
) -> dict[str, object]:
    """Persist deterministic entities plus model-validated semantic assets."""
    version_number = int(frozen.script_version_id.rsplit("@", 1)[1])
    source = runtime.frozen_script_source(context, project_id, frozen.snapshot_id)
    parsed = parse_script(source, version_number=version_number, change_summary="asset extraction")
    reading_script = is_reading_script(parsed.analysis.locations, parsed.analysis.dialogue_count)
    candidates: list[tuple[AssetKind, str, dict[str, object]]] = []
    if AssetKind.CHARACTER in requested_kinds:
        character_sources: dict[str, list[str]] = {}
        for scene in parsed.scenes:
            for paragraph in scene.paragraphs:
                if paragraph.speaker:
                    character_sources.setdefault(paragraph.speaker, []).append(paragraph.paragraph_id)
        candidates.extend(
            (
                AssetKind.CHARACTER,
                name,
                {
                    "sourceScriptVersionId": frozen.script_version_id,
                    "sourceParagraphIds": identifiers,
                    "tags": ["script-extracted"],
                },
            )
            for name, identifiers in character_sources.items()
        )
    if AssetKind.SCENE in requested_kinds and not reading_script:
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
    semantic_kinds = tuple(
        kind
        for kind in requested_kinds
        if interpret_narration or kind in {AssetKind.PROP, AssetKind.COSTUME, AssetKind.VOICE}
    )
    if semantic_kinds:
        semantic = await _semantic_asset_candidates(
            repository=repository,
            scope=scope,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            project_id=project_id,
            script_source=source,
            requested_kinds=semantic_kinds,
        )
        structured = {(kind, name.strip().casefold()) for kind, name, _ in candidates}
        candidates.extend(
            (
                item.kind,
                item.name,
                {
                    "description": item.description,
                    "tags": list(dict.fromkeys((*item.tags, "model-extracted"))),
                    "sourceScriptVersionId": frozen.script_version_id,
                    "sourceSceneIds": item.source_scene_ids,
                },
            )
            for item in semantic.assets
            if (item.kind, item.name.strip().casefold()) not in structured
        )

    if not candidates:
        if reading_script and not interpret_narration and not semantic_kinds:
            raise AssetHttpError("NARRATION_REQUIRES_INTERPRETATION", 422)
        raise AssetHttpError("NO_EXTRACTABLE_ASSETS", 422)

    for kind, name, content in candidates:
        candidate_key = f"{idempotency_key}:extract:{kind.value}:{_digest({'name': name})[:16]}"
        if repository.replay(scope, idempotency_key=candidate_key, fingerprint=fingerprint) is not None:
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
        service.create(
            workspace_id=context.workspace_id,
            owner_project_id=project_id,
            kind=kind,
            name=name,
            content=content,
            source=AssetSource(
                "frozen_script", frozen.snapshot_id, f"content://{frozen.snapshot_id}", frozen.content_sha256
            ),
            frozen_snapshot=frozen,
        )

    history = repository.extraction_history(
        scope, project_id, frozen.script_version_id, f"asset-extract:{idempotency_key}"
    )
    batch = history["batch"]
    if not isinstance(batch, dict):
        raise AssetHttpError("ASSET_RUNTIME_UNAVAILABLE", 503)
    return {**batch, "requestId": context.request_id}


def _asset_page(
    context: TrustedWorkspaceContext, runtime: AssetRuntime, query: Mapping[str, str], project_id: str | None = None
) -> JSONResponse:
    offset = _page_offset(query)
    limit = _integer(query.get("pageSize"), default=50, minimum=1, maximum=200, code="INVALID_PAGE_SIZE")
    kind_value = query.get("assetType")
    episode = query.get("episodeId") if project_id else None
    storyboards = runtime.storyboards
    if storyboards is None:
        raise AssetHttpError("STORYBOARD_DEPENDENCY_UNAVAILABLE", 503)
    impacts: dict[str, dict[str, list[str]]] = {}

    def shots(owner: str) -> dict[str, list[str]]:
        if owner not in impacts:
            grouped: dict[str, list[str]] = {}
            for board in storyboards.list(StoryboardScope(context.tenant_id, context.workspace_id, owner)):
                if episode and board.episode_id != episode:
                    continue
                for shot in board.shots:
                    for asset_id in {ref.asset_id for ref in shot.asset_references}:
                        grouped.setdefault(asset_id, []).append(shot.shot_id)
            impacts[owner] = grouped
        return impacts[owner]

    versions = None
    if episode and project_id:
        project = get_project_manager().load_project(project_id, read_only=True)
        entry = next((entry for entry in ordered_episode_entries(project) if str(entry["episode"]) == episode), None)
        if entry is None:
            raise AssetHttpError("EPISODE_NOT_FOUND", 404)
        binding = entry.get("production") or {}
        version = binding.get("scriptVersionId")
        versions = (version,) if isinstance(version, str) and version else ()
    assets, total = _repository(runtime).library_page(
        AssetScope(context.tenant_id, context.workspace_id),
        project_id=project_id,
        kind=_kind(kind_value) if kind_value else None,
        search=query.get("search"),
        rights=query.get("rightsStatus"),
        episode_versions=versions,
        shot_assets=tuple(shots(project_id)) if episode and project_id else (),
        offset=offset,
        limit=limit,
    )
    return JSONResponse(
        {
            "data": [
                _asset_item(
                    runtime,
                    context,
                    project_id or item.owner_project_id,
                    item,
                    tuple(shots(project_id or item.owner_project_id).get(item.asset_id, ())),
                )
                for item in assets
            ],
            "meta": {
                "requestId": context.request_id,
                "page": {"total": total, "nextToken": str(offset + limit) if offset + limit < total else None},
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
    return _integer(
        request.headers.get("If-Match", "").strip().strip('"'), default=-1, minimum=1, code="EXPECTED_VERSION_REQUIRED"
    )


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
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _success(context: TrustedWorkspaceContext, data: object) -> JSONResponse:
    return JSONResponse(
        {"data": data, "meta": {"requestId": context.request_id}}, headers={"X-Request-Id": context.request_id}
    )


def _error(request: Request, code: str, status_code: int, details: Mapping[str, object] | None = None) -> JSONResponse:
    request_id = (request.headers.get("X-Request-Id") or str(uuid4())).strip()
    translated = {
        "ASSET_PROFILE_INPUT_INVALID",
        "ASSET_PROFILE_IN_PROGRESS",
        "ASSET_PROFILE_UNAVAILABLE",
        "ASSET_NOT_FOUND",
        "RIGHTS_EVIDENCE_NOT_FOUND",
        "HOLDER_REQUIRED",
        "RIGHTS_EXPIRY_INVALID",
        "RIGHTS_EVIDENCE_EXPIRED",
        "RIGHTS_EVIDENCE_NOT_VERIFIED",
        "ASSET_REFERENCE_IMAGE_UNSUPPORTED",
        "GENERATION_INPUT_ASSET_VERSION_REQUIRED",
        "GENERATION_INPUT_ASSET_VERSION_NOT_FOUND",
        "GENERATION_INPUT_ASSET_CONTENT_MISMATCH",
        "GENERATION_INPUT_ASSET_MEDIA_REQUIRED",
        "GENERATION_INPUT_ASSET_MEDIA_NOT_FOUND",
        "GENERATION_INPUT_ASSET_MEDIA_MISMATCH",
        "GENERATION_INPUT_ASSET_REFERENCE_INVALID",
        "GENERATION_INPUT_ASSET_UNVERIFIED",
        "GENERATION_INPUT_REQUIRED",
        "GENERATION_INPUT_MEDIA_TYPE_INVALID",
        "GENERATION_INPUT_COUNT_INVALID",
        "GENERATION_INPUT_TOO_LARGE",
        "GENERATION_INPUT_ROLES_UNSUPPORTED",
        "GENERATION_PROVIDER_INPUT_CONTRACT_UNAVAILABLE",
    }
    message = get_translator(request)(code.lower()) if code in translated else code
    return JSONResponse(
        {
            "error": {"code": code, "message": message, "details": dict(details or {})},
            "meta": {"requestId": request_id},
        },
        status_code=status_code,
        headers={"X-Request-Id": request_id},
    )
