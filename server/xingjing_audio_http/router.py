"""Trusted M07 HTTP contract.

The route exposes only persisted tracks and explicitly requested actions.  It
does not infer a Provider request from a UI button, and never returns a fake
audio or subtitle artifact when the provider or object storage is absent.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute

from server.xingjing_audio_persistence.repository import (
    AudioBillingError,
    AudioPersistenceConflict,
    AudioPersistenceNotFound,
    IdempotencyConflict,
    VersionConflict,
)
from server.xingjing_audio_runtime import AudioRuntime, AudioRuntimeUnavailable


class AudioHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class AudioContractRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> JSONResponse:
            try:
                return cast(JSONResponse, await original(request))
            except AudioHttpError as error:
                return _error(request, error.code, error.status_code)
            except AudioRuntimeUnavailable as error:
                return _error(request, error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
            except PermissionError:
                return _error(request, "PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)
            except LookupError:
                return _error(request, "AUDIO_RESOURCE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except AudioPersistenceNotFound:
                return _error(request, "AUDIO_RESOURCE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except (VersionConflict, IdempotencyConflict, AudioPersistenceConflict):
                return _error(request, "AUDIO_VERSION_OR_IDEMPOTENCY_CONFLICT", status.HTTP_409_CONFLICT)
            except AudioBillingError as error:
                code = str(error) or "AUDIO_BILLING_REJECTED"
                status_code = status.HTTP_402_PAYMENT_REQUIRED if code == "AUDIO_INSUFFICIENT_CREDITS" else status.HTTP_409_CONFLICT
                return _error(request, code, status_code)
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, Mapping) else {}
                raw_code = detail.get("code")
                code = raw_code if isinstance(raw_code, str) else "REQUEST_REJECTED"
                return _error(request, code, error.status_code)
            except ValueError as error:
                return _error(request, str(error) or "INVALID_AUDIO_REQUEST", status.HTTP_400_BAD_REQUEST)

        return handler


def create_audio_router(runtime: AudioRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["xingjing-audio"], route_class=AudioContractRoute)

    @router.get("/projects/{project_id}/audio-tracks")
    async def list_audio_tracks(request: Request, project_id: str) -> JSONResponse:
        scope, context = await runtime.scope_for(request, project_id, "audio.view")
        offset = _integer(request.query_params.get("offset"), default=0, minimum=0, maximum=2**31 - 1, code="INVALID_OFFSET")
        limit = _integer(request.query_params.get("limit"), default=50, minimum=1, maximum=100, code="INVALID_LIMIT")
        audio_tracks, subtitle_tracks, total = await runtime.list_tracks(
            scope,
            track_kind=_enum_query(request, "trackKind", {"dialogue", "voiceover", "bgm", "sfx", "mix"}),
            state=_enum_query(request, "state", {"draft", "ready", "archived"}),
            language=_query_text(request, "language"),
            query=_query_text(request, "q"),
            offset=offset,
            limit=limit,
        )
        return _success(
            context.request_id,
            {"audioTracks": jsonable_encoder(audio_tracks), "subtitleTracks": jsonable_encoder(subtitle_tracks)},
            total=total,
        )

    @router.get("/projects/{project_id}/audio-tasks")
    async def list_audio_tasks(request: Request, project_id: str) -> JSONResponse:
        scope, context = await runtime.scope_for(request, project_id, "audio.view")
        offset = _integer(request.query_params.get("offset"), default=0, minimum=0, maximum=2**31 - 1, code="INVALID_OFFSET")
        limit = _integer(request.query_params.get("limit"), default=50, minimum=1, maximum=100, code="INVALID_LIMIT")
        tasks, total = await runtime.list_media_tasks(
            scope,
            status=_enum_query(
                request,
                "status",
                {"pending", "queued", "running", "cancelling", "retrying", "succeeded", "failed", "cancelled", "timed_out"},
            ),
            task_kind=_enum_query(request, "taskKind", {"audio", "subtitle", "lip_sync", "mix"}),
            query=_query_text(request, "q"),
            offset=offset,
            limit=limit,
        )
        return _success(context.request_id, {"tasks": jsonable_encoder(tasks)}, total=total)

    @router.get("/projects/{project_id}/audio-audit-events")
    async def list_audio_audit_events(request: Request, project_id: str) -> JSONResponse:
        scope, context = await runtime.scope_for(request, project_id, "audio.view")
        offset = _integer(request.query_params.get("offset"), default=0, minimum=0, maximum=2**31 - 1, code="INVALID_OFFSET")
        limit = _integer(request.query_params.get("limit"), default=50, minimum=1, maximum=100, code="INVALID_LIMIT")
        events, total = await runtime.list_audit_events(
            scope,
            request_id=_query_text(request, "requestId"),
            actor_id=_query_text(request, "actorId"),
            object_type=_query_text(request, "objectType"),
            object_id=_query_text(request, "objectId"),
            offset=offset,
            limit=limit,
        )
        return _success(context.request_id, {"events": jsonable_encoder(events)}, total=total)

    @router.get("/projects/{project_id}/lip-sync-versions")
    async def list_lip_sync_versions(request: Request, project_id: str) -> JSONResponse:
        scope, context = await runtime.scope_for(request, project_id, "audio.view")
        offset = _integer(request.query_params.get("offset"), default=0, minimum=0, maximum=2**31 - 1, code="INVALID_OFFSET")
        limit = _integer(request.query_params.get("limit"), default=50, minimum=1, maximum=100, code="INVALID_LIMIT")
        versions, total = await runtime.list_lip_sync_versions(scope, offset=offset, limit=limit)
        return _success(context.request_id, {"versions": jsonable_encoder(versions)}, total=total)

    @router.get("/projects/{project_id}/audio-delivery-snapshot")
    async def audio_delivery_snapshot(request: Request, project_id: str) -> JSONResponse:
        scope, context = await runtime.scope_for(request, project_id, "audio.view")
        snapshot = await runtime.delivery_snapshot(scope)
        return _success(context.request_id, jsonable_encoder(snapshot))

    @router.get("/projects/{project_id}/audio-objects/{object_key:path}")
    async def audio_object_content(request: Request, project_id: str, object_key: str) -> FileResponse:
        scope, _ = await runtime.scope_for(request, project_id, "audio.view")
        path, media_type = await runtime.media_object_file(scope, object_key)
        return FileResponse(path, media_type=media_type)

    @router.get("/projects/{project_id}/{track_kind}-tracks/{track_id}/versions")
    async def list_track_versions(request: Request, project_id: str, track_kind: str, track_id: str) -> JSONResponse:
        scope, context = await runtime.scope_for(request, project_id, "audio.view")
        offset = _integer(request.query_params.get("offset"), default=0, minimum=0, maximum=2**31 - 1, code="INVALID_OFFSET")
        limit = _integer(request.query_params.get("limit"), default=50, minimum=1, maximum=100, code="INVALID_LIMIT")
        versions, total = await runtime.list_track_versions(
            scope, track_kind=track_kind, track_id=track_id, offset=offset, limit=limit
        )
        return _success(context.request_id, {"versions": jsonable_encoder(versions)}, total=total)

    @router.post("/projects/{project_id}/audio-actions")
    async def audio_action(request: Request, project_id: str) -> JSONResponse:
        scope, context = await runtime.scope_for(request, project_id, "audio.manage")
        body = await _json_object(request)
        key = _idempotency_key(request)
        result = await _dispatch_action(runtime, scope, context, body, key, request)
        return _success(context.request_id, {"result": jsonable_encoder(result)})

    @router.post("/audio-providers/callbacks")
    async def audio_provider_callback(request: Request) -> JSONResponse:
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise AudioHttpError("INVALID_JSON") from error
        if not isinstance(payload, Mapping):
            raise AudioHttpError("INVALID_REQUEST_BODY")
        task = await runtime.apply_provider_callback(
            raw_body=raw_body,
            signature=request.headers.get("X-Xingjing-Provider-Signature", ""),
            payload=cast(Mapping[str, object], payload),
        )
        return _success(
            request.headers.get("X-Request-Id", "audio-provider-callback"),
            {"task": jsonable_encoder(task)},
        )

    return router


async def _dispatch_action(
    runtime: AudioRuntime,
    scope: Any,
    context: Any,
    body: Mapping[str, object],
    idempotency_key: str,
    request: Request,
) -> Mapping[str, Any]:
    action = _text(body, "action")
    if action == "create_audio_track":
        return await runtime.create_audio_track(
            scope, context, track_id=_text(body, "trackId"), track_kind=_text(body, "trackKind"), title=_text(body, "title"), idempotency_key=idempotency_key
        )
    if action == "create_subtitle_track":
        return await runtime.create_subtitle_track(
            scope,
            context,
            track_id=_text(body, "trackId"),
            language=_text(body, "language"),
            format=_text(body, "format"),
            title=_text(body, "title"),
            idempotency_key=idempotency_key,
        )
    if action == "append_audio_version":
        return await runtime.append_audio_version(
            scope,
            context,
            track_id=_text(body, "trackId"),
            version_id=_text(body, "versionId"),
            expected_version=_expected_version(request, body),
            cue_payload=_object(body, "cuePayload"),
            object_key=_text(body, "objectKey"),
            source_task_id=_optional_text(body, "sourceTaskId"),
            idempotency_key=idempotency_key,
        )
    if action == "create_lip_sync_version":
        return await runtime.create_lip_sync_version(
            scope,
            context,
            version_id=_text(body, "versionId"),
            audio_version_id=_text(body, "audioVersionId"),
            subtitle_version_id=_optional_text(body, "subtitleVersionId"),
            input_video_key=_text(body, "inputVideoKey"),
            calibration=_object(body, "calibration"),
            idempotency_key=idempotency_key,
        )
    if action == "check_lip_sync_inputs":
        return await runtime.check_lip_sync_inputs(
            scope,
            audio_version_id=_text(body, "audioVersionId"),
            subtitle_version_id=_optional_text(body, "subtitleVersionId"),
            input_video_key=_text(body, "inputVideoKey"),
            calibration=_object(body, "calibration"),
        )
    if action == "complete_lip_sync_version":
        return await runtime.complete_lip_sync_version(
            scope,
            context,
            version_id=_text(body, "versionId"),
            expected_version=_expected_version(request, body),
            source_task_id=_text(body, "sourceTaskId"),
            output_key=_text(body, "outputKey"),
            idempotency_key=idempotency_key,
        )
    if action == "create_lip_sync_fallback":
        return await runtime.create_lip_sync_fallback(
            scope,
            context,
            version_id=_text(body, "versionId"),
            fallback_of_id=_text(body, "fallbackOfId"),
            idempotency_key=idempotency_key,
        )
    if action == "select_lip_sync_version":
        return await runtime.select_lip_sync_version(
            scope,
            context,
            version_id=_text(body, "versionId"),
            expected_version=_expected_version(request, body),
            idempotency_key=idempotency_key,
        )
    if action == "append_subtitle_version":
        return await runtime.append_subtitle_version(
            scope,
            context,
            track_id=_text(body, "trackId"),
            version_id=_text(body, "versionId"),
            expected_version=_expected_version(request, body),
            cues=_object(body, "cues"),
            object_key=_optional_text(body, "objectKey"),
            source_task_id=_optional_text(body, "sourceTaskId"),
            idempotency_key=idempotency_key,
        )
    if action == "create_media_task":
        return await runtime.create_media_task(
            scope,
            context,
            task_id=_text(body, "taskId"),
            task_kind=_text(body, "taskKind"),
            resource_type=_text(body, "resourceType"),
            resource_id=_text(body, "resourceId"),
            max_attempts=_integer_value(body.get("maxAttempts"), default=2, minimum=1, maximum=10, code="INVALID_MAX_ATTEMPTS"),
            idempotency_key=idempotency_key,
        )
    if action == "retry_media_task":
        return await runtime.retry_media_task(
            scope,
            context,
            task_id=_text(body, "taskId"),
            expected_version=_expected_version(request, body),
            idempotency_key=idempotency_key,
        )
    if action == "record_media_fallback":
        return await runtime.record_media_fallback(
            scope,
            context,
            task_id=_text(body, "taskId"),
            expected_version=_expected_version(request, body),
            reason=_text(body, "reason"),
            idempotency_key=idempotency_key,
        )
    if action == "cancel_media_task":
        return await runtime.cancel_media_task(
            scope,
            context,
            task_id=_text(body, "taskId"),
            expected_version=_expected_version(request, body),
            reason=_text(body, "reason"),
            idempotency_key=idempotency_key,
        )
    raise AudioHttpError("UNSUPPORTED_AUDIO_ACTION")


async def _json_object(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise AudioHttpError("INVALID_JSON") from error
    if not isinstance(value, Mapping):
        raise AudioHttpError("INVALID_REQUEST_BODY")
    return cast(Mapping[str, object], value)


def _text(body: Mapping[str, object], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AudioHttpError(f"{name.upper()}_REQUIRED")
    return value.strip()


def _optional_text(body: Mapping[str, object], name: str) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AudioHttpError(f"{name.upper()}_INVALID")
    return value.strip()


def _query_text(request: Request, name: str) -> str | None:
    value = request.query_params.get(name)
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise AudioHttpError(f"{name.upper()}_INVALID")
    return normalized


def _enum_query(request: Request, name: str, allowed: set[str]) -> str | None:
    value = _query_text(request, name)
    if value is not None and value not in allowed:
        raise AudioHttpError(f"{name.upper()}_INVALID")
    return value


def _object(body: Mapping[str, object], name: str) -> Mapping[str, Any]:
    value = body.get(name)
    if not isinstance(value, Mapping):
        raise AudioHttpError(f"{name.upper()}_REQUIRED")
    return cast(Mapping[str, Any], value)


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value:
        raise AudioHttpError("IDEMPOTENCY_KEY_REQUIRED")
    return value


def _expected_version(request: Request, body: Mapping[str, object]) -> int:
    header = request.headers.get("If-Match", "").strip().strip('"')
    candidate: object = header if header else body.get("version")
    return _integer_value(candidate, default=None, minimum=1, maximum=2**31 - 1, code="IF_MATCH_REQUIRED")


def _integer(value: str | None, *, default: int, minimum: int, maximum: int, code: str) -> int:
    return _integer_value(value, default=default, minimum=minimum, maximum=maximum, code=code)


def _integer_value(value: object, *, default: int | None, minimum: int, maximum: int, code: str) -> int:
    if value is None or value == "":
        if default is None:
            raise AudioHttpError(code)
        return default
    if isinstance(value, bool):
        raise AudioHttpError(code)
    parsed = int(value) if isinstance(value, int) or (isinstance(value, str) and value.isdigit()) else -1
    if not minimum <= parsed <= maximum:
        raise AudioHttpError(code)
    return parsed


def _success(request_id: str, data: Mapping[str, object], *, total: int | None = None) -> JSONResponse:
    meta: dict[str, object] = {"requestId": request_id}
    if total is not None:
        meta["total"] = total
    return JSONResponse({"data": dict(data), "meta": meta})


def _error(request: Request, code: str, status_code: int) -> JSONResponse:
    request_id = (request.headers.get("X-Request-Id") or "unknown").strip() or "unknown"
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "details": {}}, "meta": {"requestId": request_id}})
