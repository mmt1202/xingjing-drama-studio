"""M09 HTTP contract for authoritative compliance evidence and export preflight.

The public export endpoint never treats a gate decision as a produced file.
Only an explicit preflight route is available until a real media/export
executor, object storage and completion callback have been composed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from server.xingjing_compliance.models import ExportGateDecision, FormalExportRequest
from server.xingjing_compliance_persistence import (
    ComplianceIdempotencyConflict,
    ComplianceVersionConflict,
    DeliveryIdempotencyConflict,
)
from server.xingjing_compliance_runtime import (
    AuthorityDataMissing,
    CompliancePermissionDenied,
    ComplianceProjectScopeDenied,
    ComplianceRuntimeUnavailable,
    ProductionComplianceRuntime,
    RuntimeConfigurationError,
)
from server.xingjing_compliance_runtime.formal_export import FormalExportArtifactError
from server.xingjing_compliance_runtime.provider import ComplianceProviderError

_DOWNLOAD_TYPES = {
    "mp4": ("video/mp4", "mp4"),
    "subtitle_srt": ("application/x-subrip", "srt"),
    "storyboard_csv": ("text/csv; charset=utf-8", "csv"),
    "davinci_edl": ("text/plain; charset=utf-8", "edl"),
    "premiere_xml": ("application/xml", "xml"),
    "compliance_report": ("application/json", "json"),
    "cost_report": ("application/json", "json"),
    "material_package": ("application/zip", "zip"),
    "project_archive": ("application/zip", "zip"),
    "jianying_draft": ("application/zip", "zip"),
    "publish_package": ("application/zip", "zip"),
}


class ComplianceHttpError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.details = dict(details or {})
        super().__init__(code)


class ComplianceContractRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> JSONResponse:
            try:
                return cast(JSONResponse, await original(request))
            except ComplianceHttpError as error:
                return _error(request, error.code, error.status_code, error.details)
            except CompliancePermissionDenied:
                return _error(request, "PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)
            except ComplianceProjectScopeDenied:
                # Do not disclose whether an out-of-scope project exists.
                return _error(request, "PROJECT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            except AuthorityDataMissing:
                return _error(request, "COMPLIANCE_AUTHORITY_DATA_MISSING", status.HTTP_409_CONFLICT)
            except ComplianceVersionConflict as error:
                return _error(
                    request,
                    "VERSION_CONFLICT",
                    status.HTTP_409_CONFLICT,
                    {"currentVersion": error.current_version},
                )
            except ComplianceIdempotencyConflict:
                return _error(request, "IDEMPOTENCY_CONFLICT", status.HTTP_409_CONFLICT)
            except DeliveryIdempotencyConflict:
                return _error(request, "EXPORT_IDEMPOTENCY_CONFLICT", status.HTTP_409_CONFLICT)
            except FormalExportArtifactError as error:
                status_code = status.HTTP_404_NOT_FOUND if error.code.endswith("NOT_FOUND") else status.HTTP_409_CONFLICT
                return _error(request, error.code, status_code)
            except ComplianceProviderError as error:
                return _error(request, error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
            except (ComplianceRuntimeUnavailable, RuntimeConfigurationError, SQLAlchemyError):
                return _error(request, "COMPLIANCE_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, Mapping) else {}
                raw_code = detail.get("code")
                code = raw_code if isinstance(raw_code, str) else "REQUEST_REJECTED"
                return _error(request, code, error.status_code)
            except ValueError as error:
                return _error(request, str(error) or "INVALID_COMPLIANCE_REQUEST", status.HTTP_400_BAD_REQUEST)

        return handler


def create_production_compliance_router(runtime: ProductionComplianceRuntime) -> APIRouter:
    """Expose only evidence, delivery history and a clearly non-delivery preflight."""

    router = APIRouter(prefix="/api/v1", tags=["xingjing-compliance"], route_class=ComplianceContractRoute)

    @router.get("/projects/{project_id}/compliance")
    async def load_compliance(request: Request, project_id: str) -> JSONResponse:
        formal_request = _formal_request(request, project_id)
        evidence = await runtime.load_export_evidence(request, formal_request)
        return _success(
            request,
            {
                "projectId": formal_request.project_id,
                "projectVersion": formal_request.project_version,
                "target": formal_request.target,
                "state": _state_payload(evidence.state),
                "policy": {"id": evidence.policy_id, "version": evidence.policy_version},
                "authorizationIds": list(evidence.authorization_ids),
                "reviewedAt": evidence.reviewed_at.isoformat(),
                "reviewVersion": evidence.review_version,
            },
        )

    @router.get("/projects/{project_id}/exports")
    async def list_exports(request: Request, project_id: str) -> JSONResponse:
        deliveries = await runtime.list_delivery_records(request, project_id=project_id)
        return _success(request, {"deliveries": jsonable_encoder([asdict(item) for item in deliveries])})

    @router.get("/projects/{project_id}/export-contexts")
    async def list_export_contexts(request: Request, project_id: str) -> JSONResponse:
        contexts = await runtime.list_export_contexts(request, project_id=project_id)
        return _success(request, {"contexts": list(contexts)})

    @router.post("/projects/{project_id}/exports/preflight")
    async def preflight_export(request: Request, project_id: str) -> JSONResponse:
        decision = await runtime.authorize_export(request, _formal_request(request, project_id))
        return _success(request, {"preflight": _decision_payload(decision)})

    @router.post("/projects/{project_id}/compliance/actions")
    async def apply_compliance_action(request: Request, project_id: str) -> JSONResponse:
        body = await _json_body(request)
        project_version = _required_text(body, "projectVersion")
        action = _required_text(body, "action")
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise ComplianceHttpError("IDEMPOTENCY_KEY_REQUIRED")
        if action == "run_check":
            result = await runtime.run_compliance_check(
                request,
                project_id=project_id,
                project_version=project_version,
                target=_required_text(body, "target"),
                idempotency_key=idempotency_key,
            )
            return _success(request, {"assessment": result})
        if action == "record_authorization":
            result = await runtime.record_authorization(
                request,
                project_id=project_id,
                project_version=project_version,
                authorization_id=_required_text(body, "authorizationId"),
                authorization_type=_required_text(body, "authorizationType"),
                subject_id=_required_text(body, "subjectId"),
                evidence_object_key=_required_text(body, "evidenceObjectKey"),
                evidence_sha256=_required_text(body, "evidenceSha256"),
                valid_from=_required_datetime(body, "validFrom"),
                valid_until=_optional_datetime(body, "validUntil"),
                idempotency_key=idempotency_key,
            )
            return _success(request, {"authorization": result})
        reason = _required_text(body, "reason")
        expected_version = body.get("expectedVersion")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
            raise ComplianceHttpError("EXPECTED_VERSION_INVALID")
        result = await runtime.transition_review(
            request,
            project_id=project_id,
            project_version=project_version,
            action=action,
            reason=reason,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
        return _success(request, {"review": result})

    @router.post("/projects/{project_id}/exports")
    async def submit_export(request: Request, project_id: str) -> JSONResponse:
        body = await _json_body(request)
        project_version = _required_text(body, "projectVersion")
        target = _required_text(body, "target")
        output_format = _required_text(body, "format")
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise ComplianceHttpError("IDEMPOTENCY_KEY_REQUIRED")
        delivery = await runtime.submit_export(
            request,
            formal_request=FormalExportRequest(
                project_id=project_id,
                project_version=project_version,
                target=target,
            ),
            output_format=output_format,
            idempotency_key=idempotency_key,
        )
        return _success(request, {"delivery": cast(dict[str, object], jsonable_encoder(asdict(delivery)))})

    @router.get("/projects/{project_id}/exports/{request_id}/download", response_class=FileResponse)
    async def download_export(request: Request, project_id: str, request_id: str) -> FileResponse:
        path, output_format = await runtime.delivery_file(
            request,
            project_id=project_id,
            request_id=request_id,
        )
        media_type, extension = _DOWNLOAD_TYPES.get(output_format, ("application/octet-stream", "bin"))
        return FileResponse(
            path,
            media_type=media_type,
            filename=f"xingjing-{project_id}-{request_id}.{extension}",
        )

    @router.post("/internal/compliance/billing-settlements")
    async def record_billing_settlement(request: Request) -> JSONResponse:
        raw_body = await request.body()
        result = await runtime.record_billing_callback(
            raw_body=raw_body,
            signature=request.headers.get("X-Xingjing-Billing-Signature", ""),
        )
        return _success(request, {"settlement": result})

    return router


def create_unavailable_compliance_router(code: str = "COMPLIANCE_RUNTIME_NOT_CONFIGURED") -> APIRouter:
    """Expose required M09 routes without accepting an unguarded export."""

    router = APIRouter(prefix="/api/v1", tags=["xingjing-compliance"])

    async def unavailable(request: Request) -> JSONResponse:
        return _error(request, code, status.HTTP_503_SERVICE_UNAVAILABLE)

    routes: tuple[tuple[str, tuple[str, ...], str], ...] = (
        ("/projects/{project_id}/compliance", ("GET",), "m09_compliance_read_unavailable"),
        ("/projects/{project_id}/compliance/actions", ("POST",), "m09_compliance_action_unavailable"),
        ("/projects/{project_id}/exports", ("GET",), "m09_exports_read_unavailable"),
        ("/projects/{project_id}/exports", ("POST",), "m09_exports_submit_unavailable"),
        ("/projects/{project_id}/export-contexts", ("GET",), "m09_export_contexts_unavailable"),
        ("/projects/{project_id}/exports/{request_id}/download", ("GET",), "m09_export_download_unavailable"),
        ("/projects/{project_id}/exports/preflight", ("POST",), "m09_export_preflight_unavailable"),
        (
            "/internal/compliance/billing-settlements",
            ("POST",),
            "m09_billing_settlement_callback_unavailable",
        ),
    )
    for path, methods, operation_id in routes:
        router.add_api_route(path, unavailable, methods=list(methods), operation_id=operation_id)
    return router


def _formal_request(request: Request, project_id: str) -> FormalExportRequest:
    project_version = request.query_params.get("projectVersion", "").strip()
    target = request.query_params.get("target", "").strip()
    if not project_version:
        raise ComplianceHttpError("PROJECT_VERSION_REQUIRED")
    if not target:
        raise ComplianceHttpError("EXPORT_TARGET_REQUIRED")
    return FormalExportRequest(project_id=project_id, project_version=project_version, target=target)


async def _json_body(request: Request) -> dict[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise ComplianceHttpError("INVALID_JSON") from error
    if not isinstance(value, dict):
        raise ComplianceHttpError("JSON_OBJECT_REQUIRED")
    return cast(dict[str, object], value)


def _required_text(body: Mapping[str, object], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ComplianceHttpError(f"{field.upper()}_REQUIRED")
    return value.strip()


def _required_datetime(body: Mapping[str, object], field: str) -> datetime:
    value = _required_text(body, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ComplianceHttpError(f"{field.upper()}_INVALID") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ComplianceHttpError(f"{field.upper()}_TIMEZONE_REQUIRED")
    return parsed


def _optional_datetime(body: Mapping[str, object], field: str) -> datetime | None:
    value = body.get(field)
    if value is None or value == "":
        return None
    return _required_datetime(body, field)


def _state_payload(value: object) -> dict[str, object]:
    raw = asdict(cast(Any, value)) if hasattr(value, "__dataclass_fields__") else {}
    return cast(dict[str, object], jsonable_encoder(raw))


def _decision_payload(decision: ExportGateDecision) -> dict[str, object]:
    return {
        "allowed": decision.allowed,
        "blockCodes": [item.value for item in decision.block_codes],
        "manifest": None if decision.manifest_context is None else _state_payload(decision.manifest_context),
        "isDelivery": False,
    }


def _success(request: Request, data: Mapping[str, object]) -> JSONResponse:
    return JSONResponse({"data": dict(data), "meta": {"requestId": _request_id(request)}})


def _error(request: Request, code: str, status_code: int, details: Mapping[str, object] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "details": dict(details or {})}, "meta": {"requestId": _request_id(request)}},
    )


def _request_id(request: Request) -> str:
    return (request.headers.get("X-Request-Id") or "unknown").strip() or "unknown"
