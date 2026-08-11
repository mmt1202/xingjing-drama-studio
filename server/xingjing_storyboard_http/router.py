# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportGeneralTypeIssues=false, reportUnnecessaryIsInstance=false
from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from server.xingjing_generation import GenerationRequest, MediaType
from server.xingjing_generation_runtime import GenerationRuntimeUnavailable
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_storyboard.errors import (
    ContractViolation,
    IdempotencyConflict,
    PermissionDenied,
    StoryboardError,
    StoryboardNotFound,
    VersionConflict,
)
from server.xingjing_storyboard.exchange import ExportFormat, import_shots
from server.xingjing_storyboard.models import Storyboard
from server.xingjing_storyboard.ports import AuditEvent
from server.xingjing_storyboard.service import AccessContext, ShotEdit

from .dependencies import ProjectScopeAuthorizer, StoryboardHttpDependencies
from .projection import project_shot

_configured_dependencies: StoryboardHttpDependencies | None = None


class HttpContractError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 400, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(code)


class StoryboardContractRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except HttpContractError as error:
                return _error_response(request, error.code, error.status_code, error.details)
            except StoryboardError as error:
                return _storyboard_error_response(request, error)
            except HTTPException as error:
                return _http_exception_response(request, error)

        return handler


def create_storyboard_router(dependencies: StoryboardHttpDependencies) -> APIRouter:
    result = APIRouter(route_class=StoryboardContractRoute)

    @result.get("/projects/{project_id}/prompt-templates")
    async def list_prompt_templates(request: Request, project_id: str):
        context = await _access_context(request, project_id, dependencies)
        if dependencies.prompt_repository is None:
            raise HttpContractError("PROMPT_TEMPLATE_STORE_UNAVAILABLE", status_code=503)
        usages = {
            prompt_id: sum(
                1 for storyboard in _all_storyboards(dependencies, context) for shot in storyboard.shots
                if shot.prompt_template_id == prompt_id
            )
            for prompt_id in (item.prompt_id for item in dependencies.prompt_repository.list(context.scope))
        }
        items = dependencies.prompt_repository.list(context.scope)
        return _data_response(context.request_id, [item.to_dict(usage_count=usages.get(item.prompt_id, 0)) for item in items])

    @result.post("/projects/{project_id}/prompt-templates")
    async def create_prompt_template(request: Request, project_id: str):
        context = await _access_context(request, project_id, dependencies)
        _require_manage(context)
        if dependencies.prompt_repository is None:
            raise HttpContractError("PROMPT_TEMPLATE_STORE_UNAVAILABLE", status_code=503)
        body = _object(await request.json(), code="INVALID_JSON_BODY")
        now = datetime.now(UTC)
        template, replayed = dependencies.prompt_repository.create(
            context.scope,
            name=_required_text(body, "name"), media_type=_required_text(body, "mediaType"),
            template=_required_text(body, "template"), negative_prompt=str(body.get("negativePrompt") or ""),
            variables=_optional_string_list(body.get("variables"), code="INVALID_PROMPT_VARIABLES"),
            model_adapter_versions=_optional_string_list(body.get("modelAdapterVersions"), code="INVALID_MODEL_ADAPTERS"),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            audit=AuditEvent(context.tenant_id, context.workspace_id, context.project_id, context.request_id,
                context.actor_id, "storyboard.prompt_template_created", "pending", "succeeded", now, {}, {}),
        )
        return _data_response(context.request_id, template.to_dict(), replayed=replayed)

    @result.put("/projects/{project_id}/prompt-templates/{prompt_id}")
    async def update_prompt_template(request: Request, project_id: str, prompt_id: str):
        context = await _access_context(request, project_id, dependencies)
        _require_manage(context)
        repository = dependencies.prompt_repository
        if repository is None:
            raise HttpContractError("PROMPT_TEMPLATE_STORE_UNAVAILABLE", status_code=503)
        current = repository.get(context.scope, prompt_id)
        if current is None or current.archived_at is not None:
            raise HttpContractError("PROMPT_TEMPLATE_NOT_FOUND", status_code=404)
        expected = _expected_version(request, {})
        body = _object(await request.json(), code="INVALID_JSON_BODY")
        updated = replace(
            current, name=_required_text(body, "name"), media_type=_required_text(body, "mediaType"),
            template=_required_text(body, "template"), negative_prompt=str(body.get("negativePrompt") or ""),
            variables=_optional_string_list(body.get("variables"), code="INVALID_PROMPT_VARIABLES"),
            model_adapter_versions=_optional_string_list(body.get("modelAdapterVersions"), code="INVALID_MODEL_ADAPTERS"),
            version=expected + 1, updated_at=datetime.now(UTC),
        )
        stored, replayed = repository.update(
            context.scope, updated, expected_version=expected,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            audit=AuditEvent(context.tenant_id, context.workspace_id, context.project_id, context.request_id,
                context.actor_id, "storyboard.prompt_template_updated", prompt_id, "succeeded", updated.updated_at,
                current.to_dict(), updated.to_dict()),
        )
        return _data_response(context.request_id, stored.to_dict(), replayed=replayed)

    @result.delete("/projects/{project_id}/prompt-templates/{prompt_id}")
    async def archive_prompt_template(request: Request, project_id: str, prompt_id: str):
        context = await _access_context(request, project_id, dependencies)
        _require_manage(context)
        repository = dependencies.prompt_repository
        if repository is None:
            raise HttpContractError("PROMPT_TEMPLATE_STORE_UNAVAILABLE", status_code=503)
        current = repository.get(context.scope, prompt_id)
        if current is None:
            raise HttpContractError("PROMPT_TEMPLATE_NOT_FOUND", status_code=404)
        expected = _expected_version(request, {})
        now = datetime.now(UTC)
        archived = replace(current, version=expected + 1, updated_at=now, archived_at=now)
        stored, replayed = repository.update(
            context.scope, archived, expected_version=expected,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            audit=AuditEvent(context.tenant_id, context.workspace_id, context.project_id, context.request_id,
                context.actor_id, "storyboard.prompt_template_archived", prompt_id, "succeeded", now,
                current.to_dict(), archived.to_dict()),
        )
        return _data_response(context.request_id, stored.to_dict(), replayed=replayed)

    @result.get("/projects/{project_id}/storyboard-audit")
    async def query_storyboard_audit(request: Request, project_id: str):
        context = await _access_context(request, project_id, dependencies)
        _require_permission(context.permissions, "shot.view")
        if dependencies.audit_repository is None:
            raise HttpContractError("STORYBOARD_AUDIT_UNAVAILABLE", status_code=503)
        events = dependencies.audit_repository.query_audit(
            context.scope, storyboard_id=request.query_params.get("objectId"),
            request_id=request.query_params.get("requestId"), actor_id=request.query_params.get("actorId"),
            action=request.query_params.get("action"),
        )
        return _data_response(context.request_id, [{
            "requestId": item.request_id, "actorId": item.actor_id, "action": item.action,
            "objectId": item.object_id, "result": item.result, "occurredAt": item.occurred_at.isoformat(),
            "before": item.before, "after": item.after, "errorCode": item.error_code,
        } for item in events])

    @result.get("/projects/{project_id}/shots")
    async def list_shots(request: Request, project_id: str):
        context = await _access_context(request, project_id, dependencies)
        storyboards = _all_storyboards(dependencies, context)
        episode_id = request.query_params.get("episodeId")
        search = (request.query_params.get("search") or "").casefold()
        status = request.query_params.get("status")
        has_issues = _optional_bool(request.query_params.get("hasIssues"))
        items = [
            project_shot(storyboard, shot)
            for storyboard in storyboards
            if episode_id is None or storyboard.episode_id == episode_id
            for shot in storyboard.shots
            if _matches(storyboard, shot.shot_id, shot.shot_number, shot.prompt, search, status, has_issues)
        ]
        items.sort(key=lambda item: (str(item["episodeId"]), int(item["sequenceNo"]), str(item["id"])))
        offset = _non_negative_integer(request.query_params.get("pageToken"), default=0, code="INVALID_PAGE_TOKEN")
        page_size = _bounded_integer(
            request.query_params.get("pageSize"), default=20, minimum=1, maximum=200, code="INVALID_PAGE_SIZE"
        )
        selected = items[offset : offset + page_size]
        selected = [
            await _with_generation_facts(item, context, dependencies)
            for item in selected
        ]
        next_token = str(offset + page_size) if offset + page_size < len(items) else None
        collection_version = max((storyboard.version for storyboard in storyboards), default=0)
        return {
            "data": selected,
            "meta": {
                "requestId": context.request_id,
                "page": {"nextToken": next_token},
                "collectionVersion": collection_version,
            },
        }

    @result.get("/projects/{project_id}/shots/{shot_id}")
    async def get_shot(request: Request, project_id: str, shot_id: str):
        context = await _access_context(request, project_id, dependencies)
        for storyboard in _all_storyboards(dependencies, context):
            shot = next((item for item in storyboard.shots if item.shot_id == shot_id), None)
            if shot is not None:
                projected = await _with_generation_facts(
                    project_shot(storyboard, shot), context, dependencies
                )
                return {"data": projected, "meta": {"requestId": context.request_id}}
        raise HttpContractError("SHOT_NOT_FOUND", status_code=404)

    @result.post("/projects/{project_id}/shots/actions")
    async def shot_actions(request: Request, project_id: str):
        context = await _access_context(request, project_id, dependencies)
        body = await _json_object(request)
        action = _required_text(body, "action")
        payload = _object(body.get("payload"), code="INVALID_ACTION_PAYLOAD")
        if action == "preflightImport":
            _require_manage(context)
            upload = _upload(dependencies, context, _required_text(payload, "uploadId"))
            imported = import_shots(_required_text(payload, "fileName"), upload.content)
            return _action_response(
                context,
                {
                    "batch": _batch(
                        "preflight", [item.shot_id or str(item.position) for item in imported], context.request_id
                    )
                },
            )
        if action == "commitImport":
            upload = _upload(dependencies, context, _required_text(payload, "uploadId"))
            result_value = dependencies.service.import_storyboard(
                context,
                episode_id=_required_text(payload, "episodeId"),
                filename=upload.filename,
                content=upload.content,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
            persisted = dependencies.service.get_storyboard(context, result_value.storyboard.storyboard_id)
            return _action_response(
                context,
                {"shots": [project_shot(persisted, shot) for shot in persisted.shots]},
                replayed=result_value.replayed,
                version=persisted.version,
            )
        if action == "createExport":
            storyboard = dependencies.service.get_storyboard(context, _required_text(payload, "storyboardId"))
            try:
                format_value = ExportFormat(_required_text(payload, "format"))
            except ValueError as error:
                raise HttpContractError("UNSUPPORTED_EXPORT_FORMAT") from error
            exported = dependencies.service.export_storyboard(
                context,
                storyboard_id=storyboard.storyboard_id,
                format=format_value,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
            export_id = dependencies.upload_store.put_export(context, exported.content, exported.media_type, exported.filename)
            return _action_response(
                context,
                {
                    "task": {
                        "taskId": export_id,
                        "status": "succeeded",
                        "downloadUrl": f"/api/v1/projects/{context.project_id}/storyboard-exports/{export_id}",
                    }
                },
                replayed=exported.replayed,
            )
        if action == "generateShots":
            _require_manage(context)
            if dependencies.smart_draft_generator is None:
                raise HttpContractError("SMART_STORYBOARD_SOURCE_UNAVAILABLE", status_code=503)
            try:
                drafts = dependencies.smart_draft_generator(
                    context,
                    _required_text(payload, "scriptVersionId"),
                    _required_text(payload, "assetSnapshotId"),
                    request.headers.get("Idempotency-Key", ""),
                )
            except ContractViolation as error:
                if str(error) == "SMART_STORYBOARD_REQUEST_IN_PROGRESS":
                    raise HttpContractError(
                        "SMART_STORYBOARD_REQUEST_IN_PROGRESS", status_code=409
                    ) from error
                raise
            if inspect.isawaitable(drafts):
                try:
                    drafts = await drafts
                except ContractViolation as error:
                    if str(error) == "SMART_STORYBOARD_REQUEST_IN_PROGRESS":
                        raise HttpContractError(
                            "SMART_STORYBOARD_REQUEST_IN_PROGRESS", status_code=409
                        ) from error
                    if str(error) == "SMART_STORYBOARD_MODEL_FAILED":
                        raise HttpContractError(
                            "SMART_STORYBOARD_MODEL_FAILED",
                            status_code=503,
                            details=error.details,
                        ) from error
                    raise
            result_value = dependencies.service.create_storyboard(
                context,
                episode_id=_required_text(payload, "episodeId"),
                shots=drafts,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
            persisted = dependencies.service.get_storyboard(context, result_value.storyboard.storyboard_id)
            return _action_response(
                context,
                {
                    "shots": [project_shot(persisted, shot) for shot in persisted.shots],
                    "message": "已基于冻结剧本和资产快照创建分镜草案",
                },
                replayed=result_value.replayed,
                version=persisted.version,
            )
        if action == "generateShotMedia":
            _require_manage(context)
            generation = dependencies.generation_runtime
            if generation is None:
                raise HttpContractError("GENERATION_RUNTIME_UNAVAILABLE", status_code=503)
            storyboard = _storyboard_for_shots(
                _all_storyboards(dependencies, context), (_required_text(body, "targetId"),)
            )
            expected = _expected_version(request, payload)
            if storyboard.version != expected:
                raise VersionConflict("VERSION_CONFLICT", details={"current_version": storyboard.version})
            shot = next(item for item in storyboard.shots if item.shot_id == _required_text(body, "targetId"))
            if not shot.prompt.strip():
                raise HttpContractError("SHOT_PROMPT_REQUIRED")
            media_type = _required_text(payload, "mediaType")
            if media_type not in {"image", "video"}:
                raise HttpContractError("GENERATION_MEDIA_TYPE_INVALID")
            trusted = TrustedWorkspaceContext(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor_id=context.actor_id,
                request_id=context.request_id, permissions=context.permissions, role="",
            )
            generation_request = GenerationRequest(
                workspace_id=context.workspace_id, project_id=context.project_id,
                media_type=MediaType(media_type),
                capability="image_generation" if media_type == "image" else "video_generation",
                prompt=shot.prompt,
                parameters={
                    "storyboard_id": storyboard.storyboard_id, "storyboard_version": storyboard.version,
                    "shot_id": shot.shot_id, "negative_prompt": shot.negative_prompt,
                    "prompt_template_id": shot.prompt_template_id,
                    "prompt_model_adapter_version": shot.prompt_model_adapter_version,
                    "duration_ms": shot.duration_ms,
                },
                input_asset_ids=tuple(dict.fromkeys(item.asset_id for item in shot.asset_references)),
                requested_provider_id=_optional_text(payload, "providerId"),
                requested_model_id=_optional_text(payload, "modelId"),
            )
            try:
                task = await generation.submit(
                    trusted, context.project_id, generation_request,
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                )
            except (GenerationRuntimeUnavailable, RuntimeError, ValueError) as error:
                raise HttpContractError(str(error), status_code=503) from error
            recorded = dependencies.service.record_shot_generation_task(
                context, storyboard_id=storyboard.storyboard_id, expected_version=expected,
                shot_id=shot.shot_id, task_id=task.task_id,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
            return _action_response(
                context, {"task": _generation_task(task, context), "message": "镜头媒体生成任务已创建"},
                replayed=recorded.replayed, version=recorded.storyboard.version,
            )
        if action == "adoptShotMedia":
            _require_manage(context)
            generation = dependencies.generation_runtime
            if generation is None:
                raise HttpContractError("GENERATION_RUNTIME_UNAVAILABLE", status_code=503)
            storyboard = _storyboard_for_shots(
                _all_storyboards(dependencies, context), (_required_text(body, "targetId"),)
            )
            generated_asset_id = _required_text(payload, "generatedAssetId")
            task_id = _required_text(payload, "taskId")
            trusted = TrustedWorkspaceContext(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor_id=context.actor_id,
                request_id=context.request_id, permissions=context.permissions, role="",
            )
            try:
                generated_asset, _path = await generation.generated_artifact_file(
                    trusted, context.project_id, generated_asset_id
                )
                task = await generation.get_task(trusted, context.project_id, task_id)
            except (GenerationRuntimeUnavailable, RuntimeError, LookupError) as error:
                raise HttpContractError("GENERATED_ASSET_NOT_FOUND", status_code=404) from error
            parameters = task.request.parameters
            if (
                generated_asset.task_id != task_id
                or generated_asset_id not in task.output_asset_ids
                or parameters.get("storyboard_id") != storyboard.storyboard_id
                or parameters.get("shot_id") != _required_text(body, "targetId")
                or next(
                    item for item in storyboard.shots
                    if item.shot_id == _required_text(body, "targetId")
                ).generation_task_id != task_id
            ):
                raise HttpContractError("GENERATED_ASSET_SCOPE_MISMATCH", status_code=409)
            result_value = dependencies.service.replace_shot_media(
                context, storyboard_id=storyboard.storyboard_id,
                expected_version=_expected_version(request, payload),
                shot_id=_required_text(body, "targetId"), new_media_id=generated_asset_id,
                reason="generation_output_adopted", idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
            persisted = dependencies.service.get_storyboard(context, result_value.storyboard.storyboard_id)
            adopted = next(item for item in persisted.shots if item.shot_id == _required_text(body, "targetId"))
            return _action_response(
                context, {"shot": project_shot(persisted, adopted), "message": "生成结果已采用为镜头新版本"},
                replayed=result_value.replayed, version=persisted.version,
            )
        storyboard = (
            dependencies.service.get_storyboard(context, _required_text(payload, "storyboardId"))
            if action == "confirmStoryboard"
            else _storyboard_for_shots(_all_storyboards(dependencies, context), _action_shot_ids(action, body, payload))
        )
        expected_version = _expected_version(request, payload)
        if action == "batchUpdate":
            result_value = dependencies.service.batch_edit(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                edits=_batch_edits(payload),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "reorder":
            result_value = dependencies.service.reorder_shots(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                ordered_shot_ids=_string_list(payload.get("orderedShotIds"), code="ORDERED_SHOT_IDS_REQUIRED"),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "splitShot":
            result_value = dependencies.service.split_shot(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                shot_id=_required_text(body, "targetId"),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "mergeShots":
            result_value = dependencies.service.merge_shots(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                shot_ids=_string_list(payload.get("selectedShotIds"), code="ITEM_IDS_REQUIRED"),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "savePrompt":
            prompt_text = _required_text(payload, "prompt")
            negative_prompt = str(payload.get("negativePrompt") or "")
            template_id = str(payload.get("templateId") or "") or None
            variables = _string_mapping(payload.get("variables"))
            adapter_version = str(payload.get("modelAdapterVersion") or "") or None
            if template_id:
                repository = dependencies.prompt_repository
                template = None if repository is None else repository.get(context.scope, template_id)
                if template is None or template.archived_at is not None:
                    raise HttpContractError("PROMPT_TEMPLATE_NOT_FOUND", status_code=404)
                prompt_text, negative_prompt = template.render(variables, adapter_version=adapter_version)
            result_value = dependencies.service.batch_edit(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                edits=(ShotEdit(
                    shot_id=_required_text(body, "targetId"), prompt=prompt_text,
                    negative_prompt=negative_prompt, prompt_template_id=template_id,
                    prompt_variables=variables, prompt_model_adapter_version=adapter_version,
                ),),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "selectStoryboardCandidate":
            result_value = dependencies.service.select_shot_candidate(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                shot_id=_required_text(body, "targetId"),
                candidate_id=_required_text(payload, "candidateId"),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "restoreVersion":
            result_value = dependencies.service.restore_shot_version(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                shot_id=_required_text(body, "targetId"),
                source_version=_version_from_id(_required_text(payload, "versionId")),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "runQualityCheck":
            result_value = dependencies.service.run_quality_check(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "replaceShot":
            result_value = dependencies.service.replace_shot_media(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                shot_id=_required_text(body, "targetId"),
                new_media_id=_required_text(payload, "candidateId"),
                reason=str(payload.get("reason") or "candidate_selection"),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        elif action == "confirmStoryboard":
            result_value = dependencies.service.freeze_storyboard(
                context,
                storyboard_id=storyboard.storyboard_id,
                expected_version=expected_version,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        else:
            raise HttpContractError("UNSUPPORTED_SHOT_ACTION")
        # 所有写操作均从领域服务重新读取最终持久化状态，再投影给前端。
        persisted = dependencies.service.get_storyboard(context, result_value.storyboard.storyboard_id)
        if action in {"batchUpdate", "reorder", "splitShot", "mergeShots"}:
            changed_ids = _string_list(
                (
                    payload.get("itemIds")
                    if action == "batchUpdate"
                    else payload.get("orderedShotIds")
                    if action == "reorder"
                    else payload.get("selectedShotIds")
                    if action == "mergeShots"
                    else [body.get("targetId")]
                ),
                code="ITEM_IDS_REQUIRED" if action != "reorder" else "ORDERED_SHOT_IDS_REQUIRED",
            )
            return _action_response(
                context,
                {
                    "shots": [project_shot(persisted, shot) for shot in persisted.shots],
                    "batch": _batch(
                        request.headers.get("Idempotency-Key", ""), changed_ids, context.request_id, persisted.version
                    ),
                },
                replayed=result_value.replayed,
                version=persisted.version,
            )
        if action == "runQualityCheck":
            return _action_response(
                context,
                {
                    "shots": [project_shot(persisted, shot) for shot in persisted.shots],
                    "issues": [
                        issue for shot in persisted.shots for issue in project_shot(persisted, shot)["qualityIssues"]
                    ],
                },
                replayed=result_value.replayed,
                version=persisted.version,
            )
        shot_id = _required_text(body, "targetId") if action in {"replaceShot", "savePrompt", "selectStoryboardCandidate", "restoreVersion"} else None
        data: dict[str, object] = (
            {"shots": [project_shot(persisted, shot) for shot in persisted.shots]}
            if shot_id is None
            else {"shot": project_shot(persisted, next(shot for shot in persisted.shots if shot.shot_id == shot_id))}
        )
        return _action_response(context, data, replayed=result_value.replayed, version=persisted.version)

    @result.get("/projects/{project_id}/storyboards/{storyboard_id}/versions/compare")
    async def compare_versions(request: Request, project_id: str, storyboard_id: str):
        context = await _access_context(request, project_id, dependencies)
        comparison = dependencies.service.compare_versions(
            context,
            storyboard_id=storyboard_id,
            baseline_version=_positive_query(request, "baselineVersion"),
            candidate_version=_positive_query(request, "candidateVersion"),
        )
        return {
            "data": {
                "baselineVersion": comparison.baseline_version,
                "candidateVersion": comparison.candidate_version,
                "changedShotIds": list(comparison.changed_shot_ids),
                "fieldsByShot": {key: list(value) for key, value in comparison.fields_by_shot.items()},
            },
            "meta": {"requestId": context.request_id},
        }

    @result.post("/uploads")
    async def create_upload(request: Request):
        trusted = await _trusted_context(request, dependencies)
        _require_permission(trusted.permissions, "shot.manage")
        body = await _json_object(request)
        payload = _object(body.get("payload"), code="INVALID_ACTION_PAYLOAD")
        project_id = _required_text(payload, "projectId")
        await _authorize_project_scope(dependencies.project_scope_authorizer, trusted, project_id)
        context = _access_context_from_trusted(trusted, project_id)
        upload = dependencies.upload_store.create(
            context,
            filename=_required_text(payload, "fileName"),
            media_type=str(payload.get("mediaType") or "application/octet-stream"),
            size_bytes=_non_negative_payload_integer(payload, "sizeBytes"),
            content_sha256=_sha256_payload(payload, "sha256"),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
        return _action_response_for_request_id(
            trusted.request_id,
            {
                "uploadId": upload.upload_id,
                "uploadUrl": f"/api/v1/projects/{context.project_id}/storyboard-uploads/{upload.upload_id}/content",
            },
        )

    @result.put("/projects/{project_id}/storyboard-uploads/{upload_id}/content", status_code=204)
    async def put_upload(project_id: str, upload_id: str, request: Request):
        context = await _access_context(request, project_id, dependencies)
        _require_manage(context)
        if not dependencies.upload_store.put(context, upload_id, await request.body()):
            raise HttpContractError("UPLOAD_NOT_FOUND", status_code=404)
        return Response(status_code=204)

    @result.post("/projects/{project_id}/uploads/{upload_id}/complete")
    async def complete_upload(project_id: str, upload_id: str, request: Request):
        context = await _access_context(request, project_id, dependencies)
        _require_manage(context)
        if not dependencies.upload_store.complete(context, upload_id, idempotency_key=request.headers.get("Idempotency-Key", "")):
            raise HttpContractError("UPLOAD_NOT_FOUND", status_code=404)
        return _action_response_for_request_id(context.request_id, {"uploadId": upload_id, "status": "succeeded"})

    @result.get("/projects/{project_id}/storyboard-exports/{export_id}")
    async def download_export(project_id: str, export_id: str, request: Request):
        context = await _access_context(request, project_id, dependencies)
        _require_permission(context.permissions, "shot.view")
        exported = dependencies.upload_store.get_export(context, export_id)
        if exported is None:
            raise HttpContractError("EXPORT_NOT_FOUND", status_code=404)
        content, media_type, filename = exported
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return result


def create_unavailable_storyboard_router(code: str = "STORYBOARD_RUNTIME_UNAVAILABLE") -> APIRouter:
    """Keep the M05 surface deterministic when deployment prerequisites are absent."""
    result = APIRouter(route_class=StoryboardContractRoute)

    async def unavailable(request: Request) -> JSONResponse:
        return _error_response(request, code, 503, {})

    result.add_api_route("/projects/{project_id}/shots", unavailable, methods=["GET"])
    result.add_api_route("/projects/{project_id}/shots/{shot_id}", unavailable, methods=["GET"])
    result.add_api_route("/projects/{project_id}/shots/actions", unavailable, methods=["POST"])
    result.add_api_route(
        "/projects/{project_id}/prompt-templates",
        unavailable,
        methods=["GET"],
        operation_id="unavailable_prompt_templates_get",
    )
    result.add_api_route(
        "/projects/{project_id}/prompt-templates",
        unavailable,
        methods=["POST"],
        operation_id="unavailable_prompt_templates_post",
    )
    result.add_api_route(
        "/projects/{project_id}/prompt-templates/{prompt_id}",
        unavailable,
        methods=["PUT"],
        operation_id="unavailable_prompt_template_put",
    )
    result.add_api_route(
        "/projects/{project_id}/prompt-templates/{prompt_id}",
        unavailable,
        methods=["DELETE"],
        operation_id="unavailable_prompt_template_delete",
    )
    result.add_api_route("/projects/{project_id}/storyboard-audit", unavailable, methods=["GET"])
    result.add_api_route(
        "/projects/{project_id}/storyboards/{storyboard_id}/versions/compare", unavailable, methods=["GET"]
    )
    result.add_api_route("/uploads", unavailable, methods=["POST"])
    result.add_api_route("/projects/{project_id}/storyboard-uploads/{upload_id}/content", unavailable, methods=["PUT"])
    result.add_api_route("/projects/{project_id}/uploads/{upload_id}/complete", unavailable, methods=["POST"])
    result.add_api_route("/projects/{project_id}/storyboard-exports/{export_id}", unavailable, methods=["GET"])
    return result


class _ConfiguredDependencies:
    def __getattr__(self, name: str) -> object:
        if _configured_dependencies is None:
            raise RuntimeError("STORYBOARD_HTTP_DEPENDENCIES_NOT_CONFIGURED")
        return getattr(_configured_dependencies, name)


def configure_storyboard_http(dependencies: StoryboardHttpDependencies) -> APIRouter:
    global _configured_dependencies
    _configured_dependencies = dependencies
    return router


router = create_storyboard_router(cast(StoryboardHttpDependencies, _ConfiguredDependencies()))


async def _access_context(
    request: Request,
    project_id: str,
    dependencies: StoryboardHttpDependencies,
) -> AccessContext:
    trusted = await _trusted_context(request, dependencies)
    await _authorize_project_scope(dependencies.project_scope_authorizer, trusted, project_id)
    return _access_context_from_trusted(trusted, project_id)


def _access_context_from_trusted(trusted: TrustedWorkspaceContext, project_id: str) -> AccessContext:
    return AccessContext(
        tenant_id=trusted.tenant_id,
        workspace_id=trusted.workspace_id,
        project_id=project_id,
        actor_id=trusted.actor_id,
        request_id=trusted.request_id,
        permissions=trusted.permissions,
    )


async def _authorize_project_scope(
    authorizer: ProjectScopeAuthorizer | None, trusted: TrustedWorkspaceContext, project_id: str
) -> None:
    if authorizer is None:
        raise HttpContractError("PROJECT_SCOPE_AUTHORIZATION_UNAVAILABLE", status_code=503)
    try:
        allowed = authorizer(trusted, project_id)
        if inspect.isawaitable(allowed):
            allowed = await allowed
    except HttpContractError:
        raise
    except Exception as error:
        raise HttpContractError("PROJECT_SCOPE_AUTHORIZATION_UNAVAILABLE", status_code=503) from error
    if not allowed:
        raise HttpContractError("PROJECT_NOT_FOUND", status_code=404)


async def _trusted_context(request: Request, dependencies: StoryboardHttpDependencies) -> TrustedWorkspaceContext:
    resolver = dependencies.trusted_context_resolver
    if resolver is None:
        raise HttpContractError("IDENTITY_CONTEXT_UNAVAILABLE", status_code=503)
    resolved: object = resolver(request)
    if inspect.isawaitable(resolved):
        resolved = await resolved
    if not isinstance(resolved, TrustedWorkspaceContext):
        raise HttpContractError("IDENTITY_CONTEXT_INVALID", status_code=503)
    return resolved


def _all_storyboards(dependencies: StoryboardHttpDependencies, context: AccessContext) -> tuple[Storyboard, ...]:
    result: list[Storyboard] = []
    offset = 0
    while True:
        page = dependencies.service.list_storyboards(context, offset=offset, limit=200)
        result.extend(page.items)
        offset += len(page.items)
        if offset >= page.total or not page.items:
            return tuple(result)


def _matches(
    storyboard: Storyboard,
    shot_id: str,
    shot_number: str,
    prompt: str,
    search: str,
    status: str | None,
    has_issues: bool | None,
) -> bool:
    shot = next(item for item in storyboard.shots if item.shot_id == shot_id)
    if search and search not in f"{shot_id}\n{shot_number}\n{prompt}".casefold():
        return False
    if status and status not in {storyboard.status.value, shot.generation_status.value}:
        return False
    return has_issues is None or bool(shot.quality_issues) is has_issues


async def _json_object(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise HttpContractError("INVALID_JSON") from error
    return _object(value, code="INVALID_REQUEST_BODY")


def _object(value: object, *, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HttpContractError(code)
    return cast(Mapping[str, object], value)


def _required_text(value: Mapping[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise HttpContractError(f"{key.upper()}_REQUIRED")
    return raw


def _non_negative_payload_integer(value: Mapping[str, object], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise HttpContractError(f"{key.upper()}_REQUIRED")
    return raw


def _sha256_payload(value: Mapping[str, object], key: str) -> str:
    raw = _required_text(value, key).lower()
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise HttpContractError(f"INVALID_{key.upper()}")
    return raw


def _string_list(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise HttpContractError(code)
    return tuple(cast(str, item) for item in value)


def _optional_string_list(value: object, *, code: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise HttpContractError(code)
    result = tuple(cast(str, item) for item in value)
    if len(result) != len(set(result)):
        raise HttpContractError(code)
    return result


def _storyboard_for_shots(storyboards: Sequence[Storyboard], shot_ids: Sequence[str]) -> Storyboard:
    matched = [
        storyboard for storyboard in storyboards if set(shot_ids).issubset({shot.shot_id for shot in storyboard.shots})
    ]
    if not matched:
        raise HttpContractError("SHOT_NOT_FOUND", status_code=404)
    if len(matched) != 1:
        raise HttpContractError("SHOTS_MUST_SHARE_STORYBOARD")
    return matched[0]


def _expected_version(request: Request, payload: Mapping[str, object]) -> int:
    header = request.headers.get("If-Match")
    value: object = header.strip().strip('"') if header is not None else payload.get("baseVersion")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    raise HttpContractError("EXPECTED_VERSION_REQUIRED")


def _batch_edits(payload: Mapping[str, object]) -> tuple[ShotEdit, ...]:
    changes = _object(payload.get("changes"), code="CHANGES_REQUIRED")
    permitted = {
        "shotSize": "shot_size",
        "durationMs": "duration_ms",
        "dialogue": "dialogue",
        "cameraMove": "camera_movement",
        "prompt": "prompt",
        "modelPolicy": "model_strategy",
        "costTier": "cost_tier",
    }
    unexpected = set(changes) - set(permitted)
    if unexpected:
        raise HttpContractError("UNSUPPORTED_BATCH_CHANGE", details={"fields": sorted(unexpected)})
    if not changes:
        raise HttpContractError("CHANGES_REQUIRED")
    translated = {permitted[key]: value for key, value in changes.items()}
    try:
        return tuple(
            ShotEdit(shot_id=shot_id, **translated)
            for shot_id in _string_list(payload.get("itemIds"), code="ITEM_IDS_REQUIRED")
        )
    except TypeError as error:
        raise HttpContractError("INVALID_BATCH_CHANGE") from error


def _string_mapping(value: object) -> dict[str, str]:
    if value is None:
        return {}
    mapping = _object(value, code="INVALID_PROMPT_VARIABLE_VALUES")
    if not all(isinstance(item, str) for item in mapping.values()):
        raise HttpContractError("INVALID_PROMPT_VARIABLE_VALUES")
    return {key: str(item) for key, item in mapping.items()}


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    raw = value.get(key)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise HttpContractError(f"INVALID_{key.upper()}")
    return raw


def _generation_task(task: object, context: AccessContext) -> dict[str, object]:
    task_id = str(getattr(task, "task_id"))
    status = getattr(getattr(task, "status"), "value", str(getattr(task, "status")))
    return {
        "taskId": task_id, "projectId": context.project_id, "status": status,
        "statusUrl": f"/api/v1/generation-tasks/{task_id}?projectId={context.project_id}",
        "requestId": context.request_id,
    }


async def _with_generation_facts(
    projected: dict[str, object],
    context: AccessContext,
    dependencies: StoryboardHttpDependencies,
) -> dict[str, object]:
    task_id = projected.get("generationTaskId")
    runtime = dependencies.generation_runtime
    if not isinstance(task_id, str) or not task_id or runtime is None:
        return projected
    trusted = TrustedWorkspaceContext(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor_id=context.actor_id,
        request_id=context.request_id,
        permissions=context.permissions,
        role="",
    )
    try:
        task = await runtime.get_task(trusted, context.project_id, task_id)
        billing = await runtime.billing_for_task(trusted, context.project_id, task_id)
    except (GenerationRuntimeUnavailable, RuntimeError, LookupError):
        return projected
    enriched = {
        **projected,
        "generationStatus": task.status.value,
        "generationFailure": task.failure.message if task.failure else None,
    }
    if billing is not None:
        enriched.update(
            {
                "generationCurrency": billing.get("currency"),
                "estimatedCostMinor": billing.get("estimated_minor"),
                "actualCostMinor": billing.get("actual_minor"),
                "billingStatus": billing.get("status"),
            }
        )
    return enriched


def _action_shot_ids(action: str, body: Mapping[str, object], payload: Mapping[str, object]) -> tuple[str, ...]:
    if action in {"batchUpdate", "runQualityCheck"}:
        return _string_list(payload.get("itemIds") or payload.get("shotIds"), code="ITEM_IDS_REQUIRED")
    if action == "reorder":
        return _string_list(payload.get("orderedShotIds"), code="ORDERED_SHOT_IDS_REQUIRED")
    if action in {"replaceShot", "splitShot", "savePrompt", "selectStoryboardCandidate", "restoreVersion"}:
        return (_required_text(body, "targetId"),)
    if action == "mergeShots":
        return _string_list(payload.get("selectedShotIds"), code="ITEM_IDS_REQUIRED")
    raise HttpContractError("UNSUPPORTED_SHOT_ACTION")


def _version_from_id(value: str) -> int:
    marker = "-v"
    if marker not in value:
        raise HttpContractError("INVALID_VERSION_ID")
    number = value.rsplit(marker, 1)[1].split("-", 1)[0]
    if not number.isdigit() or int(number) < 1:
        raise HttpContractError("INVALID_VERSION_ID")
    return int(number)


def _require_manage(context: AccessContext) -> None:
    _require_permission(context.permissions, "shot.manage")


def _require_permission(permissions: frozenset[str], permission: str) -> None:
    if permission not in permissions:
        raise PermissionDenied("PERMISSION_DENIED")


def _upload(dependencies: StoryboardHttpDependencies, context: AccessContext, upload_id: str):
    upload = dependencies.upload_store.get(context, upload_id)
    if upload is None:
        raise HttpContractError("UPLOAD_NOT_FOUND", status_code=404)
    return upload


def _batch(
    operation_id: str, item_ids: Sequence[str], request_id: str, version: int | None = None
) -> dict[str, object]:
    return {
        "operationId": operation_id,
        "items": [
            {"id": item_id, "status": "succeeded", **({"version": version} if version else {})} for item_id in item_ids
        ],
        "succeeded": len(item_ids),
        "failed": 0,
        "requestId": request_id,
    }


def _action_response(
    context: AccessContext, data: dict[str, object], *, replayed: bool = False, version: int | None = None
) -> JSONResponse:
    response = JSONResponse(
        content={
            "data": data,
            "meta": {"requestId": context.request_id, **({"collectionVersion": version} if version else {})},
        },
        headers={"X-Request-Id": context.request_id},
    )
    if replayed:
        response.headers["X-Idempotency-Replayed"] = "true"
    return response


def _action_response_for_request_id(request_id: str, data: dict[str, object]) -> JSONResponse:
    return JSONResponse(content={"data": data, "meta": {"requestId": request_id}}, headers={"X-Request-Id": request_id})


def _data_response(request_id: str, data: object, *, replayed: bool = False) -> JSONResponse:
    response = JSONResponse(content={"data": data, "meta": {"requestId": request_id}}, headers={"X-Request-Id": request_id})
    if replayed:
        response.headers["X-Idempotency-Replayed"] = "true"
    return response


def _positive_query(request: Request, key: str) -> int:
    return _bounded_integer(
        request.query_params.get(key), default=0, minimum=1, maximum=2**31 - 1, code=f"INVALID_{key.upper()}"
    )


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value.casefold() == "true":
        return True
    if value.casefold() == "false":
        return False
    raise HttpContractError("INVALID_HAS_ISSUES")


def _non_negative_integer(value: str | None, *, default: int, code: str) -> int:
    parsed = _integer(value, default=default, code=code)
    if parsed < 0:
        raise HttpContractError(code)
    return parsed


def _bounded_integer(value: str | None, *, default: int, minimum: int, maximum: int, code: str) -> int:
    parsed = _integer(value, default=default, code=code)
    if not minimum <= parsed <= maximum:
        raise HttpContractError(code)
    return parsed


def _integer(value: str | None, *, default: int, code: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise HttpContractError(code) from error


def _storyboard_error_response(request: Request, error: StoryboardError) -> JSONResponse:
    if isinstance(error, PermissionDenied):
        return _error_response(request, error.code, 403, {})
    if isinstance(error, StoryboardNotFound):
        return _error_response(request, error.code, 404, {})
    if isinstance(error, (VersionConflict, IdempotencyConflict)):
        return _error_response(request, error.code, 409, error.details)
    if isinstance(error, ContractViolation):
        return _error_response(request, error.code, 400, error.details)
    return _error_response(request, error.code, 422, error.details)


def _http_exception_response(request: Request, error: HTTPException) -> JSONResponse:
    detail = error.detail
    code = detail.get("code") if isinstance(detail, Mapping) else None
    if not isinstance(code, str) or not code:
        code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 503: "IDENTITY_CONTEXT_UNAVAILABLE"}.get(
            error.status_code, "HTTP_ERROR"
        )
    return _error_response(request, code, error.status_code, {})


def _error_response(
    request: Request,
    code: str,
    status_code: int,
    details: dict[str, object],
) -> JSONResponse:
    request_id = (request.headers.get("X-Request-Id") or "").strip() or str(uuid.uuid4())
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": code, "retryable": status_code >= 500, "details": details},
            "meta": {"requestId": request_id},
        },
        headers={"X-Request-Id": request_id},
    )
