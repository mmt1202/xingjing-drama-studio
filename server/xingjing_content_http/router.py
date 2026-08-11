# pyright: reportMissingImports=false
from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from server.xingjing_content import (
    ApplyAnalysisCommand,
    ContentConflict,
    ContentService,
    DirectorProfile,
    ImportScriptCommand,
    ReviseScriptCommand,
    SetDirectorProfileCommand,
)
from server.xingjing_content.ingest import extract_script_text
from server.xingjing_content.models import ScriptDocument, ScriptVersion
from server.xingjing_identity_context import TrustedWorkspaceContext

from .dependencies import ContentHttpDependencies, ContentRuntimeUnavailable

_configured_dependencies: ContentHttpDependencies | None = None


class ContentHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})
        super().__init__(code)


class ContentContractRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except ContentHttpError as error:
                return _error_response(request, error.code, error.status_code, error.details)
            except ContentRuntimeUnavailable as error:
                return _error_response(request, error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
            except SQLAlchemyError:
                return _error_response(request, "CONTENT_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
            except ContentConflict as error:
                return _error_response(request, str(error), status.HTTP_409_CONFLICT)
            except KeyError as error:
                code = str(error).strip("'")
                return _error_response(request, code or "SCRIPT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, Mapping) else {}
                raw_code = detail.get("code")
                code = raw_code if isinstance(raw_code, str) else "REQUEST_REJECTED"
                return _error_response(request, code, error.status_code)
            except ValueError as error:
                code = str(error) or "INVALID_REQUEST"
                if code == "CONTENT_AI_REQUEST_IN_PROGRESS":
                    return _error_response(request, code, status.HTTP_409_CONFLICT)
                if code == "CONTENT_AI_MODEL_FAILED":
                    return _error_response(request, code, status.HTTP_502_BAD_GATEWAY)
                if code in {"SCRIPT_FROZEN", "SCRIPT_NOT_FROZEN"}:
                    return _error_response(request, code, status.HTTP_409_CONFLICT)
                return _error_response(request, code, status.HTTP_400_BAD_REQUEST)

        return handler


def create_content_router(dependencies: ContentHttpDependencies) -> APIRouter:
    result = APIRouter(route_class=ContentContractRoute)

    @result.get("/projects/{project_id}/scripts")
    async def list_scripts(request: Request, project_id: str):
        context = await _context(request, project_id, dependencies, "script.view")
        service = dependencies.service_for(context)
        offset = _integer(request.query_params.get("offset"), default=0, minimum=0, code="INVALID_OFFSET")
        limit = _integer(request.query_params.get("limit"), default=50, minimum=1, maximum=200, code="INVALID_LIMIT")
        locked_raw = request.query_params.get("locked")
        if locked_raw not in {None, "true", "false"}:
            raise ContentHttpError("INVALID_LOCKED_FILTER")
        page = service.list_scripts(
            workspace_id=context.workspace_id,
            project_id=project_id,
            query=request.query_params.get("q"),
            locked=None if locked_raw is None else locked_raw == "true",
            offset=offset,
            limit=limit,
        )
        return _success(context, {"scripts": [_script(item) for item in page.items]}, total=page.total)

    @result.get("/projects/{project_id}/scripts/{script_id}")
    async def get_script(request: Request, project_id: str, script_id: str):
        context = await _context(request, project_id, dependencies, "script.view")
        document = _project_script(dependencies.service_for(context), context, project_id, script_id)
        baseline = request.query_params.get("baselineVersion")
        candidate = request.query_params.get("candidateVersion")
        data: dict[str, object] = {"script": _script(document)}
        if baseline is not None or candidate is not None:
            data["comparison"] = _compare_versions(
                document,
                _integer(baseline, minimum=1, code="INVALID_BASELINE_VERSION"),
                _integer(candidate, minimum=1, code="INVALID_CANDIDATE_VERSION"),
            )
        return _success(context, data)

    @result.get("/projects/{project_id}/scripts/{script_id}/audit")
    async def get_script_audit(request: Request, project_id: str, script_id: str):
        context = await _context(request, project_id, dependencies, "script.manage")
        service = dependencies.service_for(context)
        _project_script(service, context, project_id, script_id)
        limit = _integer(request.query_params.get("limit"), default=100, minimum=1, maximum=500, code="INVALID_LIMIT")
        events = service.list_audit(
            workspace_id=context.workspace_id,
            project_id=project_id,
            script_id=script_id,
            request_id=request.query_params.get("requestId"),
            actor_id=request.query_params.get("actorId"),
            limit=limit,
        )
        return _success(context, {"events": list(events)}, total=len(events))

    @result.post("/projects/{project_id}/scripts/actions")
    async def script_actions(request: Request, project_id: str):
        body = await _json_object(request)
        action = _required_text(body, "action")
        permission = "script.view" if action == "compareVersions" else "script.manage"
        context = await _context(request, project_id, dependencies, permission)
        service = dependencies.service_for(context)
        payload = _object(body.get("payload"), "INVALID_ACTION_PAYLOAD")
        if action == "compareVersions":
            document = _project_script(service, context, project_id, _required_text(body, "targetId"))
            return _success(
                context,
                {
                    "comparison": _compare_versions(
                        document, _positive(payload, "baselineVersion"), _positive(payload, "candidateVersion")
                    )
                },
            )
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise ContentHttpError("IDEMPOTENCY_KEY_REQUIRED")
        if action == "import":
            document = service.import_script(
                ImportScriptCommand(
                    context.workspace_id,
                    project_id,
                    _required_text(payload, "title"),
                    _required_text(payload, "filename"),
                    _required_text(payload, "mediaType"),
                    _required_text(payload, "content"),
                    key,
                )
            )
        else:
            script_id = _required_text(body, "targetId")
            _project_script(service, context, project_id, script_id)
            expected = _if_match(request)
            if action == "revise":
                document = service.revise_script(
                    ReviseScriptCommand(
                        context.workspace_id,
                        script_id,
                        expected,
                        _required_text(payload, "content"),
                        key,
                        _required_text(payload, "changeSummary"),
                    )
                )
            elif action == "setDirectorProfile":
                document = service.set_director_profile(
                    SetDirectorProfileCommand(context.workspace_id, script_id, expected, key, _profile(payload))
                )
            elif action == "freeze":
                document = service.freeze_script(
                    workspace_id=context.workspace_id,
                    script_id=script_id,
                    expected_revision=expected,
                    idempotency_key=key,
                )
            elif action == "analyze":
                analysis = await dependencies.ai_service_for(context, project_id).analyze(
                    _project_script(service, context, project_id, script_id),
                    expected_revision=expected,
                    idempotency_key=key,
                )
                document = service.apply_analysis(
                    ApplyAnalysisCommand(
                        context.workspace_id,
                        script_id,
                        expected,
                        key,
                        analysis,
                    )
                )
            elif action == "generateDirector":
                profile = await dependencies.ai_service_for(context, project_id).direct(
                    _project_script(service, context, project_id, script_id),
                    expected_revision=expected,
                    idempotency_key=key,
                )
                document = service.set_director_profile(
                    SetDirectorProfileCommand(context.workspace_id, script_id, expected, key, profile)
                )
            elif action == "rewrite":
                content, summary = await dependencies.ai_service_for(context, project_id).rewrite(
                    _project_script(service, context, project_id, script_id),
                    expected_revision=expected,
                    instruction=_required_text(payload, "instruction"),
                    idempotency_key=key,
                )
                document = service.revise_script(
                    ReviseScriptCommand(
                        context.workspace_id,
                        script_id,
                        expected,
                        content,
                        key,
                        summary,
                    )
                )
            else:
                raise ContentHttpError("UNSUPPORTED_SCRIPT_ACTION")
        return _success(context, {"script": _script(document)})

    @result.post("/projects/{project_id}/scripts/imports")
    async def import_script_file(
        request: Request,
        project_id: str,
        title: str = Form(...),
        file: UploadFile = File(...),
    ):
        context = await _context(request, project_id, dependencies, "script.manage")
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise ContentHttpError("IDEMPOTENCY_KEY_REQUIRED")
        filename = (file.filename or "").strip()
        if not filename:
            raise ContentHttpError("FILENAME_REQUIRED")
        raw = await file.read(20 * 1024 * 1024 + 1)
        content, media_type = extract_script_text(filename, raw)
        document = dependencies.service_for(context).import_script(
            ImportScriptCommand(
                context.workspace_id,
                project_id,
                title.strip() or filename.rsplit(".", 1)[0],
                filename,
                media_type,
                content,
                key,
            )
        )
        return _success(context, {"script": _script(document)})

    return result


class _ConfiguredDependencies:
    def __getattr__(self, name: str) -> object:
        if _configured_dependencies is None:
            raise ContentHttpError("CONTENT_HTTP_DEPENDENCIES_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
        return getattr(_configured_dependencies, name)


def configure_content_http(dependencies: ContentHttpDependencies) -> APIRouter:
    global _configured_dependencies
    _configured_dependencies = dependencies
    return router


router = create_content_router(cast(ContentHttpDependencies, _ConfiguredDependencies()))


async def _context(
    request: Request, project_id: str, dependencies: ContentHttpDependencies, permission: str
) -> TrustedWorkspaceContext:
    if dependencies.unavailable_code is not None:
        raise ContentRuntimeUnavailable(dependencies.unavailable_code)
    context = await dependencies.trusted_context_resolver(request)
    if permission not in context.permissions:
        raise ContentHttpError("PERMISSION_DENIED", status.HTTP_403_FORBIDDEN, {"permission": permission})
    if not await dependencies.project_scope_authorizer(context, project_id):
        raise ContentHttpError("PROJECT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return context


def _project_script(
    service: ContentService, context: TrustedWorkspaceContext, project_id: str, script_id: str
) -> ScriptDocument:
    document = service.get_script(workspace_id=context.workspace_id, script_id=script_id)
    if document.project_id != project_id:
        raise ContentHttpError("SCRIPT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return document


async def _json_object(request: Request) -> Mapping[str, object]:
    try:
        return _object(await request.json(), "INVALID_REQUEST_BODY")
    except ValueError as error:
        raise ContentHttpError("INVALID_JSON") from error


def _object(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContentHttpError(code)
    return cast(Mapping[str, object], value)


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ContentHttpError(f"{key.upper()}_REQUIRED")
    return item.strip()


def _integer(
    value: str | None, *, default: int | None = None, minimum: int, maximum: int = 2**31 - 1, code: str
) -> int:
    if value is None and default is not None:
        return default
    if value is None or not value.isdigit():
        raise ContentHttpError(code)
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ContentHttpError(code)
    return parsed


def _positive(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContentHttpError(f"{key.upper()}_REQUIRED")
    return value


def _if_match(request: Request) -> int:
    raw = request.headers.get("If-Match", "").strip().strip('"')
    return _integer(raw, minimum=1, code="EXPECTED_VERSION_REQUIRED")


def _profile(payload: Mapping[str, object]) -> DirectorProfile:
    profile = _object(payload.get("profile", payload), "INVALID_DIRECTOR_PROFILE")
    allowed = {"audience", "pacing", "visualStyle", "cameraLanguage", "productionConstraints"}
    unexpected = set(profile) - allowed
    if unexpected:
        raise ContentHttpError("INVALID_DIRECTOR_PROFILE", details={"fields": sorted(unexpected)})
    constraints = profile.get("productionConstraints", [])
    if not isinstance(constraints, list) or any(not isinstance(item, str) for item in constraints):
        raise ContentHttpError("INVALID_DIRECTOR_PROFILE")
    values = {key: profile.get(key) for key in ("audience", "pacing", "visualStyle", "cameraLanguage")}
    if any(value is not None and not isinstance(value, str) for value in values.values()):
        raise ContentHttpError("INVALID_DIRECTOR_PROFILE")
    return DirectorProfile(
        audience=cast(str | None, values["audience"]),
        pacing=cast(str | None, values["pacing"]),
        visual_style=cast(str | None, values["visualStyle"]),
        camera_language=cast(str | None, values["cameraLanguage"]),
        production_constraints=tuple(cast(str, item) for item in constraints),
    )


def _script(document: ScriptDocument) -> dict[str, object]:
    return {
        "id": document.script_id,
        "projectId": document.project_id,
        "title": document.title,
        "revision": document.revision,
        "sourceDocument": asdict(document.source_document),
        "versions": [_version_payload(item) for item in document.versions],
        "lockedVersionNumber": document.locked_version_number,
        "directorProfile": {
            "audience": document.director_profile.audience,
            "pacing": document.director_profile.pacing,
            "visualStyle": document.director_profile.visual_style,
            "cameraLanguage": document.director_profile.camera_language,
            "productionConstraints": list(document.director_profile.production_constraints),
        },
    }


def _version_payload(version: ScriptVersion) -> dict[str, object]:
    analysis = version.analysis
    return {
        "number": version.number,
        "sourceContent": version.source_content,
        "scenes": [
            {
                "sceneId": scene.scene_id,
                "heading": {
                    "location": scene.heading.location,
                    "timeOfDay": scene.heading.time_of_day,
                    "setting": scene.heading.setting,
                },
                "paragraphs": [
                    {
                        "paragraphId": paragraph.paragraph_id,
                        "kind": paragraph.kind,
                        "text": paragraph.text,
                        "sourceText": paragraph.source_text,
                        "speaker": paragraph.speaker,
                    }
                    for paragraph in scene.paragraphs
                ],
            }
            for scene in version.scenes
        ],
        "sourceMappings": [
            {
                "targetId": mapping.target_id,
                "sourceStart": mapping.source_start,
                "sourceEnd": mapping.source_end,
            }
            for mapping in version.source_mappings
        ],
        "validationErrors": [
            {
                "code": issue.code,
                "field": issue.path,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in version.validation_errors
        ],
        "changeSummary": version.change_summary,
        "analysis": {
            "wordCount": analysis.word_count,
            "characterCount": analysis.character_count,
            "sceneCount": analysis.scene_count,
            "dialogueCount": analysis.dialogue_count,
            "estimatedDurationMs": analysis.estimated_duration_ms,
            "estimatedEpisodeCount": analysis.estimated_episode_count,
            "sensitiveTerms": list(analysis.sensitive_terms),
            "characters": [asdict(item) for item in analysis.characters],
            "locations": list(analysis.locations),
            "props": list(analysis.props),
            "relationships": [asdict(item) for item in analysis.relationships],
            "storyBeats": [
                {"label": item.label, "summary": item.summary, "sceneIds": list(item.scene_ids)}
                for item in analysis.story_beats
            ],
            "episodeSuggestions": list(analysis.episode_suggestions),
        },
    }


def _compare_versions(document: ScriptDocument, baseline_number: int, candidate_number: int) -> dict[str, object]:
    baseline = _version(document, baseline_number)
    candidate = _version(document, candidate_number)
    changed = [
        name
        for name in ("sourceContent", "scenes", "sourceMappings", "validationErrors", "changeSummary", "analysis")
        if _version_value(baseline, name) != _version_value(candidate, name)
    ]
    return {"baselineVersion": baseline_number, "candidateVersion": candidate_number, "changedFields": changed}


def _version(document: ScriptDocument, number: int) -> ScriptVersion:
    result = next((item for item in document.versions if item.number == number), None)
    if result is None:
        raise ContentHttpError("VERSION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return result


def _version_value(version: ScriptVersion, name: str) -> object:
    values = {
        "sourceContent": version.source_content,
        "scenes": version.scenes,
        "sourceMappings": version.source_mappings,
        "validationErrors": version.validation_errors,
        "changeSummary": version.change_summary,
        "analysis": version.analysis,
    }
    return values[name]


def _success(context: TrustedWorkspaceContext, data: Mapping[str, object], *, total: int | None = None) -> JSONResponse:
    meta: dict[str, object] = {"requestId": context.request_id}
    if total is not None:
        meta["total"] = total
    return JSONResponse({"data": data, "meta": meta}, headers={"X-Request-Id": context.request_id})


def _error_response(
    request: Request, code: str, status_code: int, details: Mapping[str, object] | None = None
) -> JSONResponse:
    request_id = (request.headers.get("X-Request-Id") or str(uuid.uuid4())).strip()
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": _error_message(code),
                "retryable": status_code >= 500 or code == "CONTENT_AI_REQUEST_IN_PROGRESS",
                "details": dict(details or {}),
            },
            "meta": {"requestId": request_id},
        },
        status_code=status_code,
        headers={"X-Request-Id": request_id},
    )


def _error_message(code: str) -> str:
    messages = {
        "CONTENT_AI_MODEL_FAILED": "AI 模型调用失败，请检查模型配置后重试。",
        "CONTENT_AI_REQUEST_IN_PROGRESS": "相同操作正在处理中，请稍后刷新。",
        "SCRIPT_FROZEN": "剧本已冻结，不能再修改正文或结构。",
        "SCRIPT_NOT_FROZEN": "请先冻结当前剧本版本，再生成导演方案。",
        "VERSION_CONFLICT": "剧本已被其他操作更新，请刷新后重新提交。",
        "PERMISSION_DENIED": "当前账号没有执行此操作的权限。",
        "SCRIPT_FILE_TYPE_UNSUPPORTED": "仅支持 TXT、Markdown 和 DOCX 文件。",
        "SCRIPT_FILE_TOO_LARGE": "剧本文件超过 20MB 上限。",
        "DOCX_INVALID": "DOCX 文件损坏或格式不受支持。",
    }
    return messages.get(code, code)
