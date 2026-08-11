"""Formal-export authority persistence.

This module deliberately owns separate SQLAlchemy metadata.  Production code
must run its Alembic migration; the adapter never creates tables implicitly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from server.xingjing_compliance.models import (
    AuditContext,
    ExportAuthorityState,
    ExportManifestContext,
    FormalExportRequest,
    ManualReviewStatus,
    ProviderReviewResult,
    ReviewConclusion,
)
from server.xingjing_compliance_runtime.contracts import DeliveryRecord, ExportArtifact, ExportAuthorityEvidence
from server.xingjing_compliance_runtime.errors import AuthorityDataMissing


class CompliancePersistenceBase(DeclarativeBase):
    """独立 metadata，由正式 Alembic 链显式接入。"""


class DeliveryIdempotencyConflict(RuntimeError):
    """同一导出请求号不得对应两个不同的正式交付清单。"""


class ComplianceVersionConflict(RuntimeError):
    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__("compliance review version conflict")


class ComplianceIdempotencyConflict(RuntimeError):
    """同一幂等键不得表示不同的合规写命令。"""


class PolicyRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_policies"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(128), primary_key=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rules_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    rules_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class AuthorizationRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_authorizations"
    __table_args__ = (
        CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_xj_authorization_valid_window"),
        CheckConstraint("length(evidence_digest) >= 16", name="ck_xj_authorization_evidence_digest"),
        Index(
            "ix_xj_compliance_authorization_export",
            "tenant_id",
            "workspace_id",
            "project_id",
            "project_version",
            "valid_from",
            "authorization_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    authorization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    authorization_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    evidence_object_key: Mapped[str | None] = mapped_column(String(1024))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_reviews"
    __table_args__ = (
        CheckConstraint(
            "conclusion IN ('approved', 'manual_review_required', 'blocked')",
            name="ck_xj_compliance_review_conclusion",
        ),
        CheckConstraint(
            "manual_review_status IN ('not_required', 'pending', 'approved', 'rejected', 'appealed')",
            name="ck_xj_compliance_review_manual_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "policy_id", "policy_version"],
            [
                "xingjing_compliance_policies.tenant_id",
                "xingjing_compliance_policies.workspace_id",
                "xingjing_compliance_policies.policy_id",
                "xingjing_compliance_policies.version",
            ],
            ondelete="RESTRICT",
            name="fk_xj_compliance_review_policy",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    conclusion: Mapped[str] = mapped_column(String(32), nullable=False)
    manual_review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ComplianceAuditRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_audit_events"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_xj_compliance_audit_version"),
        Index(
            "ix_xj_compliance_audit_project_time",
            "tenant_id",
            "workspace_id",
            "project_id",
            "occurred_at",
            "event_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "project_id",
            "idempotency_key",
            name="uq_xj_compliance_audit_idempotency",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_version: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    command_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectSnapshotRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_project_snapshots"
    __table_args__ = (
        CheckConstraint("length(snapshot_digest) >= 16", name="ck_xj_compliance_snapshot_digest"),
        Index(
            "ix_xj_compliance_snapshot_project",
            "tenant_id",
            "workspace_id",
            "project_id",
            "project_version",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    snapshot_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExportConfigurationRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_export_configurations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "project_id", "snapshot_version"],
            [
                "xingjing_compliance_project_snapshots.tenant_id",
                "xingjing_compliance_project_snapshots.workspace_id",
                "xingjing_compliance_project_snapshots.project_id",
                "xingjing_compliance_project_snapshots.project_version",
            ],
            ondelete="RESTRICT",
            name="fk_xj_compliance_export_snapshot",
        ),
        CheckConstraint("length(target) > 0", name="ck_xj_compliance_export_target"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target: Mapped[str] = mapped_column(String(128), primary_key=True)
    current_project_version: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_version: Mapped[str] = mapped_column(String(128), nullable=False)
    export_permitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    aigc_marking_satisfied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    commercial_export_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingSettlementRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_billing_settlements"
    __table_args__ = (
        CheckConstraint("settled_at IS NULL OR settled = true", name="ck_xj_billing_settlement_timestamp"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    settled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    settlement_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    cost_summary: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeliveryRow(CompliancePersistenceBase):
    __tablename__ = "xingjing_compliance_export_deliveries"
    __table_args__ = (
        CheckConstraint("length(manifest_digest) = 71", name="ck_xj_delivery_manifest_digest"),
        Index(
            "ix_xj_compliance_delivery_project_created",
            "tenant_id",
            "workspace_id",
            "project_id",
            "created_at",
            "request_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_version: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    manifest_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str | None] = mapped_column(String(32))
    format: Mapped[str | None] = mapped_column(String(32))
    object_key: Mapped[str | None] = mapped_column(String(1024))
    content_sha256: Mapped[str | None] = mapped_column(String(71))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    download_path: Mapped[str | None] = mapped_column(String(1024))


SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class ComplianceAuthorityScope:
    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.workspace_id.strip():
            raise ValueError("tenant_id and workspace_id are required")


class SqlAlchemyComplianceAuthorityStore:
    """以 PostgreSQL 事务读取 M09/S06 正式导出的权威事实。"""

    def __init__(self, session_factory: SessionFactory, *, scope: ComplianceAuthorityScope) -> None:
        self._session_factory = session_factory
        self._scope = scope

    async def save_policy(
        self,
        *,
        policy_id: str,
        version: str,
        effective_at: datetime,
        rules_digest: str,
        rules_payload: dict[str, object],
    ) -> None:
        await self._insert_immutable(
            PolicyRow(
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                policy_id=policy_id,
                version=version,
                effective_at=_utc(effective_at),
                rules_digest=rules_digest,
                rules_payload=rules_payload,
            )
        )

    async def save_authorization(
        self,
        *,
        project_id: str,
        project_version: str,
        authorization_id: str,
        authorization_type: str,
        subject_id: str,
        evidence_digest: str,
        valid_from: datetime,
        valid_until: datetime | None,
        revoked_at: datetime | None,
        recorded_at: datetime,
    ) -> None:
        await self._insert_immutable(
            AuthorizationRow(
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                project_id=project_id,
                project_version=project_version,
                authorization_id=authorization_id,
                authorization_type=authorization_type,
                subject_id=subject_id,
                evidence_digest=evidence_digest,
                evidence_object_key=None,
                valid_from=_utc(valid_from),
                valid_until=None if valid_until is None else _utc(valid_until),
                revoked_at=None if revoked_at is None else _utc(revoked_at),
                recorded_at=_utc(recorded_at),
            )
        )

    async def save_review(
        self,
        *,
        project_id: str,
        project_version: str,
        assessment_id: str,
        policy_id: str,
        policy_version: str,
        conclusion: ReviewConclusion,
        manual_review_status: ManualReviewStatus,
        input_digest: str,
        evidence_digest: str,
        reviewed_at: datetime,
        review_payload: dict[str, object],
    ) -> None:
        await self._insert_immutable(
            ReviewRow(
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                project_id=project_id,
                project_version=project_version,
                assessment_id=assessment_id,
                policy_id=policy_id,
                policy_version=policy_version,
                conclusion=conclusion.value,
                manual_review_status=manual_review_status.value,
                input_digest=input_digest,
                evidence_digest=evidence_digest,
                reviewed_at=_utc(reviewed_at),
                review_payload=review_payload,
                version=1,
            )
        )

    async def record_authorization(
        self,
        *,
        project_id: str,
        project_version: str,
        authorization_id: str,
        authorization_type: str,
        subject_id: str,
        evidence_digest: str,
        evidence_object_key: str,
        valid_from: datetime,
        valid_until: datetime | None,
        idempotency_key: str,
        audit: AuditContext,
    ) -> dict[str, object]:
        self._assert_audit(audit)
        payload: dict[str, object] = {
            "authorization_id": authorization_id,
            "authorization_type": authorization_type,
            "subject_id": subject_id,
            "evidence_digest": evidence_digest,
            "evidence_object_key": evidence_object_key,
            "valid_from": _utc(valid_from).isoformat(),
            "valid_until": None if valid_until is None else _utc(valid_until).isoformat(),
        }
        command_digest = _digest({**payload, "project_id": project_id, "project_version": project_version})
        async with self._session_factory.begin() as session:
            replay = await session.scalar(
                select(ComplianceAuditRow).where(
                    ComplianceAuditRow.tenant_id == self._scope.tenant_id,
                    ComplianceAuditRow.workspace_id == self._scope.workspace_id,
                    ComplianceAuditRow.project_id == project_id,
                    ComplianceAuditRow.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                if replay.command_digest != command_digest:
                    raise ComplianceIdempotencyConflict()
                return dict(replay.after_payload)
            statement = (
                insert(AuthorizationRow)
                .values(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    project_id=project_id,
                    project_version=project_version,
                    authorization_id=authorization_id,
                    authorization_type=authorization_type,
                    subject_id=subject_id,
                    evidence_digest=evidence_digest,
                    evidence_object_key=evidence_object_key,
                    valid_from=_utc(valid_from),
                    valid_until=None if valid_until is None else _utc(valid_until),
                    revoked_at=None,
                    recorded_at=_utc(audit.occurred_at),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        "tenant_id",
                        "workspace_id",
                        "project_id",
                        "project_version",
                        "authorization_id",
                    ]
                )
            )
            await session.execute(statement)
            stored = await self._one(
                session,
                select(AuthorizationRow).where(
                    AuthorizationRow.tenant_id == self._scope.tenant_id,
                    AuthorizationRow.workspace_id == self._scope.workspace_id,
                    AuthorizationRow.project_id == project_id,
                    AuthorizationRow.project_version == project_version,
                    AuthorizationRow.authorization_id == authorization_id,
                ),
                "authorization",
            )
            stored_payload = {
                "authorization_id": stored.authorization_id,
                "authorization_type": stored.authorization_type,
                "subject_id": stored.subject_id,
                "evidence_digest": stored.evidence_digest,
                "evidence_object_key": stored.evidence_object_key,
                "valid_from": _utc(stored.valid_from).isoformat(),
                "valid_until": None if stored.valid_until is None else _utc(stored.valid_until).isoformat(),
            }
            if stored_payload != payload:
                raise ComplianceIdempotencyConflict()
            session.add(
                ComplianceAuditRow(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    event_id=_event_id(project_id, idempotency_key),
                    project_id=project_id,
                    project_version=project_version,
                    actor_id=audit.actor_id,
                    request_id=audit.request_id,
                    idempotency_key=idempotency_key,
                    command_digest=command_digest,
                    action="authorization.record",
                    version=1,
                    before_payload={},
                    after_payload=stored_payload,
                    occurred_at=_utc(audit.occurred_at),
                )
            )
        return stored_payload

    async def transition_review(
        self,
        *,
        project_id: str,
        project_version: str,
        action: str,
        reason: str,
        expected_version: int,
        idempotency_key: str,
        audit: AuditContext,
    ) -> dict[str, object]:
        self._assert_audit(audit)
        if not reason.strip():
            raise ValueError("COMPLIANCE_REASON_REQUIRED")
        if action not in {"appeal", "approve", "reject"}:
            raise ValueError("COMPLIANCE_ACTION_INVALID")
        command = {
            "project_id": project_id,
            "project_version": project_version,
            "action": action,
            "reason": reason.strip(),
            "expected_version": expected_version,
            "actor_id": audit.actor_id,
        }
        command_digest = _digest(command)
        async with self._session_factory.begin() as session:
            replay = await session.scalar(
                select(ComplianceAuditRow).where(
                    ComplianceAuditRow.tenant_id == self._scope.tenant_id,
                    ComplianceAuditRow.workspace_id == self._scope.workspace_id,
                    ComplianceAuditRow.project_id == project_id,
                    ComplianceAuditRow.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                if replay.command_digest != command_digest:
                    raise ComplianceIdempotencyConflict()
                return dict(replay.after_payload)
            review = await self._one(
                session,
                select(ReviewRow).where(
                    ReviewRow.tenant_id == self._scope.tenant_id,
                    ReviewRow.workspace_id == self._scope.workspace_id,
                    ReviewRow.project_id == project_id,
                    ReviewRow.project_version == project_version,
                ),
                "review",
            )
            if review.version != expected_version:
                raise ComplianceVersionConflict(review.version)
            next_status, next_conclusion = _review_transition(
                current_status=review.manual_review_status,
                action=action,
                current_conclusion=review.conclusion,
            )
            before = {
                "manual_review_status": review.manual_review_status,
                "conclusion": review.conclusion,
                "version": review.version,
            }
            after = {
                "manual_review_status": next_status,
                "conclusion": next_conclusion,
                "version": review.version + 1,
                "reason": reason.strip(),
            }
            result = await session.execute(
                update(ReviewRow)
                .where(
                    ReviewRow.tenant_id == self._scope.tenant_id,
                    ReviewRow.workspace_id == self._scope.workspace_id,
                    ReviewRow.project_id == project_id,
                    ReviewRow.project_version == project_version,
                    ReviewRow.version == expected_version,
                )
                .values(
                    manual_review_status=next_status,
                    conclusion=next_conclusion,
                    version=expected_version + 1,
                    reviewed_at=_utc(audit.occurred_at),
                    review_payload={**review.review_payload, "last_action": after},
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                current = await session.scalar(
                    select(ReviewRow.version).where(
                        ReviewRow.tenant_id == self._scope.tenant_id,
                        ReviewRow.workspace_id == self._scope.workspace_id,
                        ReviewRow.project_id == project_id,
                        ReviewRow.project_version == project_version,
                    )
                )
                raise ComplianceVersionConflict(current or 0)
            session.add(
                ComplianceAuditRow(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    event_id=_event_id(project_id, idempotency_key),
                    project_id=project_id,
                    project_version=project_version,
                    actor_id=audit.actor_id,
                    request_id=audit.request_id,
                    idempotency_key=idempotency_key,
                    command_digest=command_digest,
                    action=f"review.{action}",
                    version=expected_version + 1,
                    before_payload=before,
                    after_payload=after,
                    occurred_at=_utc(audit.occurred_at),
                )
            )
        return after

    async def save_provider_assessment(
        self,
        *,
        project_id: str,
        project_version: str,
        target: str,
        snapshot_digest: str,
        snapshot_payload: dict[str, object],
        input_digest: str,
        result: ProviderReviewResult,
        commercial_export_enabled: bool,
        idempotency_key: str,
        audit: AuditContext,
    ) -> dict[str, object]:
        self._assert_audit(audit)
        assessment_id = "assessment-" + hashlib.sha256(
            (
                f"{project_id}\0{project_version}\0{input_digest}\0{result.provider_id}\0"
                f"{result.provider_version}\0{result.evidence_digest}"
            ).encode()
        ).hexdigest()
        manual_status = (
            ManualReviewStatus.NOT_REQUIRED
            if result.conclusion is ReviewConclusion.APPROVED
            else ManualReviewStatus.PENDING
        )
        risks = [
            {
                "rule_id": risk.rule_id,
                "risk_level": risk.risk_level.value,
                "effect": risk.effect.value,
                "message": risk.message,
                "evidence": dict(risk.evidence),
                "missing_authorization_type": risk.missing_authorization_type,
            }
            for risk in result.risks
        ]
        command_payload = {
            "project_id": project_id,
            "project_version": project_version,
            "target": target,
            "snapshot_digest": snapshot_digest,
            "input_digest": input_digest,
            "provider_id": result.provider_id,
            "provider_version": result.provider_version,
            "conclusion": result.conclusion.value,
            "evidence_digest": result.evidence_digest,
            "policy_digest": result.policy_digest,
            "risks": risks,
            "commercial_export_enabled": commercial_export_enabled,
        }
        command_digest = _digest(command_payload)
        async with self._session_factory.begin() as session:
            replay = await session.scalar(
                select(ComplianceAuditRow).where(
                    ComplianceAuditRow.tenant_id == self._scope.tenant_id,
                    ComplianceAuditRow.workspace_id == self._scope.workspace_id,
                    ComplianceAuditRow.project_id == project_id,
                    ComplianceAuditRow.idempotency_key == idempotency_key,
                )
            )
            if replay is not None:
                if replay.command_digest != command_digest:
                    raise ComplianceIdempotencyConflict()
                return dict(replay.after_payload)
            await session.execute(
                insert(PolicyRow)
                .values(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    policy_id=result.provider_id,
                    version=result.provider_version,
                    effective_at=_utc(audit.occurred_at),
                    rules_digest=result.policy_digest,
                    rules_payload=dict(result.policy_summary),
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "workspace_id", "policy_id", "version"]
                )
            )
            policy = await self._one(
                session,
                select(PolicyRow).where(
                    PolicyRow.tenant_id == self._scope.tenant_id,
                    PolicyRow.workspace_id == self._scope.workspace_id,
                    PolicyRow.policy_id == result.provider_id,
                    PolicyRow.version == result.provider_version,
                ),
                "policy",
            )
            if policy.rules_digest != result.policy_digest:
                raise ComplianceIdempotencyConflict()
            await session.execute(
                insert(ProjectSnapshotRow)
                .values(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    project_id=project_id,
                    project_version=project_version,
                    snapshot_digest=snapshot_digest,
                    snapshot_payload=snapshot_payload,
                    is_immutable=True,
                    created_at=_utc(audit.occurred_at),
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "workspace_id", "project_id", "project_version"]
                )
            )
            snapshot = await self._one(
                session,
                select(ProjectSnapshotRow).where(
                    ProjectSnapshotRow.tenant_id == self._scope.tenant_id,
                    ProjectSnapshotRow.workspace_id == self._scope.workspace_id,
                    ProjectSnapshotRow.project_id == project_id,
                    ProjectSnapshotRow.project_version == project_version,
                ),
                "immutable project snapshot",
            )
            if snapshot.snapshot_digest != snapshot_digest:
                raise ComplianceIdempotencyConflict()
            current = await session.scalar(
                select(ReviewRow).where(
                    ReviewRow.tenant_id == self._scope.tenant_id,
                    ReviewRow.workspace_id == self._scope.workspace_id,
                    ReviewRow.project_id == project_id,
                    ReviewRow.project_version == project_version,
                )
            )
            before: dict[str, object] = {}
            if current is None:
                session.add(
                    ReviewRow(
                        tenant_id=self._scope.tenant_id,
                        workspace_id=self._scope.workspace_id,
                        project_id=project_id,
                        project_version=project_version,
                        assessment_id=assessment_id,
                        policy_id=result.provider_id,
                        policy_version=result.provider_version,
                        conclusion=result.conclusion.value,
                        manual_review_status=manual_status.value,
                        input_digest=input_digest,
                        evidence_digest=result.evidence_digest,
                        reviewed_at=_utc(audit.occurred_at),
                        review_payload={"risks": risks, "provider_evidence_digest": result.evidence_digest},
                        version=1,
                    )
                )
                review_version = 1
                after = {
                    "assessment_id": assessment_id,
                    "conclusion": result.conclusion.value,
                    "manual_review_status": manual_status.value,
                    "review_version": review_version,
                    "risks": risks,
                    "policy": {"id": result.provider_id, "version": result.provider_version},
                }
            else:
                before = {
                    "assessment_id": current.assessment_id,
                    "conclusion": current.conclusion,
                    "manual_review_status": current.manual_review_status,
                    "review_version": current.version,
                    "policy": {"id": current.policy_id, "version": current.policy_version},
                }
                unchanged = (
                    current.input_digest == input_digest
                    and current.policy_id == result.provider_id
                    and current.policy_version == result.provider_version
                    and current.evidence_digest == result.evidence_digest
                    and current.conclusion == result.conclusion.value
                )
                review_version = current.version if unchanged else current.version + 1
                after = {
                    "assessment_id": assessment_id if not unchanged else current.assessment_id,
                    "conclusion": current.conclusion if unchanged else result.conclusion.value,
                    "manual_review_status": current.manual_review_status if unchanged else manual_status.value,
                    "review_version": review_version,
                    "risks": current.review_payload.get("risks", []) if unchanged else risks,
                    "policy": {
                        "id": current.policy_id if unchanged else result.provider_id,
                        "version": current.policy_version if unchanged else result.provider_version,
                    },
                }
                if not unchanged:
                    updated = await session.execute(
                        update(ReviewRow)
                        .where(
                            ReviewRow.tenant_id == self._scope.tenant_id,
                            ReviewRow.workspace_id == self._scope.workspace_id,
                            ReviewRow.project_id == project_id,
                            ReviewRow.project_version == project_version,
                            ReviewRow.version == current.version,
                        )
                        .values(
                            assessment_id=assessment_id,
                            policy_id=result.provider_id,
                            policy_version=result.provider_version,
                            conclusion=result.conclusion.value,
                            manual_review_status=manual_status.value,
                            input_digest=input_digest,
                            evidence_digest=result.evidence_digest,
                            reviewed_at=_utc(audit.occurred_at),
                            review_payload={"risks": risks, "provider_evidence_digest": result.evidence_digest},
                            version=review_version,
                        )
                    )
                    if getattr(updated, "rowcount", 0) != 1:
                        raise ComplianceVersionConflict(current.version)
            configuration_values = {
                "current_project_version": project_version,
                "snapshot_version": project_version,
                "export_permitted": True,
                "aigc_marking_satisfied": True,
                "commercial_export_enabled": commercial_export_enabled,
                "updated_at": _utc(audit.occurred_at),
            }
            await session.execute(
                insert(ExportConfigurationRow)
                .values(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    project_id=project_id,
                    target=target,
                    **configuration_values,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "project_id", "target"],
                    set_=configuration_values,
                )
            )
            session.add(
                ComplianceAuditRow(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    event_id=_event_id(project_id, idempotency_key),
                    project_id=project_id,
                    project_version=project_version,
                    actor_id=audit.actor_id,
                    request_id=audit.request_id,
                    idempotency_key=idempotency_key,
                    command_digest=command_digest,
                    action="review.provider_check",
                    version=review_version,
                    before_payload=before,
                    after_payload=after,
                    occurred_at=_utc(audit.occurred_at),
                )
            )
        return after

    async def save_project_snapshot(
        self,
        *,
        project_id: str,
        project_version: str,
        snapshot_digest: str,
        snapshot_payload: dict[str, object],
        is_immutable: bool,
        created_at: datetime,
    ) -> None:
        await self._insert_immutable(
            ProjectSnapshotRow(
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                project_id=project_id,
                project_version=project_version,
                snapshot_digest=snapshot_digest,
                snapshot_payload=snapshot_payload,
                is_immutable=is_immutable,
                created_at=_utc(created_at),
            )
        )

    async def save_export_snapshot(
        self,
        *,
        project_id: str,
        current_project_version: str,
        snapshot_version: str,
        target: str,
        export_permitted: bool,
        aigc_marking_satisfied: bool,
        commercial_export_enabled: bool,
        updated_at: datetime,
    ) -> None:
        values = {
            "current_project_version": current_project_version,
            "snapshot_version": snapshot_version,
            "export_permitted": export_permitted,
            "aigc_marking_satisfied": aigc_marking_satisfied,
            "commercial_export_enabled": commercial_export_enabled,
            "updated_at": _utc(updated_at),
        }
        statement = insert(ExportConfigurationRow).values(
            tenant_id=self._scope.tenant_id,
            workspace_id=self._scope.workspace_id,
            project_id=project_id,
            target=target,
            **values,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["tenant_id", "workspace_id", "project_id", "target"], set_=values
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def save_billing_settlement(
        self,
        *,
        project_id: str,
        project_version: str,
        settled: bool,
        settlement_reference: str,
        settled_at: datetime | None,
        recorded_at: datetime,
    ) -> None:
        await self._insert_immutable(
            BillingSettlementRow(
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                project_id=project_id,
                project_version=project_version,
                settled=settled,
                settlement_reference=settlement_reference,
                settled_at=None if settled_at is None else _utc(settled_at),
                recorded_at=_utc(recorded_at),
            )
        )

    async def record_billing_settlement_callback(
        self,
        *,
        project_id: str,
        project_version: str,
        settlement_reference: str,
        settled_at: datetime,
        event_id: str,
        cost_summary: list[dict[str, object]],
        recorded_at: datetime,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "project_id": project_id,
            "project_version": project_version,
            "settled": True,
            "settlement_reference": settlement_reference,
            "settled_at": _utc(settled_at).isoformat(),
            "cost_summary": cost_summary,
        }
        command_digest = _digest(payload)
        async with self._session_factory.begin() as session:
            replay = await session.scalar(
                select(ComplianceAuditRow).where(
                    ComplianceAuditRow.tenant_id == self._scope.tenant_id,
                    ComplianceAuditRow.workspace_id == self._scope.workspace_id,
                    ComplianceAuditRow.project_id == project_id,
                    ComplianceAuditRow.idempotency_key == event_id,
                )
            )
            if replay is not None:
                if replay.command_digest != command_digest:
                    raise ComplianceIdempotencyConflict()
                return dict(replay.after_payload)
            await session.execute(
                insert(BillingSettlementRow)
                .values(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    project_id=project_id,
                    project_version=project_version,
                    settled=True,
                    settlement_reference=settlement_reference,
                    cost_summary=cost_summary,
                    settled_at=_utc(settled_at),
                    recorded_at=_utc(recorded_at),
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "workspace_id", "project_id", "project_version"]
                )
            )
            stored = await self._one(
                session,
                select(BillingSettlementRow).where(
                    BillingSettlementRow.tenant_id == self._scope.tenant_id,
                    BillingSettlementRow.workspace_id == self._scope.workspace_id,
                    BillingSettlementRow.project_id == project_id,
                    BillingSettlementRow.project_version == project_version,
                ),
                "billing settlement",
            )
            stored_payload = {
                "project_id": stored.project_id,
                "project_version": stored.project_version,
                "settled": stored.settled,
                "settlement_reference": stored.settlement_reference,
                "settled_at": None if stored.settled_at is None else _utc(stored.settled_at).isoformat(),
                "cost_summary": list(stored.cost_summary),
            }
            if stored_payload != payload:
                raise ComplianceIdempotencyConflict()
            session.add(
                ComplianceAuditRow(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    event_id=_event_id(project_id, event_id),
                    project_id=project_id,
                    project_version=project_version,
                    actor_id="billing-service",
                    request_id=event_id,
                    idempotency_key=event_id,
                    command_digest=command_digest,
                    action="billing.settlement_recorded",
                    version=1,
                    before_payload={},
                    after_payload=stored_payload,
                    occurred_at=_utc(recorded_at),
                )
            )
        return stored_payload

    async def load_compliance_status(
        self, request: FormalExportRequest, audit: AuditContext
    ) -> ExportAuthorityEvidence:
        return await self._load_evidence(request, audit, require_complete=False)

    async def load_export_evidence(self, request: FormalExportRequest, audit: AuditContext) -> ExportAuthorityEvidence:
        return await self._load_evidence(request, audit, require_complete=True)

    async def _load_evidence(
        self,
        request: FormalExportRequest,
        audit: AuditContext,
        *,
        require_complete: bool,
    ) -> ExportAuthorityEvidence:
        self._assert_audit(audit)
        async with self._session_factory() as session:
            review = await self._one(
                session,
                select(ReviewRow).where(
                    ReviewRow.tenant_id == self._scope.tenant_id,
                    ReviewRow.workspace_id == self._scope.workspace_id,
                    ReviewRow.project_id == request.project_id,
                    ReviewRow.project_version == request.project_version,
                ).order_by(ReviewRow.reviewed_at.desc(), ReviewRow.assessment_id.desc()).limit(1),
                "review",
            )
            await self._one(
                session,
                select(PolicyRow).where(
                    PolicyRow.tenant_id == self._scope.tenant_id,
                    PolicyRow.workspace_id == self._scope.workspace_id,
                    PolicyRow.policy_id == review.policy_id,
                    PolicyRow.version == review.policy_version,
                ),
                "policy",
            )
            snapshot = await self._one(
                session,
                select(ProjectSnapshotRow).where(
                    ProjectSnapshotRow.tenant_id == self._scope.tenant_id,
                    ProjectSnapshotRow.workspace_id == self._scope.workspace_id,
                    ProjectSnapshotRow.project_id == request.project_id,
                    ProjectSnapshotRow.project_version == request.project_version,
                ),
                "immutable project snapshot",
            )
            configuration = await self._one(
                session,
                select(ExportConfigurationRow).where(
                    ExportConfigurationRow.tenant_id == self._scope.tenant_id,
                    ExportConfigurationRow.workspace_id == self._scope.workspace_id,
                    ExportConfigurationRow.project_id == request.project_id,
                    ExportConfigurationRow.target == request.target,
                ),
                "export configuration",
            )
            settlement = await session.scalar(
                select(BillingSettlementRow).where(
                    BillingSettlementRow.tenant_id == self._scope.tenant_id,
                    BillingSettlementRow.workspace_id == self._scope.workspace_id,
                    BillingSettlementRow.project_id == request.project_id,
                    BillingSettlementRow.project_version == request.project_version,
                )
            )
            authorizations = (
                await session.scalars(
                    select(AuthorizationRow.authorization_id)
                    .where(
                        AuthorizationRow.tenant_id == self._scope.tenant_id,
                        AuthorizationRow.workspace_id == self._scope.workspace_id,
                        AuthorizationRow.project_id == request.project_id,
                        AuthorizationRow.project_version == request.project_version,
                        AuthorizationRow.valid_from <= audit.occurred_at,
                        (AuthorizationRow.valid_until.is_(None)) | (AuthorizationRow.valid_until > audit.occurred_at),
                        (AuthorizationRow.revoked_at.is_(None)) | (AuthorizationRow.revoked_at > audit.occurred_at),
                    )
                    .order_by(AuthorizationRow.authorization_id)
                )
            ).all()
        if require_complete and settlement is None:
            raise AuthorityDataMissing("authoritative billing settlement is missing")
        if require_complete and not authorizations:
            raise AuthorityDataMissing("authoritative authorization is missing")
        return ExportAuthorityEvidence(
            state=ExportAuthorityState(
                tenant_id=self._scope.tenant_id,
                current_project_version=(
                    configuration.current_project_version
                    if configuration.current_project_version == configuration.snapshot_version
                    else f"{configuration.current_project_version}!={configuration.snapshot_version}"
                ),
                snapshot_is_immutable=snapshot.is_immutable,
                has_export_permission=configuration.export_permitted,
                compliance_conclusion=ReviewConclusion(review.conclusion),
                compliance_project_version=review.project_version,
                manual_review_status=ManualReviewStatus(review.manual_review_status),
                authorizations_complete=True,
                billing_settled=settlement is not None and settlement.settled,
                aigc_marking_satisfied=configuration.aigc_marking_satisfied,
                commercial_export_enabled=configuration.commercial_export_enabled,
            ),
            policy_id=review.policy_id,
            policy_version=review.policy_version,
            authorization_ids=tuple(authorizations),
            reviewed_at=_utc(review.reviewed_at),
            review_version=review.version,
            billing_summary=() if settlement is None else tuple(dict(item) for item in settlement.cost_summary),
        )

    async def record_delivery(
        self,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
        artifact: ExportArtifact | None = None,
    ) -> DeliveryRecord:
        self._assert_manifest(manifest)
        summary = await self._delivery_summary(manifest, evidence, artifact)
        digest = _digest(summary)
        values = {
            "tenant_id": self._scope.tenant_id,
            "workspace_id": self._scope.workspace_id,
            "project_id": manifest.project_id,
            "request_id": manifest.request_id,
            "project_version": manifest.project_version,
            "target": manifest.target,
            "policy_id": evidence.policy_id,
            "policy_version": evidence.policy_version,
            "manifest_digest": digest,
            "manifest_summary": summary,
            "created_at": _utc(manifest.authorized_at),
            "status": "succeeded" if artifact is not None else None,
            "format": None if artifact is None else artifact.format,
            "object_key": None if artifact is None else artifact.object_key,
            "content_sha256": None if artifact is None else artifact.content_sha256,
            "size_bytes": None if artifact is None else artifact.size_bytes,
            "download_path": None if artifact is None else artifact.download_path,
        }
        statement = (
            insert(DeliveryRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "workspace_id", "project_id", "request_id"])
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)
            row = await self._one(
                session,
                select(DeliveryRow).where(
                    DeliveryRow.tenant_id == self._scope.tenant_id,
                    DeliveryRow.workspace_id == self._scope.workspace_id,
                    DeliveryRow.project_id == manifest.project_id,
                    DeliveryRow.request_id == manifest.request_id,
                ),
                "delivery record",
            )
        if row.manifest_digest != digest:
            raise DeliveryIdempotencyConflict(
                "formal export request_id was already recorded with a different manifest"
            )
        return self._delivery(row)

    async def list_deliveries(self, *, tenant_id: str, project_id: str) -> tuple[DeliveryRecord, ...]:
        if tenant_id != self._scope.tenant_id:
            return ()
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(DeliveryRow)
                    .where(
                        DeliveryRow.tenant_id == self._scope.tenant_id,
                        DeliveryRow.workspace_id == self._scope.workspace_id,
                        DeliveryRow.project_id == project_id,
                    )
                    .order_by(DeliveryRow.created_at, DeliveryRow.request_id)
                )
            ).all()
        return tuple(self._delivery(row) for row in rows)

    async def get_delivery(self, *, project_id: str, request_id: str) -> DeliveryRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(DeliveryRow).where(
                    DeliveryRow.tenant_id == self._scope.tenant_id,
                    DeliveryRow.workspace_id == self._scope.workspace_id,
                    DeliveryRow.project_id == project_id,
                    DeliveryRow.request_id == request_id,
                )
            )
        return None if row is None else self._delivery(row)

    async def _delivery_summary(
        self,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
        artifact: ExportArtifact | None,
    ) -> dict[str, object]:
        async with self._session_factory() as session:
            snapshot = await self._one(
                session,
                select(ProjectSnapshotRow).where(
                    ProjectSnapshotRow.tenant_id == self._scope.tenant_id,
                    ProjectSnapshotRow.workspace_id == self._scope.workspace_id,
                    ProjectSnapshotRow.project_id == manifest.project_id,
                    ProjectSnapshotRow.project_version == manifest.project_version,
                ),
                "immutable project snapshot",
            )
        state = evidence.state
        return {
            "manifest": {
                "tenant_id": manifest.tenant_id,
                "project_id": manifest.project_id,
                "project_version": manifest.project_version,
                "target": manifest.target,
                "actor_id": manifest.actor_id,
                "request_id": manifest.request_id,
                "authorized_at": _utc(manifest.authorized_at).isoformat(),
            },
            "policy": {"id": evidence.policy_id, "version": evidence.policy_version},
            "reviewed_at": _utc(evidence.reviewed_at).isoformat(),
            "review_version": evidence.review_version,
            "authorization_ids": list(evidence.authorization_ids),
            "project_snapshot": {"digest": snapshot.snapshot_digest, "immutable": snapshot.is_immutable},
            "authority": {
                "current_project_version": state.current_project_version,
                "compliance_project_version": state.compliance_project_version,
                "conclusion": state.compliance_conclusion.value,
                "manual_review_status": state.manual_review_status.value,
                "billing_settled": state.billing_settled,
                "aigc_marking_satisfied": state.aigc_marking_satisfied,
                "commercial_export_enabled": state.commercial_export_enabled,
            },
            "billing_summary": list(evidence.billing_summary),
            "artifact": None
            if artifact is None
            else {
                "format": artifact.format,
                "object_key": artifact.object_key,
                "content_sha256": artifact.content_sha256,
                "size_bytes": artifact.size_bytes,
                "download_path": artifact.download_path,
            },
        }

    async def _insert_immutable(self, row: object) -> None:
        async with self._session_factory.begin() as session:
            session.add(row)

    async def _one(self, session: AsyncSession, statement: Any, name: str) -> Any:
        row = await session.scalar(statement)
        if row is None:
            raise AuthorityDataMissing(f"authoritative {name} is missing")
        return row

    def _assert_audit(self, audit: AuditContext) -> None:
        if audit.tenant_id != self._scope.tenant_id:
            raise AuthorityDataMissing("authoritative tenant scope is missing")

    def _assert_manifest(self, manifest: ExportManifestContext) -> None:
        if manifest.tenant_id != self._scope.tenant_id:
            raise AuthorityDataMissing("authoritative tenant scope is missing")

    @staticmethod
    def _delivery(row: DeliveryRow) -> DeliveryRecord:
        return DeliveryRecord(
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            project_version=row.project_version,
            request_id=row.request_id,
            policy_version=row.policy_version,
            manifest_digest=row.manifest_digest,
            created_at=_utc(row.created_at),
            target=row.target,
            status=row.status or "succeeded",
            format=row.format,
            object_key=row.object_key,
            content_sha256=row.content_sha256,
            size_bytes=row.size_bytes,
            download_path=row.download_path,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authoritative timestamps must include a timezone")
    return value.astimezone(UTC)


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _event_id(project_id: str, idempotency_key: str) -> str:
    encoded = f"{project_id}\0{idempotency_key}".encode()
    return "evt-" + hashlib.sha256(encoded).hexdigest()


def _review_transition(*, current_status: str, action: str, current_conclusion: str) -> tuple[str, str]:
    transitions = {
        ("rejected", "appeal"): ("appealed", "manual_review_required"),
        ("pending", "approve"): ("approved", "approved"),
        ("appealed", "approve"): ("approved", "approved"),
        ("pending", "reject"): ("rejected", "blocked"),
        ("appealed", "reject"): ("rejected", "blocked"),
    }
    resolved = transitions.get((current_status, action))
    if resolved is None:
        raise ValueError(f"INVALID_REVIEW_TRANSITION:{current_status}:{action}:{current_conclusion}")
    return resolved
