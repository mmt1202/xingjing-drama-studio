from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from server.xingjing_storyboard.errors import ContractViolation, IdempotencyConflict, VersionConflict
from server.xingjing_storyboard.ports import AuditEvent, StoryboardScope
from server.xingjing_storyboard.prompts import PromptTemplate

from .repository import AuditRow, Base, IdempotencyRow, SessionFactory


class PromptTemplateRow(Base):
    __tablename__ = "xingjing_storyboard_prompt_templates"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_xj_prompt_version_positive"),
        CheckConstraint(
            "media_type IN ('image', 'video', 'both')",
            name="ck_xj_prompt_media_type",
        ),
        Index("ix_xj_prompt_scope_updated", "tenant_id", "workspace_id", "project_id", "updated_at", "prompt_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    prompt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    template: Mapped[str] = mapped_column(String(12000), nullable=False)
    negative_prompt: Mapped[str] = mapped_column(String(12000), nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    model_adapter_versions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SmartStoryboardRequestRow(Base):
    __tablename__ = "xingjing_smart_storyboard_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_xj_smart_storyboard_status",
        ),
        CheckConstraint("attempts >= 1", name="ck_xj_smart_storyboard_attempts"),
        Index(
            "ix_xj_smart_storyboard_scope_updated",
            "tenant_id",
            "workspace_id",
            "project_id",
            "updated_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSON)
    failure_reason: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class SmartStoryboardClaim:
    claimed: bool
    status: str
    result_payload: dict[str, object] | None


class SqlAlchemySmartStoryboardRequestRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def claim(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        now: datetime,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> SmartStoryboardClaim:
        if not idempotency_key.strip():
            raise ContractViolation("IDEMPOTENCY_KEY_REQUIRED")
        if len(request_fingerprint) != 64:
            raise ContractViolation("SMART_STORYBOARD_FINGERPRINT_INVALID")
        try:
            with self._session_factory() as session, session.begin():
                row = session.get(
                    SmartStoryboardRequestRow,
                    (scope.tenant_id, scope.workspace_id, scope.project_id, idempotency_key),
                    with_for_update=True,
                )
                if row is None:
                    session.add(
                        SmartStoryboardRequestRow(
                            tenant_id=scope.tenant_id,
                            workspace_id=scope.workspace_id,
                            project_id=scope.project_id,
                            idempotency_key=idempotency_key,
                            request_fingerprint=request_fingerprint,
                            status="running",
                            attempts=1,
                            result_payload=None,
                            failure_reason=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    return SmartStoryboardClaim(True, "running", None)
                return self._claim_existing(
                    row,
                    request_fingerprint=request_fingerprint,
                    now=now,
                    stale_after=stale_after,
                )
        except IntegrityError:
            with self._session_factory() as session, session.begin():
                row = session.get(
                    SmartStoryboardRequestRow,
                    (scope.tenant_id, scope.workspace_id, scope.project_id, idempotency_key),
                    with_for_update=True,
                )
                if row is None:
                    raise
                return self._claim_existing(
                    row,
                    request_fingerprint=request_fingerprint,
                    now=now,
                    stale_after=stale_after,
                )

    def succeed(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        result_payload: dict[str, object],
        now: datetime,
    ) -> None:
        self._finish(
            scope,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status="succeeded",
            result_payload=result_payload,
            failure_reason=None,
            now=now,
        )

    def fail(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        failure_reason: str,
        now: datetime,
    ) -> None:
        self._finish(
            scope,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status="failed",
            result_payload=None,
            failure_reason=failure_reason[:1000],
            now=now,
        )

    @staticmethod
    def _claim_existing(
        row: SmartStoryboardRequestRow,
        *,
        request_fingerprint: str,
        now: datetime,
        stale_after: timedelta,
    ) -> SmartStoryboardClaim:
        if row.request_fingerprint != request_fingerprint:
            raise IdempotencyConflict("IDEMPOTENCY_KEY_REUSED")
        if row.status == "succeeded":
            return SmartStoryboardClaim(False, row.status, row.result_payload)
        updated_at = now_utc(row.updated_at)
        if row.status == "running" and updated_at + stale_after > now:
            return SmartStoryboardClaim(False, row.status, None)
        row.status = "running"
        row.attempts += 1
        row.failure_reason = None
        row.updated_at = now
        return SmartStoryboardClaim(True, row.status, None)

    def _finish(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        status: str,
        result_payload: dict[str, object] | None,
        failure_reason: str | None,
        now: datetime,
    ) -> None:
        with self._session_factory() as session, session.begin():
            row = session.get(
                SmartStoryboardRequestRow,
                (scope.tenant_id, scope.workspace_id, scope.project_id, idempotency_key),
                with_for_update=True,
            )
            if row is None or row.request_fingerprint != request_fingerprint:
                raise ContractViolation("SMART_STORYBOARD_REQUEST_STATE_MISSING")
            if row.status != "running":
                raise ContractViolation("SMART_STORYBOARD_REQUEST_NOT_RUNNING")
            row.status = status
            row.result_payload = result_payload
            row.failure_reason = failure_reason
            row.updated_at = now


class SqlAlchemyPromptTemplateRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def list(self, scope: StoryboardScope, *, include_archived: bool = False) -> tuple[PromptTemplate, ...]:
        predicates = list(self._scope(scope))
        if not include_archived:
            predicates.append(PromptTemplateRow.archived_at.is_(None))
        with self._session_factory() as session:
            rows = session.scalars(
                select(PromptTemplateRow).where(*predicates).order_by(
                    PromptTemplateRow.updated_at.desc(), PromptTemplateRow.prompt_id.desc()
                )
            ).all()
            return tuple(self._model(row) for row in rows)

    def get(self, scope: StoryboardScope, prompt_id: str) -> PromptTemplate | None:
        with self._session_factory() as session:
            row = session.scalar(select(PromptTemplateRow).where(*self._scope(scope), PromptTemplateRow.prompt_id == prompt_id))
            return None if row is None else self._model(row)

    def create(
        self,
        scope: StoryboardScope,
        *,
        name: str,
        media_type: str,
        template: str,
        negative_prompt: str,
        variables: tuple[str, ...],
        model_adapter_versions: tuple[str, ...],
        idempotency_key: str,
        audit: AuditEvent,
    ) -> tuple[PromptTemplate, bool]:
        now = audit.occurred_at
        prompt = PromptTemplate(
            prompt_id=f"prompt-{uuid4()}", tenant_id=scope.tenant_id, workspace_id=scope.workspace_id,
            project_id=scope.project_id, name=name, media_type=media_type, template=template,
            negative_prompt=negative_prompt, variables=variables, model_adapter_versions=model_adapter_versions,
            version=1, created_at=now, updated_at=now,
        )
        fingerprint = self._fingerprint({
            "name": name, "media_type": media_type, "template": template, "negative_prompt": negative_prompt,
            "variables": variables, "model_adapter_versions": model_adapter_versions,
        })
        return self._write(
            scope, prompt, expected_version=None, idempotency_key=idempotency_key,
            fingerprint=fingerprint, audit=audit,
        )

    def update(
        self,
        scope: StoryboardScope,
        prompt: PromptTemplate,
        *,
        expected_version: int,
        idempotency_key: str,
        audit: AuditEvent,
    ) -> tuple[PromptTemplate, bool]:
        if prompt.version != expected_version + 1:
            raise ContractViolation("INVALID_PROMPT_VERSION_TRANSITION")
        fingerprint = self._fingerprint({
            "prompt_id": prompt.prompt_id, "expected_version": expected_version, "name": prompt.name,
            "media_type": prompt.media_type, "template": prompt.template,
            "negative_prompt": prompt.negative_prompt, "variables": prompt.variables,
            "model_adapter_versions": prompt.model_adapter_versions,
            "archived": prompt.archived_at is not None,
        })
        return self._write(
            scope, prompt, expected_version=expected_version, idempotency_key=idempotency_key,
            fingerprint=fingerprint, audit=audit,
        )

    def _write(
        self,
        scope: StoryboardScope,
        prompt: PromptTemplate,
        *,
        expected_version: int | None,
        idempotency_key: str,
        fingerprint: str,
        audit: AuditEvent,
    ) -> tuple[PromptTemplate, bool]:
        if not idempotency_key.strip():
            raise ContractViolation("IDEMPOTENCY_KEY_REQUIRED")
        payload = prompt.to_dict()
        operation = "prompt.create" if expected_version is None else "prompt.update"
        try:
            with self._session_factory() as session, session.begin():
                receipt = session.scalar(select(IdempotencyRow).where(
                    IdempotencyRow.tenant_id == scope.tenant_id, IdempotencyRow.workspace_id == scope.workspace_id,
                    IdempotencyRow.project_id == scope.project_id, IdempotencyRow.operation_kind == operation,
                    IdempotencyRow.idempotency_key == idempotency_key,
                ))
                if receipt is not None:
                    if receipt.fingerprint != fingerprint:
                        raise IdempotencyConflict("IDEMPOTENCY_KEY_REUSED")
                    replay = self.get(scope, str(receipt.result_payload["id"]))
                    if replay is None:
                        raise ContractViolation("PROMPT_REPLAY_STATE_MISSING")
                    return replay, True
                if expected_version is None:
                    session.add(self._row(prompt))
                else:
                    result = session.execute(update(PromptTemplateRow).where(
                        *self._scope(scope), PromptTemplateRow.prompt_id == prompt.prompt_id,
                        PromptTemplateRow.version == expected_version,
                    ).values(
                        name=prompt.name, media_type=prompt.media_type, template=prompt.template,
                        negative_prompt=prompt.negative_prompt, variables=list(prompt.variables),
                        model_adapter_versions=list(prompt.model_adapter_versions), version=prompt.version,
                        updated_at=prompt.updated_at, archived_at=prompt.archived_at,
                    ))
                    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                        raise VersionConflict("VERSION_CONFLICT")
                session.add(IdempotencyRow(
                    tenant_id=scope.tenant_id, workspace_id=scope.workspace_id, project_id=scope.project_id,
                    operation_kind=operation, idempotency_key=idempotency_key, fingerprint=fingerprint,
                    result_payload={"id": prompt.prompt_id}, created_at=now_utc(audit.occurred_at),
                ))
                session.add(AuditRow(
                    tenant_id=audit.tenant_id, workspace_id=audit.workspace_id, project_id=audit.project_id,
                    request_id=audit.request_id, actor_id=audit.actor_id, action=audit.action,
                    object_id=prompt.prompt_id, result="succeeded", occurred_at=audit.occurred_at,
                    before_payload=audit.before, after_payload=payload, error_code=None,
                ))
        except IntegrityError as error:
            replay = self._replay(scope, operation, idempotency_key, fingerprint)
            if replay is not None:
                return replay, True
            raise VersionConflict("VERSION_CONFLICT") from error
        return prompt, False

    def _replay(
        self, scope: StoryboardScope, operation: str, idempotency_key: str, fingerprint: str
    ) -> PromptTemplate | None:
        with self._session_factory() as session:
            receipt = session.scalar(select(IdempotencyRow).where(
                IdempotencyRow.tenant_id == scope.tenant_id,
                IdempotencyRow.workspace_id == scope.workspace_id,
                IdempotencyRow.project_id == scope.project_id,
                IdempotencyRow.operation_kind == operation,
                IdempotencyRow.idempotency_key == idempotency_key,
            ))
            if receipt is None:
                return None
            if receipt.fingerprint != fingerprint:
                raise IdempotencyConflict("IDEMPOTENCY_KEY_REUSED")
            prompt_id = receipt.result_payload.get("id")
            if not isinstance(prompt_id, str):
                raise ContractViolation("PROMPT_REPLAY_STATE_INVALID")
            row = session.scalar(select(PromptTemplateRow).where(
                *self._scope(scope), PromptTemplateRow.prompt_id == prompt_id,
            ))
            if row is None:
                raise ContractViolation("PROMPT_REPLAY_STATE_MISSING")
            return self._model(row)

    @staticmethod
    def _scope(scope: StoryboardScope) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[bool]]:
        return (
            PromptTemplateRow.tenant_id == scope.tenant_id,
            PromptTemplateRow.workspace_id == scope.workspace_id,
            PromptTemplateRow.project_id == scope.project_id,
        )

    @staticmethod
    def _fingerprint(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _row(value: PromptTemplate) -> PromptTemplateRow:
        return PromptTemplateRow(
            tenant_id=value.tenant_id, workspace_id=value.workspace_id, project_id=value.project_id,
            prompt_id=value.prompt_id, name=value.name, media_type=value.media_type, template=value.template,
            negative_prompt=value.negative_prompt, variables=list(value.variables),
            model_adapter_versions=list(value.model_adapter_versions), version=value.version,
            created_at=value.created_at, updated_at=value.updated_at, archived_at=value.archived_at,
        )

    @staticmethod
    def _model(row: PromptTemplateRow) -> PromptTemplate:
        return PromptTemplate(
            prompt_id=row.prompt_id, tenant_id=row.tenant_id, workspace_id=row.workspace_id,
            project_id=row.project_id, name=row.name, media_type=row.media_type, template=row.template,
            negative_prompt=row.negative_prompt, variables=tuple(row.variables),
            model_adapter_versions=tuple(row.model_adapter_versions), version=row.version,
            created_at=now_utc(row.created_at), updated_at=now_utc(row.updated_at),
            archived_at=now_utc(row.archived_at) if row.archived_at else None,
        )


def now_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
