"""Trusted dashboard reads and bounded, audited CSV snapshots."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from lib.i18n import get_translator
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence.persistence import OutboxRow, ProjectRow

from .aggregate import dashboard
from .contracts import EXPORT_BYTES_LIMIT, EXPORT_LIMIT, AnalyticsFailure, AnalyticsQuery, Dataset
from .database import require_same_database
from .models import AnalyticsAuditRow, AnalyticsExportRow
from .source import load, visible_projects

if TYPE_CHECKING:
    from server.xingjing_projects_runtime.runtime import ProjectsRuntime

PRIVATE = {"Cache-Control": "no-store, private", "Referrer-Policy": "no-referrer"}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _scope_digest(
    context: TrustedWorkspaceContext,
    ids: list[str],
    permissions: dict[str, list[str]],
    assignments: dict[str, list[str]],
) -> str:
    return _digest(
        [
            context.tenant_id,
            context.workspace_id,
            context.actor_id,
            sorted(context.permissions),
            sorted(ids),
            permissions,
            assignments,
        ]
    )


def _validate_filters(data: Dataset, query: AnalyticsQuery) -> None:
    # Unknown attribution is a real selectable bucket, not a fallback for an invalid ID.
    if query.modelId and not any(f.model == query.modelId for f in data.facts if f.kind != "project"):
        raise AnalyticsFailure("ANALYTICS_FILTER_INVALID")
    if query.memberId and not any(f.member == query.memberId for f in data.facts if f.kind != "project"):
        raise AnalyticsFailure("ANALYTICS_FILTER_INVALID")
    if query.episodeId and not any(
        query.episodeId in {f.episode, f"{f.project}:{f.episode}"} for f in data.facts if f.kind != "project"
    ):
        raise AnalyticsFailure("ANALYTICS_FILTER_INVALID")


def csv_snapshot(report: dict[str, Any]) -> tuple[str, int]:
    rows: list[dict[str, Any]] = []
    base = {
        "generatedAt": report["generatedAt"],
        "range": report["range"]["key"],
        "start": report["range"]["start"],
        "end": report["range"]["end"],
        "timeZone": "UTC",
        "filters": json.dumps(report["filters"], sort_keys=True, ensure_ascii=False),
        "coverageNotes": ";".join(report["coverage"]["notes"]),
        "comparisonStart": None if report["comparison"] is None else report["comparison"]["start"],
        "comparisonEnd": None if report["comparison"] is None else report["comparison"]["end"],
    }
    for kind, records in [("summary", [report["summary"]]), ("trend", report["trend"]), ("group", report["rows"])]:
        for record in records:
            scalar = {key: value for key, value in record.items() if key != "costs"}
            costs = record.get("costs", [])
            for cost in costs or [{}]:
                rows.append({**base, "recordType": kind, **scalar, **cost})
    for record in report["monthlyProduction"]:
        rows.append({**base, "recordType": "monthly_production", **record})
    if report["comparison"] is not None:
        record = report["comparison"]["summary"]
        scalar = {key: value for key, value in record.items() if key != "costs"}
        for cost in record.get("costs", []) or [{}]:
            rows.append({**base, "recordType": "previous_summary", **scalar, **cost})
    if len(rows) > EXPORT_LIMIT:
        raise AnalyticsFailure("ANALYTICS_EXPORT_LIMIT", 413)
    fields = sorted({key for row in rows for key in row})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    content = "\ufeff" + stream.getvalue()
    if len(content.encode("utf-8")) > EXPORT_BYTES_LIMIT:
        raise AnalyticsFailure("ANALYTICS_EXPORT_LIMIT", 413)
    return content, len(rows)


def register_analytics_routes(runtime: ProjectsRuntime, router: APIRouter) -> None:
    async def context(request: Request, *, export: bool = False):
        if request.headers.get("X-Project-Id"):
            raise AnalyticsFailure("ANALYTICS_PROJECT_HEADER_NOT_ALLOWED", 403)
        current = await runtime._context(request, "analytics.view")
        if current.role == "API_KEY" or "project.view" not in current.permissions:
            raise AnalyticsFailure("PERMISSION_DENIED", 403)
        if export and "analytics.manage" not in current.permissions:
            raise AnalyticsFailure("PERMISSION_DENIED", 403)
        expected = request.headers.get("X-Workspace-Id")
        if expected and expected != current.workspace_id:
            raise AnalyticsFailure("WORKSPACE_SCOPE_MISMATCH", 403)
        return current

    async def report(current: TrustedWorkspaceContext, query: AnalyticsQuery, *, all_rows: bool = False):
        async with runtime._require_sessions()() as session:
            bound = session.bind
            url = bound.engine.url if isinstance(bound, AsyncConnection) else bound.url
            if (
                current.role.casefold() not in {"owner", "admin", "platform_admin"}
                or "workspace.member.view" in current.permissions
            ):
                require_same_database(url, {"XINGJING_TEAM_DATABASE_URL"})
            data = await load(session, current, query.projectId)
            variables: set[str] = set()
            for source, variable in {
                "generation": "XINGJING_GENERATION_DATABASE_URL",
                "audio": "XINGJING_AUDIO_DATABASE_URL",
                "editing": "XINGJING_EDITING_DATABASE_URL",
                "finals": "XINGJING_EDITING_DATABASE_URL",
                "deliveries": "XINGJING_COMPLIANCE_DATABASE_URL",
            }.items():
                if source in data.sources:
                    variables.add(variable)
            if "deliveries" in data.sources:
                variables.add("XINGJING_EDITING_DATABASE_URL")
            if "billing" in data.sources:
                variables.update(
                    {"XINGJING_GENERATION_DATABASE_URL", "XINGJING_AUDIO_DATABASE_URL", "XINGJING_EDITING_DATABASE_URL"}
                )
            require_same_database(url, variables)
        _validate_filters(data, query)
        value = dashboard(data, query, datetime.now(UTC), all_rows=all_rows)
        value.update(
            workspaceId=current.workspace_id,
            actorId=current.actor_id,
            canExport="analytics.manage" in current.permissions,
        )
        return data, value

    def failure(request: Request, error: AnalyticsFailure) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            headers=PRIVATE,
            content={
                "error": {
                    "code": error.code,
                    "message": get_translator(request)("project.request_failed"),
                    "details": {},
                    "retryable": error.status >= 500,
                },
                "meta": {
                    "requestId": getattr(request.state, "project_request_id", None)
                    or request.headers.get("X-Request-Id", "unknown")
                },
            },
        )

    def download(content: str, filename: str) -> Response:
        return Response(
            content.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={**PRIVATE, "Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def replay(current: TrustedWorkspaceContext, key: str, fingerprint: str) -> Response | None:
        async with runtime._require_sessions()() as session:
            bound = session.bind
            if current.role.casefold() not in {"owner", "admin", "platform_admin"}:
                require_same_database(
                    bound.engine.url if isinstance(bound, AsyncConnection) else bound.url,
                    {"XINGJING_TEAM_DATABASE_URL"},
                )
            projects, permissions, assignments = await visible_projects(session, current)
            scope_digest = _scope_digest(current, [p.id for p in projects], permissions, assignments)
            receipt = await session.get(
                AnalyticsExportRow, (current.tenant_id, current.workspace_id, current.actor_id, key)
            )
            if receipt is None:
                return None
            if receipt.fingerprint != fingerprint:
                raise AnalyticsFailure("ANALYTICS_IDEMPOTENCY_CONFLICT", 409)
            if receipt.scope_digest != scope_digest:
                raise AnalyticsFailure("ANALYTICS_EXPORT_SCOPE_CHANGED", 403)
            return download(receipt.content, receipt.filename)

    @router.get("/analytics/dashboards/creator")
    async def get_dashboard(
        request: Request,
        range: str = "month",
        projectId: str = "",
        episodeId: str = "",
        modelId: str = "",
        memberId: str = "",
        q: str = "",
        groupBy: str = "project",
        offset: int = 0,
        limit: int = 20,
    ) -> Response:
        try:
            current = await context(request)
            query = AnalyticsQuery.model_validate(
                {
                    "range": range,
                    "projectId": projectId,
                    "episodeId": episodeId,
                    "modelId": modelId,
                    "memberId": memberId,
                    "q": q,
                    "groupBy": groupBy,
                    "offset": offset,
                    "limit": limit,
                }
            )
            _, value = await report(current, query)
            return JSONResponse({"data": value, "meta": {"requestId": current.request_id}}, headers=PRIVATE)
        except ValidationError:
            return failure(request, AnalyticsFailure("ANALYTICS_QUERY_INVALID"))
        except SQLAlchemyError:
            return failure(request, AnalyticsFailure("ANALYTICS_DEPENDENCY_UNAVAILABLE", 503))
        except AnalyticsFailure as error:
            return failure(request, error)

    @router.post("/analytics/dashboards/creator/export")
    async def export_dashboard(request: Request) -> Response:
        current = None
        key = ""
        fingerprint = ""
        try:
            current = await context(request, export=True)
            body = await request.json()
            if not isinstance(body, dict) or {"offset", "limit"} & body.keys():
                raise AnalyticsFailure("ANALYTICS_QUERY_INVALID")
            query = AnalyticsQuery.model_validate(body)
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 128:
                raise AnalyticsFailure("IDEMPOTENCY_KEY_REQUIRED")
            fingerprint = _digest(query.model_dump(exclude={"offset", "limit"}))
            cached = await replay(current, key, fingerprint)
            if cached is not None:
                return cached
            data, value = await report(current, query, all_rows=True)
            receipt_key = (current.tenant_id, current.workspace_id, current.actor_id, key)
            scope_digest = _scope_digest(current, data.visible_projects, data.scope_permissions, data.assignment_scopes)
            content, row_count = csv_snapshot(cast(dict[str, Any], value))
            filename = f"xingjing-creator-analytics-{query.range}-{datetime.now(UTC):%Y%m%d}.csv"
            async with runtime._require_sessions()() as session, session.begin():
                # The lock serializes receipt insertion on PostgreSQL and SQLite.
                lock_id = next(iter(sorted(data.visible_projects)), None)
                if lock_id is not None:
                    await session.execute(
                        update(ProjectRow)
                        .where(
                            ProjectRow.id == lock_id,
                            ProjectRow.tenant_id == current.tenant_id,
                            ProjectRow.workspace_id == current.workspace_id,
                        )
                        .values(updated_at=ProjectRow.updated_at)
                    )
                projects, permissions, assignments = await visible_projects(session, current)
                if _scope_digest(current, [p.id for p in projects], permissions, assignments) != scope_digest:
                    raise AnalyticsFailure("ANALYTICS_EXPORT_SCOPE_CHANGED", 403)
                receipt = await session.get(AnalyticsExportRow, receipt_key)
                if receipt is not None:
                    if receipt.fingerprint != fingerprint:
                        raise AnalyticsFailure("ANALYTICS_IDEMPOTENCY_CONFLICT", 409)
                    if receipt.scope_digest != scope_digest:
                        raise AnalyticsFailure("ANALYTICS_EXPORT_SCOPE_CHANGED", 403)
                    content, filename = receipt.content, receipt.filename
                else:
                    now = datetime.now(UTC)
                    event_id = str(uuid4())
                    session.add(
                        AnalyticsExportRow(
                            tenant_id=current.tenant_id,
                            workspace_id=current.workspace_id,
                            actor_id=current.actor_id,
                            idempotency_key=key,
                            fingerprint=fingerprint,
                            scope_digest=scope_digest,
                            content=content,
                            filename=filename,
                            row_count=row_count,
                            created_at=now,
                        )
                    )
                    payload = {
                        "filters": query.model_dump(exclude={"offset", "limit"}),
                        "rowCount": row_count,
                        "generatedAt": value["generatedAt"],
                        "scopeDigest": scope_digest,
                        "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    }
                    session.add(
                        AnalyticsAuditRow(
                            id=event_id,
                            tenant_id=current.tenant_id,
                            workspace_id=current.workspace_id,
                            actor_id=current.actor_id,
                            request_id=current.request_id,
                            payload=payload,
                            created_at=now,
                        )
                    )
                    session.add(
                        OutboxRow(
                            id=event_id,
                            tenant_id=current.tenant_id,
                            workspace_id=current.workspace_id,
                            event_type="analytics.report.exported",
                            aggregate_type="analytics_report",
                            aggregate_id=event_id,
                            payload=payload,
                            schema_version=1,
                            created_at=now,
                        )
                    )
                    await session.flush()
            return download(content, filename)
        except ValidationError:
            return failure(request, AnalyticsFailure("ANALYTICS_QUERY_INVALID"))
        except (ValueError, TypeError):
            return failure(request, AnalyticsFailure("ANALYTICS_QUERY_INVALID"))
        except IntegrityError:
            if current is not None and key and fingerprint:
                try:
                    cached = await replay(current, key, fingerprint)
                    if cached is not None:
                        return cached
                except AnalyticsFailure as error:
                    return failure(request, error)
                except SQLAlchemyError:
                    return failure(request, AnalyticsFailure("ANALYTICS_DEPENDENCY_UNAVAILABLE", 503))
            return failure(request, AnalyticsFailure("ANALYTICS_IDEMPOTENCY_CONFLICT", 409))
        except SQLAlchemyError:
            return failure(request, AnalyticsFailure("ANALYTICS_DEPENDENCY_UNAVAILABLE", 503))
        except AnalyticsFailure as error:
            return failure(request, error)
