"""M06 HTTP contract with trusted workspace and project scope enforcement."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response

from server.xingjing_generation import GenerationRequest
from server.xingjing_generation_persistence import GenerationBillingError, TaskScopeNotFound, VersionConflict
from server.xingjing_generation_runtime.runtime import GenerationRuntime, GenerationRuntimeUnavailable


class GenerationHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})
        super().__init__(code)


class GenerationContractRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except GenerationHttpError as error:
                return _error(request, error.code, error.status_code, error.details)
            except GenerationRuntimeUnavailable as error:
                return _error(request, error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
            except TaskScopeNotFound:
                return _error(request, "GENERATION_TASK_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except VersionConflict:
                return _error(request, "GENERATION_TASK_VERSION_CONFLICT", status.HTTP_409_CONFLICT)
            except GenerationBillingError as error:
                code = str(error) or "GENERATION_BILLING_REJECTED"
                http_status = status.HTTP_402_PAYMENT_REQUIRED if code == "GENERATION_INSUFFICIENT_CREDITS" else status.HTTP_409_CONFLICT
                return _error(request, code, http_status)
            except PermissionError as error:
                return _error(request, "PERMISSION_DENIED", status.HTTP_403_FORBIDDEN, {"permission": str(error)})
            except LookupError:
                return _error(request, "PROJECT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except HTTPException as error:
                code = "REQUEST_REJECTED"
                if isinstance(error.detail, Mapping):
                    raw_code = error.detail.get("code")
                    if isinstance(raw_code, str):
                        code = raw_code
                return _error(request, code, error.status_code)
            except SQLAlchemyError:
                return _error(request, "GENERATION_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
            except ValueError as error:
                code = str(error) or "INVALID_GENERATION_REQUEST"
                http_status = status.HTTP_409_CONFLICT if code in {
                    "GENERATION_TASK_VERSION_CONFLICT", "GENERATION_TASK_NOT_CANCELLABLE"
                } else status.HTTP_400_BAD_REQUEST
                return _error(request, code, http_status)

        return handler


def create_generation_router(runtime: GenerationRuntime) -> APIRouter:
    router = APIRouter(route_class=GenerationContractRoute)

    @router.get("/generation-tasks")
    async def list_generation_tasks_by_query(request: Request):
        return await _list_generation_tasks(runtime, request, _project_query(request))

    @router.get("/generation-models")
    async def generation_models(request: Request):
        project_id = _project_query(request)
        context = await runtime.context_for(request, project_id, "generation.view")
        return _success(context.request_id, {"model": asdict(runtime.model_availability())})

    @router.get("/generation-model-usage")
    async def generation_model_usage(request: Request):
        project_id = _project_query(request)
        context = await runtime.context_for(request, project_id, "generation.view")
        usage = await runtime.model_usage_report(context, project_id)
        return _success(context.request_id, {"usage": [asdict(item) for item in usage]})

    @router.get("/generation-candidates")
    async def generation_candidates(request: Request):
        project_id = _project_query(request)
        context = await runtime.context_for(request, project_id, "generation.view")
        media_type = _optional_choice(request.query_params.get("mediaType"), {"image", "video"}, "INVALID_MEDIA_TYPE")
        capability = _optional_identifier(request.query_params.get("capability"), "INVALID_CAPABILITY")
        offset = _integer(request.query_params.get("offset"), default=0, minimum=0, maximum=2**31 - 1, code="INVALID_OFFSET")
        limit = _integer(request.query_params.get("limit"), default=20, minimum=1, maximum=100, code="INVALID_LIMIT")
        candidates, total = await runtime.generated_candidate_sets(
            context, project_id, media_type=media_type, capability=capability, offset=offset, limit=limit
        )
        return _success(
            context.request_id,
            {
                "candidates": [
                    {
                        "task": item.task.model_dump(mode="json"),
                        "assets": [asdict(asset) for asset in item.assets],
                        "selectedCandidate": asdict(item.selected_candidate) if item.selected_candidate else None,
                    }
                    for item in candidates
                ]
            },
            total=total,
        )

    @router.get("/generation-assets/{asset_id}/content")
    async def generated_asset_content(request: Request, asset_id: str):
        project_id = _project_query(request)
        context = await runtime.context_for(request, project_id, "generation.view")
        asset, path = await runtime.generated_artifact_file(context, project_id, asset_id)
        media_type = asset.metadata.get("mime_type") if isinstance(asset.metadata.get("mime_type"), str) else None
        return FileResponse(path, media_type=media_type, filename=f"{asset.asset_id}{_suffix_for_media(asset.media_type)}")

    @router.get("/projects/{project_id}/generation-tasks")
    async def list_generation_tasks(request: Request, project_id: str):
        return await _list_generation_tasks(runtime, request, project_id)

    @router.get("/generation-tasks/{task_id}")
    async def get_generation_task_by_query(request: Request, task_id: str):
        return await _get_generation_task(runtime, request, _project_query(request), task_id)

    @router.get("/projects/{project_id}/generation-tasks/{task_id}")
    async def get_generation_task(request: Request, project_id: str, task_id: str):
        return await _get_generation_task(runtime, request, project_id, task_id)

    @router.post("/generation-tasks")
    async def submit_generation_task_by_query(request: Request):
        return await _submit_generation_task(runtime, request, _project_query(request))

    @router.post("/projects/{project_id}/generation-tasks")
    async def submit_generation_task(request: Request, project_id: str):
        return await _submit_generation_task(runtime, request, project_id)

    @router.post("/generation-tasks/{task_id}/actions")
    async def generation_task_actions_by_query(request: Request, task_id: str):
        return await _generation_task_actions(runtime, request, _project_query(request), task_id)

    @router.post("/projects/{project_id}/generation-tasks/{task_id}/actions")
    async def generation_task_actions(request: Request, project_id: str, task_id: str):
        return await _generation_task_actions(runtime, request, project_id, task_id)

    @router.post("/generation-tasks/{task_id}/candidates/{asset_id}/selection")
    async def select_generation_candidate_by_query(request: Request, task_id: str, asset_id: str):
        return await _select_generation_candidate(runtime, request, _project_query(request), task_id, asset_id)

    @router.post("/generation-tasks/{task_id}/retry")
    async def retry_generation_task_by_query(request: Request, task_id: str):
        return await _retry_generation_task(runtime, request, _project_query(request), task_id)

    @router.post("/projects/{project_id}/generation-tasks/{task_id}/candidates/{asset_id}/selection")
    async def select_generation_candidate(request: Request, project_id: str, task_id: str, asset_id: str):
        return await _select_generation_candidate(runtime, request, project_id, task_id, asset_id)

    @router.post("/projects/{project_id}/generation-tasks/{task_id}/retry")
    async def retry_generation_task(request: Request, project_id: str, task_id: str):
        return await _retry_generation_task(runtime, request, project_id, task_id)

    @router.post("/generation-providers/callbacks")
    async def generation_provider_callback(request: Request):
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise GenerationHttpError("INVALID_JSON") from error
        if not isinstance(payload, dict):
            raise GenerationHttpError("INVALID_REQUEST_BODY")
        task = await runtime.apply_provider_callback(
            raw_body=raw_body,
            signature=request.headers.get("X-Xingjing-Provider-Signature", ""),
            payload=cast(dict[str, object], payload),
        )
        return _success(
            request.headers.get("X-Request-Id", "provider-callback"),
            {"task": task.model_dump(mode="json")},
        )

    @router.post("/generation-billing/grants")
    async def generation_billing_grant(request: Request):
        result = await runtime.apply_billing_grant(
            raw_body=await request.body(),
            signature=request.headers.get("X-Xingjing-Billing-Signature", ""),
        )
        return _success(request.headers.get("X-Request-Id", "billing-grant"), {"account": result})

    @router.post("/generation-providers/recover-submissions")
    async def recover_generation_provider_submissions(request: Request):
        limit = _integer(
            request.query_params.get("limit"), default=100, minimum=1,
            maximum=1_000, code="INVALID_LIMIT",
        )
        result = await runtime.recover_provider_submissions(
            secret=request.headers.get("X-Xingjing-Recovery-Secret", ""), limit=limit,
        )
        return _success(request.headers.get("X-Request-Id", "provider-recovery"), result)

    return router


async def _list_generation_tasks(runtime: GenerationRuntime, request: Request, project_id: str) -> JSONResponse:
    context = await runtime.context_for(request, project_id, "generation.view")
    offset = _integer(request.query_params.get("offset"), default=0, minimum=0, maximum=2**31 - 1, code="INVALID_OFFSET")
    limit = _integer(request.query_params.get("limit"), default=50, minimum=1, maximum=200, code="INVALID_LIMIT")
    task_status = _optional_choice(request.query_params.get("status"), {
        "queued", "running", "retrying", "cancelling", "succeeded", "failed", "timeout", "cancelled"
    }, "INVALID_GENERATION_TASK_STATUS")
    media_type = _optional_choice(request.query_params.get("mediaType"), {"image", "video"}, "INVALID_MEDIA_TYPE")
    capability = _optional_identifier(request.query_params.get("capability"), "INVALID_CAPABILITY")
    tasks, total = await runtime.list_tasks(
        context,
        project_id,
        offset=offset,
        limit=limit,
        status=task_status,
        media_type=media_type,
        capability=capability,
    )
    return _success(context.request_id, {"tasks": [task.model_dump(mode="json") for task in tasks]}, total=total)

async def _get_generation_task(
    runtime: GenerationRuntime, request: Request, project_id: str, task_id: str
) -> JSONResponse:
    context = await runtime.context_for(request, project_id, "generation.view")
    task = await runtime.get_task(context, project_id, task_id)
    assets = await runtime.generated_assets(context, project_id, task_id)
    selected_candidate = await runtime.generated_candidate_selection(context, project_id, task_id)
    costs = await runtime.cost_evidence(context, project_id, task_id)
    billing = await runtime.billing_for_task(context, project_id, task_id)
    progress = await runtime.task_progress(context, project_id, task_id)
    return _success(
        context.request_id,
        {
            "task": task.model_dump(mode="json"),
            "generatedAssets": [asdict(asset) for asset in assets],
            "selectedCandidate": asdict(selected_candidate) if selected_candidate else None,
            "costEvidence": [asdict(cost) for cost in costs],
            "billing": billing,
            "progress": progress,
        },
    )

async def _submit_generation_task(runtime: GenerationRuntime, request: Request, project_id: str) -> JSONResponse:
    context = await runtime.context_for(request, project_id, "generation.manage")
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not idempotency_key:
        raise GenerationHttpError("IDEMPOTENCY_KEY_REQUIRED")
    body = await _json_object(request)
    if "workspace_id" in body or "project_id" in body:
        raise GenerationHttpError("GENERATION_REQUEST_SCOPE_SERVER_ASSIGNED")
    try:
        generation_request = GenerationRequest.model_validate({
            **body, "workspace_id": context.workspace_id, "project_id": project_id,
        })
    except ValueError as error:
        raise GenerationHttpError("INVALID_GENERATION_REQUEST", status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    task = await runtime.submit(context, project_id, generation_request, idempotency_key=idempotency_key)
    return _success(context.request_id, {"task": task.model_dump(mode="json")})

async def _generation_task_actions(
    runtime: GenerationRuntime, request: Request, project_id: str, task_id: str
) -> JSONResponse:
    context = await runtime.context_for(request, project_id, "generation.manage")
    body = await _json_object(request)
    if body.get("action") != "cancel":
        raise GenerationHttpError("UNSUPPORTED_GENERATION_ACTION")
    task = await runtime.cancel_task(
        context,
        project_id,
        task_id,
        expected_version=_if_match(request),
    )
    return _success(context.request_id, {"task": task.model_dump(mode="json")})


async def _select_generation_candidate(
    runtime: GenerationRuntime, request: Request, project_id: str, task_id: str, asset_id: str
) -> JSONResponse:
    context = await runtime.context_for(request, project_id, "generation.manage")
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not idempotency_key:
        raise GenerationHttpError("IDEMPOTENCY_KEY_REQUIRED")
    selected = await runtime.select_generated_candidate(
        context,
        project_id,
        task_id,
        asset_id,
        expected_version=_selection_if_match(request),
        idempotency_key=idempotency_key,
    )
    return _success(context.request_id, {"selectedCandidate": asdict(selected)})


async def _retry_generation_task(
    runtime: GenerationRuntime, request: Request, project_id: str, task_id: str
) -> JSONResponse:
    context = await runtime.context_for(request, project_id, "generation.manage")
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not idempotency_key:
        raise GenerationHttpError("IDEMPOTENCY_KEY_REQUIRED")
    task = await runtime.retry_task(context, project_id, task_id, idempotency_key=idempotency_key)
    return _success(context.request_id, {"task": task.model_dump(mode="json")})


async def _json_object(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise GenerationHttpError("INVALID_JSON") from error
    if not isinstance(value, Mapping):
        raise GenerationHttpError("INVALID_REQUEST_BODY")
    return cast(Mapping[str, object], value)


def _if_match(request: Request) -> int:
    raw = request.headers.get("If-Match", "").strip().strip('"')
    if not raw.isdigit() or int(raw) < 1:
        raise GenerationHttpError("IF_MATCH_REQUIRED")
    return int(raw)


def _selection_if_match(request: Request) -> int:
    raw = request.headers.get("If-Match", "").strip().strip('"')
    if not raw.isdigit() or int(raw) < 0:
        raise GenerationHttpError("IF_MATCH_REQUIRED")
    return int(raw)


def _project_query(request: Request) -> str:
    value = request.query_params.get("projectId", "").strip()
    if not value:
        raise GenerationHttpError("PROJECT_ID_REQUIRED")
    return value


def _integer(value: str | None, *, default: int, minimum: int, maximum: int, code: str) -> int:
    if value is None:
        return default
    if not value.isdigit() or not minimum <= int(value) <= maximum:
        raise GenerationHttpError(code)
    return int(value)


def _optional_choice(value: str | None, allowed: set[str], code: str) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if normalized not in allowed:
        raise GenerationHttpError(code)
    return normalized


def _optional_identifier(value: str | None, code: str) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if not normalized.replace("_", "").isalnum() or not normalized[0].islower() or len(normalized) > 64:
        raise GenerationHttpError(code)
    return normalized


def _suffix_for_media(media_type: str) -> str:
    return ".mp4" if media_type == "video" else ".png"


def _success(request_id: str, data: Mapping[str, object], *, total: int | None = None) -> JSONResponse:
    meta: dict[str, object] = {"requestId": request_id}
    if total is not None:
        meta["total"] = total
    return JSONResponse({"data": dict(data), "meta": meta})


def _error(request: Request, code: str, status_code: int, details: Mapping[str, object] | None = None) -> JSONResponse:
    request_id = (request.headers.get("X-Request-Id") or "unknown").strip() or "unknown"
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "details": dict(details or {})}, "meta": {"requestId": request_id}},
    )
