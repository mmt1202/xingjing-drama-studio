"""M09/S06 的 PostgreSQL 生产运行时组合。

此模块只做可信会话、项目归属和权威存储的组合。它不创建 schema，不
执行媒体导出或发布任务，也不为任何权威事实提供 SQLite、内存或默认回退。
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_assets_runtime.runtime import LocalAssetEvidenceStorage
from server.xingjing_audio_persistence.models import audio_billing_holds
from server.xingjing_compliance.models import (
    AuditContext,
    ComplianceProvider,
    ExportGateDecision,
    FormalExportRequest,
    ProviderReviewRequest,
)
from server.xingjing_compliance_persistence import (
    ComplianceAuthorityScope,
    DeliveryIdempotencyConflict,
    SqlAlchemyComplianceAuthorityStore,
)
from server.xingjing_editing_persistence.models import EditingRenderBillingHoldRow, FinalVideoVersionRow
from server.xingjing_generation_persistence.repository import GenerationBillingHoldRow
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import ProjectRow

from .contracts import DeliveryRecord, ExportAuthorityEvidence
from .errors import (
    CompliancePermissionDenied,
    ComplianceProjectScopeDenied,
    ComplianceRuntimeUnavailable,
    RuntimeConfigurationError,
)
from .formal_export import FormalExportArtifactError, LocalFormalExportExecutor
from .provider import ComplianceProviderError, HttpComplianceProvider
from .runtime import ComplianceRuntime

TrustedContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]
SessionFactory = async_sessionmaker[AsyncSession]

_M09_PERMISSIONS = frozenset({"compliance.view", "compliance.manage", "export.view", "export.manage"})


def _settlement_cost_summary(
    sources: tuple[tuple[str, list[tuple[str, int, int, int, str, datetime]]], ...],
) -> tuple[list[dict[str, object]], datetime | None]:
    """Validate terminal holds and aggregate authoritative cost by source/currency."""
    totals: dict[tuple[str, str], dict[str, int]] = {}
    latest: datetime | None = None
    for source, rows in sources:
        for currency, estimated, actual, released, status, updated_at in rows:
            if status == "active" or actual + released != estimated:
                raise ValueError("PROJECT_BILLING_NOT_SETTLED")
            bucket = totals.setdefault(
                (source, currency),
                {"estimatedMinor": 0, "actualMinor": 0, "releasedMinor": 0, "taskCount": 0},
            )
            bucket["estimatedMinor"] += estimated
            bucket["actualMinor"] += actual
            bucket["releasedMinor"] += released
            bucket["taskCount"] += 1
            latest = updated_at if latest is None or updated_at > latest else latest
    return (
        [
            {"source": source, "currency": currency, **totals[(source, currency)]}
            for source, currency in sorted(totals)
        ],
        latest,
    )


@dataclass(frozen=True, slots=True)
class ComplianceRequestContext:
    """已经完成会话、权限和项目范围校验的一次请求上下文。"""

    trusted: TrustedWorkspaceContext
    project_id: str

    @property
    def audit(self) -> AuditContext:
        return AuditContext(
            actor_id=self.trusted.actor_id,
            tenant_id=self.trusted.tenant_id,
            request_id=self.trusted.request_id,
            source="xingjing_compliance_http",
            occurred_at=datetime.now(UTC),
        )

    @property
    def authority_scope(self) -> ComplianceAuthorityScope:
        return ComplianceAuthorityScope(self.trusted.tenant_id, self.trusted.workspace_id)


class ProductionComplianceRuntime:
    """将 M09 门禁组合到唯一 PostgreSQL 权威来源。

    ``resolve_context`` 必须先于任一资源读取调用；每个公开动作都会自行
    重做该检查，避免 HTTP 层遗漏时出现跨工作区访问。
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        context_resolver: TrustedContextResolver,
        engine: AsyncEngine,
        export_executor: LocalFormalExportExecutor | None = None,
        evidence_storage: LocalAssetEvidenceStorage | None = None,
        compliance_provider: ComplianceProvider | None = None,
        commercial_export_enabled: bool = False,
        billing_callback_secret: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._context_resolver = context_resolver
        self._engine: AsyncEngine | None = engine
        self._export_executor = export_executor
        self._evidence_storage = evidence_storage
        self._compliance_provider = compliance_provider
        self._commercial_export_enabled = commercial_export_enabled
        self._billing_callback_secret = billing_callback_secret

    async def record_billing_callback(self, *, raw_body: bytes, signature: str) -> dict[str, object]:
        secret = self._billing_callback_secret
        if secret is None:
            raise ComplianceRuntimeUnavailable("BILLING_CALLBACK_NOT_CONFIGURED")
        normalized = signature.removeprefix("sha256=").strip().lower()
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not normalized or not hmac.compare_digest(normalized, expected):
            raise CompliancePermissionDenied("BILLING_CALLBACK_SIGNATURE_INVALID")
        try:
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("BILLING_CALLBACK_JSON_INVALID") from error
        if not isinstance(body, dict):
            raise ValueError("BILLING_CALLBACK_JSON_INVALID")
        tenant_id = _required_callback_text(body, "tenantId")
        workspace_id = _required_callback_text(body, "workspaceId")
        project_id = _required_callback_text(body, "projectId")
        project_version = _required_callback_text(body, "projectVersion")
        settlement_reference = _required_callback_text(body, "settlementReference")
        event_id = _required_callback_text(body, "eventId")
        cost_summary = _callback_cost_summary(body.get("costSummary"))
        settled = body.get("settled")
        if settled is not True:
            raise ValueError("BILLING_CALLBACK_NOT_SETTLED")
        raw_settled_at = _required_callback_text(body, "settledAt")
        try:
            settled_at = datetime.fromisoformat(raw_settled_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("BILLING_CALLBACK_SETTLED_AT_INVALID") from error
        if settled_at.tzinfo is None or settled_at.utcoffset() is None:
            raise ValueError("BILLING_CALLBACK_SETTLED_AT_INVALID")
        async with self._session_factory() as session:
            project_exists = await session.scalar(
                select(ProjectRow.id).where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == tenant_id,
                    ProjectRow.workspace_id == workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
            )
        if project_exists is None:
            raise ComplianceProjectScopeDenied("PROJECT_NOT_FOUND")
        store = SqlAlchemyComplianceAuthorityStore(
            self._session_factory,
            scope=ComplianceAuthorityScope(tenant_id=tenant_id, workspace_id=workspace_id),
        )
        return await store.record_billing_settlement_callback(
            project_id=project_id,
            project_version=project_version,
            settlement_reference=settlement_reference,
            settled_at=settled_at,
            event_id=event_id,
            cost_summary=cost_summary,
            recorded_at=datetime.now(UTC),
        )

    async def resolve_context(
        self,
        request: Request,
        *,
        project_id: str,
        required_permission: str,
    ) -> ComplianceRequestContext:
        """从 Java 可信会话解析上下文，并服务端校验项目工作区归属。"""
        if required_permission not in _M09_PERMISSIONS:
            raise ValueError("unsupported M09 permission")
        if not project_id.strip():
            raise ComplianceProjectScopeDenied("PROJECT_SCOPE_REQUIRED")
        trusted = self._context_resolver(request)
        if inspect.isawaitable(trusted):
            trusted = await trusted
        if required_permission not in trusted.permissions:
            raise CompliancePermissionDenied(f"M09_PERMISSION_DENIED:{required_permission}")
        context = ComplianceRequestContext(trusted=trusted, project_id=project_id)
        await self._assert_project_scope(context)
        return context

    async def load_export_evidence(
        self,
        request: Request,
        formal_request: FormalExportRequest,
    ) -> ExportAuthorityEvidence:
        """读取已确认项目范围内的正式导出权威证据，不产生交付记录。"""
        context = await self.resolve_context(
            request,
            project_id=formal_request.project_id,
            required_permission="compliance.view",
        )
        return await self._store(context).load_compliance_status(formal_request, context.audit)

    async def list_delivery_records(self, request: Request, *, project_id: str) -> tuple[DeliveryRecord, ...]:
        """列出权威交付记录；下载或重新签发由后续交付执行器负责。"""
        context = await self.resolve_context(request, project_id=project_id, required_permission="export.view")
        return await self._store(context).list_deliveries(tenant_id=context.trusted.tenant_id, project_id=project_id)

    async def list_export_contexts(self, request: Request, *, project_id: str) -> tuple[dict[str, object], ...]:
        context = await self.resolve_context(request, project_id=project_id, required_permission="export.view")
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(FinalVideoVersionRow)
                    .where(
                        FinalVideoVersionRow.tenant_id == context.trusted.tenant_id,
                        FinalVideoVersionRow.workspace_id == context.trusted.workspace_id,
                        FinalVideoVersionRow.project_id == project_id,
                    )
                    .order_by(FinalVideoVersionRow.created_at.desc(), FinalVideoVersionRow.version_id.desc())
                )
            ).all()
        return tuple(
            {
                "projectVersion": row.version_id,
                "timelineId": row.timeline_id,
                "timelineVersionId": row.timeline_version_id,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        )

    async def authorize_export(self, request: Request, formal_request: FormalExportRequest) -> ExportGateDecision:
        """只执行并记录正式导出门禁判定，绝不伪装为导出或发布任务。"""
        context = await self.resolve_context(
            request,
            project_id=formal_request.project_id,
            required_permission="export.manage",
        )
        await self._synchronize_billing_settlement(context, formal_request.project_version)
        return await ComplianceRuntime(authority_store=self._store(context)).authorize_export(formal_request, context.audit)

    async def _synchronize_billing_settlement(
        self,
        context: ComplianceRequestContext,
        project_version: str,
    ) -> None:
        """Bind terminal M06/M07/M08 billing facts to the immutable export version."""
        trusted = context.trusted
        async with self._session_factory() as session:
            generation = (
                await session.execute(
                    select(
                        GenerationBillingHoldRow.currency,
                        GenerationBillingHoldRow.estimated_minor,
                        GenerationBillingHoldRow.actual_minor,
                        GenerationBillingHoldRow.released_minor,
                        GenerationBillingHoldRow.status,
                        GenerationBillingHoldRow.updated_at,
                    ).where(
                        GenerationBillingHoldRow.workspace_id == trusted.workspace_id,
                        GenerationBillingHoldRow.project_id == context.project_id,
                    )
                )
            ).all()
            audio = (
                await session.execute(
                    select(
                        audio_billing_holds.c.currency,
                        audio_billing_holds.c.estimated_minor,
                        audio_billing_holds.c.actual_minor,
                        audio_billing_holds.c.released_minor,
                        audio_billing_holds.c.status,
                        audio_billing_holds.c.updated_at,
                    ).where(
                        audio_billing_holds.c.tenant_id == trusted.tenant_id,
                        audio_billing_holds.c.workspace_id == trusted.workspace_id,
                        audio_billing_holds.c.project_id == context.project_id,
                    )
                )
            ).all()
            editing = (
                await session.execute(
                    select(
                        EditingRenderBillingHoldRow.currency,
                        EditingRenderBillingHoldRow.estimated_minor,
                        EditingRenderBillingHoldRow.actual_minor,
                        EditingRenderBillingHoldRow.released_minor,
                        EditingRenderBillingHoldRow.status,
                        EditingRenderBillingHoldRow.updated_at,
                    ).where(
                        EditingRenderBillingHoldRow.tenant_id == trusted.tenant_id,
                        EditingRenderBillingHoldRow.workspace_id == trusted.workspace_id,
                        EditingRenderBillingHoldRow.project_id == context.project_id,
                    )
                )
            ).all()
        summary, latest = _settlement_cost_summary(
            (
                ("generation", [tuple(row) for row in generation]),
                ("audio", [tuple(row) for row in audio]),
                ("editing", [tuple(row) for row in editing]),
            )
        )
        canonical = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(
            f"{trusted.tenant_id}:{trusted.workspace_id}:{context.project_id}:{project_version}:{canonical}".encode()
        ).hexdigest()
        await self._store(context).record_billing_settlement_callback(
            project_id=context.project_id,
            project_version=project_version,
            settlement_reference=f"internal:{digest}",
            settled_at=latest or datetime.now(UTC),
            event_id=f"billing-settled:{digest}",
            cost_summary=summary,
            recorded_at=datetime.now(UTC),
        )

    async def transition_review(
        self,
        request: Request,
        *,
        project_id: str,
        project_version: str,
        action: str,
        reason: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        context = await self.resolve_context(request, project_id=project_id, required_permission="compliance.manage")
        return await self._store(context).transition_review(
            project_id=project_id,
            project_version=project_version,
            action=action,
            reason=reason,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            audit=context.audit,
        )

    async def run_compliance_check(
        self,
        request: Request,
        *,
        project_id: str,
        project_version: str,
        target: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        context = await self.resolve_context(request, project_id=project_id, required_permission="compliance.manage")
        if self._compliance_provider is None:
            raise ComplianceRuntimeUnavailable("COMPLIANCE_PROVIDER_NOT_CONFIGURED")
        if self._export_executor is None:
            raise ComplianceRuntimeUnavailable("FORMAL_EXPORT_EXECUTOR_NOT_CONFIGURED")
        source = await self._export_executor.load_source_version(
            tenant_id=context.trusted.tenant_id,
            workspace_id=context.trusted.workspace_id,
            project_id=project_id,
            version_id=project_version,
        )
        facts: dict[str, object] = {
            "exportTarget": target,
            "objectKey": source.output.object_key,
            "contentSha256": source.output.content_sha256,
            "sizeBytes": source.output.size_bytes,
            "durationMs": source.output.duration_ms,
            "timelineId": source.timeline_id,
            "timelineVersionId": source.timeline_version_id,
            "container": source.profile.container,
            "videoCodec": source.profile.video_codec,
            "audioCodec": source.profile.audio_codec,
            "width": source.profile.width,
            "height": source.profile.height,
        }
        input_digest = "sha256:" + hashlib.sha256(
            json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        audit = context.audit
        result = await self._compliance_provider.review(
            ProviderReviewRequest(
                project_id=project_id,
                project_version=project_version,
                input_digest=input_digest,
                facts=facts,
                audit=audit,
            )
        )
        return await self._store(context).save_provider_assessment(
            project_id=project_id,
            project_version=project_version,
            target=target,
            snapshot_digest="sha256:" + source.output.content_sha256,
            snapshot_payload={"finalVideo": source.model_dump(mode="json")},
            input_digest=input_digest,
            result=result,
            commercial_export_enabled=self._commercial_export_enabled,
            idempotency_key=idempotency_key,
            audit=audit,
        )

    async def submit_export(
        self,
        request: Request,
        *,
        formal_request: FormalExportRequest,
        output_format: str,
        idempotency_key: str,
    ) -> DeliveryRecord:
        context = await self.resolve_context(
            request,
            project_id=formal_request.project_id,
            required_permission="export.manage",
        )
        store = self._store(context)
        existing = await store.get_delivery(project_id=formal_request.project_id, request_id=idempotency_key)
        if existing is not None:
            if (
                existing.project_version != formal_request.project_version
                or existing.target != formal_request.target
                or existing.format != output_format
            ):
                raise DeliveryIdempotencyConflict(
                    "formal export idempotency key was reused with a different request"
                )
            return existing
        audit = AuditContext(
            actor_id=context.trusted.actor_id,
            tenant_id=context.trusted.tenant_id,
            request_id=idempotency_key,
            source="xingjing_compliance_http",
            occurred_at=datetime.now(UTC),
        )
        await self._synchronize_billing_settlement(context, formal_request.project_version)
        evidence = await store.load_export_evidence(formal_request, audit)
        decision = await ComplianceRuntime(authority_store=store).authorize_export(formal_request, audit)
        if not decision.allowed or decision.manifest_context is None:
            codes = ",".join(code.value for code in decision.block_codes)
            raise ValueError(f"FORMAL_EXPORT_BLOCKED:{codes}")
        if self._export_executor is None:
            raise ComplianceRuntimeUnavailable("FORMAL_EXPORT_EXECUTOR_NOT_CONFIGURED")
        artifact = await self._export_executor.execute(
            tenant_id=context.trusted.tenant_id,
            workspace_id=context.trusted.workspace_id,
            project_id=formal_request.project_id,
            project_version=formal_request.project_version,
            output_format=output_format,
            manifest=decision.manifest_context,
            evidence=evidence,
        )
        return await store.record_delivery(decision.manifest_context, evidence, artifact)

    async def record_authorization(
        self,
        request: Request,
        *,
        project_id: str,
        project_version: str,
        authorization_id: str,
        authorization_type: str,
        subject_id: str,
        evidence_object_key: str,
        evidence_sha256: str,
        valid_from: datetime,
        valid_until: datetime | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        context = await self.resolve_context(request, project_id=project_id, required_permission="compliance.manage")
        if self._evidence_storage is None or not self._evidence_storage.verify(
            context.trusted,
            project_id,
            evidence_object_key,
            evidence_sha256,
        ):
            raise ValueError("RIGHTS_EVIDENCE_NOT_VERIFIED")
        return await self._store(context).record_authorization(
            project_id=project_id,
            project_version=project_version,
            authorization_id=authorization_id,
            authorization_type=authorization_type,
            subject_id=subject_id,
            evidence_digest="sha256:" + evidence_sha256,
            evidence_object_key=evidence_object_key,
            valid_from=valid_from,
            valid_until=valid_until,
            idempotency_key=idempotency_key,
            audit=context.audit,
        )

    async def delivery_file(
        self,
        request: Request,
        *,
        project_id: str,
        request_id: str,
    ) -> tuple[Path, str]:
        context = await self.resolve_context(request, project_id=project_id, required_permission="export.view")
        delivery = await self._store(context).get_delivery(project_id=project_id, request_id=request_id)
        if delivery is None or delivery.object_key is None or delivery.format is None:
            raise FormalExportArtifactError("FORMAL_EXPORT_DELIVERY_NOT_FOUND")
        if self._export_executor is None:
            raise ComplianceRuntimeUnavailable("FORMAL_EXPORT_EXECUTOR_NOT_CONFIGURED")
        return (
            self._export_executor.resolve_download(
                tenant_id=context.trusted.tenant_id,
                workspace_id=context.trusted.workspace_id,
                object_key=delivery.object_key,
            ),
            delivery.format,
        )

    async def close(self) -> None:
        """释放 asyncpg 连接池；可由应用 lifespan 的关闭阶段调用。"""
        engine = self._engine
        if engine is not None:
            self._engine = None
            await engine.dispose()

    def _store(self, context: ComplianceRequestContext) -> SqlAlchemyComplianceAuthorityStore:
        return SqlAlchemyComplianceAuthorityStore(self._session_factory, scope=context.authority_scope)

    async def _assert_project_scope(self, context: ComplianceRequestContext) -> None:
        try:
            async with self._session_factory() as session:
                project_id = await session.scalar(
                    select(ProjectRow.id).where(
                        ProjectRow.id == context.project_id,
                        ProjectRow.tenant_id == context.trusted.tenant_id,
                        ProjectRow.workspace_id == context.trusted.workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                )
        except SQLAlchemyError as error:
            raise ComplianceRuntimeUnavailable("COMPLIANCE_PROJECT_SCOPE_UNAVAILABLE") from error
        if project_id is None:
            raise ComplianceProjectScopeDenied("PROJECT_SCOPE_DENIED")


def create_production_compliance_runtime(
    *,
    context_resolver: TrustedContextResolver | None = None,
) -> ProductionComplianceRuntime:
    """创建 M09 的 PostgreSQL 组合，且只接受显式 asyncpg 配置。"""
    database_url = os.environ.get("XINGJING_COMPLIANCE_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeConfigurationError("XINGJING_COMPLIANCE_DATABASE_URL_REQUIRED")
    if not database_url.startswith("postgresql+asyncpg://"):
        raise RuntimeConfigurationError("XINGJING_COMPLIANCE_DATABASE_URL_MUST_USE_POSTGRESQL_ASYNCPG")
    try:
        engine = create_async_engine(database_url, pool_pre_ping=True)
    except (ModuleNotFoundError, SQLAlchemyError, ValueError) as error:
        raise ComplianceRuntimeUnavailable("COMPLIANCE_RUNTIME_DEPENDENCY_UNAVAILABLE") from error
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    render_root_value = os.environ.get("XINGJING_EDITING_OBJECT_STORAGE_ROOT", "").strip()
    export_root_value = os.environ.get("XINGJING_COMPLIANCE_EXPORT_ROOT", "").strip()
    if not render_root_value:
        raise RuntimeConfigurationError("XINGJING_EDITING_OBJECT_STORAGE_ROOT_REQUIRED")
    if not export_root_value:
        raise RuntimeConfigurationError("XINGJING_COMPLIANCE_EXPORT_ROOT_REQUIRED")
    render_root = Path(render_root_value)
    export_root = Path(export_root_value)
    try:
        export_executor = LocalFormalExportExecutor(
            session_factory=session_factory,
            render_root=render_root,
            export_root=export_root,
        )
    except FormalExportArtifactError as error:
        raise RuntimeConfigurationError(error.code) from error
    evidence_root = os.environ.get("XINGJING_ASSET_EVIDENCE_ROOT", "").strip()
    if not evidence_root:
        raise RuntimeConfigurationError("XINGJING_ASSET_EVIDENCE_ROOT_REQUIRED")
    evidence_storage = LocalAssetEvidenceStorage(evidence_root)
    provider_url = os.environ.get("XINGJING_COMPLIANCE_PROVIDER_URL", "").strip()
    provider_token = os.environ.get("XINGJING_COMPLIANCE_PROVIDER_TOKEN", "").strip()
    compliance_provider: ComplianceProvider | None = None
    if provider_url or provider_token:
        try:
            compliance_provider = HttpComplianceProvider(url=provider_url, bearer_token=provider_token)
        except ComplianceProviderError as error:
            raise RuntimeConfigurationError(error.code) from error
    commercial_export_enabled = os.environ.get("XINGJING_COMMERCIAL_EXPORT_ENABLED", "false").lower() == "true"
    billing_callback_secret = os.environ.get("XINGJING_BILLING_CALLBACK_SECRET", "").strip()
    if billing_callback_secret and len(billing_callback_secret) < 32:
        raise RuntimeConfigurationError("XINGJING_BILLING_CALLBACK_SECRET_MINIMUM_32_REQUIRED")
    return ProductionComplianceRuntime(
        session_factory=session_factory,
        context_resolver=context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        engine=engine,
        export_executor=export_executor,
        evidence_storage=evidence_storage,
        compliance_provider=compliance_provider,
        commercial_export_enabled=commercial_export_enabled,
        billing_callback_secret=billing_callback_secret or None,
    )


def _required_callback_text(body: dict[str, object], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"BILLING_CALLBACK_{field.upper()}_INVALID")
    return value.strip()


def _callback_cost_summary(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError("BILLING_CALLBACK_COST_SUMMARY_REQUIRED")
    result: list[dict[str, object]] = []
    allowed_dimensions = {"project", "episode", "shot", "model", "member", "compute"}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("BILLING_CALLBACK_COST_SUMMARY_INVALID")
        dimension = item.get("dimension")
        reference_id = item.get("referenceId")
        currency = item.get("currency")
        actual_minor = item.get("actualMinor")
        if (
            dimension not in allowed_dimensions
            or not isinstance(reference_id, str)
            or not reference_id.strip()
            or len(reference_id) > 128
            or not isinstance(currency, str)
            or len(currency.strip()) != 3
            or not isinstance(actual_minor, int)
            or isinstance(actual_minor, bool)
            or actual_minor < 0
        ):
            raise ValueError("BILLING_CALLBACK_COST_SUMMARY_INVALID")
        result.append(
            {
                "dimension": dimension,
                "reference_id": reference_id.strip(),
                "currency": currency.strip().upper(),
                "actual_minor": actual_minor,
            }
        )
    return result
