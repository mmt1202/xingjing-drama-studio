from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from string import hexdigits
from typing import cast

from sqlalchemy import (
    JSON,
    BigInteger,
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from server.xingjing_storyboard.errors import (
    ContractViolation,
    IdempotencyConflict,
    StoryboardNotFound,
    VersionConflict,
)
from server.xingjing_storyboard.models import Storyboard
from server.xingjing_storyboard.ports import AuditEvent, CommitOutcome, StoryboardScope


class Base(DeclarativeBase):
    """独立 metadata；由主线迁移接入，生产运行时不隐式建表。"""


class StoryboardRow(Base):
    __tablename__ = "xingjing_storyboards"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_xj_storyboard_version_positive"),
        Index(
            "ix_xj_storyboard_scope_episode_id",
            "tenant_id",
            "workspace_id",
            "project_id",
            "episode_id",
            "storyboard_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    storyboard_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class IdempotencyRow(Base):
    __tablename__ = "xingjing_storyboard_idempotency"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    result_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRow(Base):
    __tablename__ = "xingjing_storyboard_audit"
    __table_args__ = (
        Index(
            "ix_xj_storyboard_audit_scope_object_time",
            "tenant_id",
            "workspace_id",
            "project_id",
            "object_id",
            "occurred_at",
            "audit_sequence",
        ),
        Index(
            "ix_xj_storyboard_audit_scope_request",
            "tenant_id",
            "workspace_id",
            "project_id",
            "request_id",
        ),
        Index(
            "ix_xj_storyboard_audit_scope_actor_time",
            "tenant_id",
            "workspace_id",
            "project_id",
            "actor_id",
            "occurred_at",
        ),
        Index(
            "ix_xj_storyboard_audit_scope_action_time",
            "tenant_id",
            "workspace_id",
            "project_id",
            "action",
            "occurred_at",
        ),
    )

    audit_sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))


class ImportUploadRow(Base):
    __tablename__ = "xingjing_storyboard_import_uploads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "project_id",
            "object_key",
            name="uq_xj_storyboard_upload_scope_object_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "project_id", "storyboard_id"],
            [
                "xingjing_storyboards.tenant_id",
                "xingjing_storyboards.workspace_id",
                "xingjing_storyboards.project_id",
                "xingjing_storyboards.storyboard_id",
            ],
            name="fk_xj_storyboard_upload_consumed_storyboard",
            ondelete="RESTRICT",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_xj_storyboard_upload_size_nonnegative"),
        CheckConstraint("length(sha256) = 64", name="ck_xj_storyboard_upload_sha256_length"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'consumed', 'failed')",
            name="ck_xj_storyboard_upload_status",
        ),
        Index(
            "ix_xj_storyboard_upload_scope_status_time",
            "tenant_id",
            "workspace_id",
            "project_id",
            "status",
            "created_at",
            "upload_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storyboard_id: Mapped[str | None] = mapped_column(String(128))


SessionFactory = Callable[[], Session]


class ImportUploadStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    CONSUMED = "consumed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ImportUploadMetadata:
    upload_id: str
    tenant_id: str
    workspace_id: str
    project_id: str
    object_key: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    source: str
    lifecycle: str
    status: ImportUploadStatus
    created_at: datetime
    completed_at: datetime | None = None
    consumed_at: datetime | None = None
    storyboard_id: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.upload_id,
            self.tenant_id,
            self.workspace_id,
            self.project_id,
            self.object_key,
            self.filename,
            self.media_type,
            self.source,
            self.lifecycle,
        )
        if any(not value.strip() for value in required):
            raise ContractViolation("UPLOAD_METADATA_REQUIRED")
        if self.size_bytes < 0:
            raise ContractViolation("INVALID_UPLOAD_SIZE")
        if len(self.sha256) != 64 or any(character not in hexdigits for character in self.sha256):
            raise ContractViolation("INVALID_UPLOAD_SHA256")
        self._require_aware(self.created_at)
        if self.completed_at is not None:
            self._require_aware(self.completed_at)
        if self.consumed_at is not None:
            self._require_aware(self.consumed_at)
        if self.status in {ImportUploadStatus.COMPLETED, ImportUploadStatus.CONSUMED} and self.completed_at is None:
            raise ContractViolation("UPLOAD_COMPLETION_TIME_REQUIRED")
        if self.status is ImportUploadStatus.CONSUMED and (
            self.consumed_at is None or self.storyboard_id is None or not self.storyboard_id.strip()
        ):
            raise ContractViolation("UPLOAD_CONSUMPTION_METADATA_REQUIRED")

    def to_dict(self) -> dict[str, object]:
        return {
            "upload_id": self.upload_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "object_key": self.object_key,
            "filename": self.filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "source": self.source,
            "lifecycle": self.lifecycle,
            "status": self.status.value,
            "created_at": self.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "completed_at": (
                None
                if self.completed_at is None
                else self.completed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            ),
            "consumed_at": (
                None
                if self.consumed_at is None
                else self.consumed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            ),
            "storyboard_id": self.storyboard_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ImportUploadMetadata:
        return cls(
            upload_id=_required_text(value, "upload_id"),
            tenant_id=_required_text(value, "tenant_id"),
            workspace_id=_required_text(value, "workspace_id"),
            project_id=_required_text(value, "project_id"),
            object_key=_required_text(value, "object_key"),
            filename=_required_text(value, "filename"),
            media_type=_required_text(value, "media_type"),
            size_bytes=_required_int(value, "size_bytes"),
            sha256=_required_text(value, "sha256"),
            source=_required_text(value, "source"),
            lifecycle=_required_text(value, "lifecycle"),
            status=ImportUploadStatus(_required_text(value, "status")),
            created_at=_required_datetime(value, "created_at"),
            completed_at=_optional_datetime(value, "completed_at"),
            consumed_at=_optional_datetime(value, "consumed_at"),
            storyboard_id=_optional_text(value, "storyboard_id"),
        )

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ContractViolation("TIMEZONE_REQUIRED")


class SqlAlchemyStoryboardRepository:
    """以单个数据库事务实现 StoryboardRepository 的生产适配器。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def commit(
        self,
        scope: StoryboardScope,
        storyboard: Storyboard,
        *,
        expected_version: int | None,
        idempotency_key: str,
        fingerprint: str,
        audit: AuditEvent,
    ) -> CommitOutcome:
        self._assert_scope(scope, storyboard)
        self._assert_audit_scope(scope, audit)
        self._validate_version_transition(storyboard, expected_version)
        self._validate_idempotency(idempotency_key, fingerprint)
        try:
            with self._session_factory() as session, session.begin():
                receipt = session.scalar(self._receipt_select(scope, "storyboard", idempotency_key))
                if receipt is not None:
                    self._assert_fingerprint(receipt, fingerprint)
                    return CommitOutcome(self._storyboard(receipt.result_payload), True)
                if expected_version is None:
                    current = session.scalar(self._storyboard_select(scope, storyboard.storyboard_id))
                    if current is not None:
                        raise VersionConflict("VERSION_CONFLICT", details={"current_version": current.version})
                    session.add(self._row(storyboard))
                else:
                    result = session.execute(
                        update(StoryboardRow)
                        .where(
                            *self._scope_predicates(scope),
                            StoryboardRow.storyboard_id == storyboard.storyboard_id,
                            StoryboardRow.version == expected_version,
                        )
                        .values(
                            episode_id=storyboard.episode_id,
                            version=storyboard.version,
                            payload=storyboard.to_dict(),
                        )
                    )
                    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                        receipt = session.scalar(self._receipt_select(scope, "storyboard", idempotency_key))
                        if receipt is not None:
                            self._assert_fingerprint(receipt, fingerprint)
                            return CommitOutcome(self._storyboard(receipt.result_payload), True)
                        current = session.scalar(self._storyboard_select(scope, storyboard.storyboard_id))
                        if current is None:
                            raise StoryboardNotFound("STORYBOARD_NOT_FOUND")
                        raise VersionConflict("VERSION_CONFLICT", details={"current_version": current.version})
                session.add(
                    IdempotencyRow(
                        tenant_id=scope.tenant_id,
                        workspace_id=scope.workspace_id,
                        project_id=scope.project_id,
                        operation_kind="storyboard",
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        result_payload=storyboard.to_dict(),
                        created_at=audit.occurred_at,
                    )
                )
                session.add(self._audit_row(audit))
        except IntegrityError as error:
            replayed = self.replay(scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
            if replayed is not None:
                return CommitOutcome(replayed, True)
            current = self.get(scope, storyboard.storyboard_id)
            if current is not None:
                raise VersionConflict("VERSION_CONFLICT", details={"current_version": current.version}) from error
            raise
        return CommitOutcome(storyboard, False)

    def replay(self, scope: StoryboardScope, *, idempotency_key: str, fingerprint: str) -> Storyboard | None:
        self._validate_idempotency(idempotency_key, fingerprint)
        with self._session_factory() as session:
            receipt = session.scalar(self._receipt_select(scope, "storyboard", idempotency_key))
            if receipt is None:
                return None
            self._assert_fingerprint(receipt, fingerprint)
            return self._storyboard(receipt.result_payload)

    def replay_export(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        fingerprint: str,
    ) -> dict[str, object] | None:
        self._validate_idempotency(idempotency_key, fingerprint)
        with self._session_factory() as session:
            receipt = session.scalar(self._receipt_select(scope, "export", idempotency_key))
            if receipt is None:
                return None
            self._assert_fingerprint(receipt, fingerprint)
            return self._mapping_payload(receipt.result_payload)

    def commit_export(
        self,
        scope: StoryboardScope,
        *,
        idempotency_key: str,
        fingerprint: str,
        result: dict[str, object],
        audit: AuditEvent,
    ) -> tuple[dict[str, object], bool]:
        self._assert_audit_scope(scope, audit)
        self._validate_idempotency(idempotency_key, fingerprint)
        stored_result = self._mapping_payload(result)
        try:
            with self._session_factory() as session, session.begin():
                receipt = session.scalar(self._receipt_select(scope, "export", idempotency_key))
                if receipt is not None:
                    self._assert_fingerprint(receipt, fingerprint)
                    return self._mapping_payload(receipt.result_payload), True
                session.add(
                    IdempotencyRow(
                        tenant_id=scope.tenant_id,
                        workspace_id=scope.workspace_id,
                        project_id=scope.project_id,
                        operation_kind="export",
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        result_payload=stored_result,
                        created_at=audit.occurred_at,
                    )
                )
                session.add(self._audit_row(audit))
        except IntegrityError:
            replayed = self.replay_export(scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
            if replayed is not None:
                return replayed, True
            raise
        return stored_result, False

    def get(self, scope: StoryboardScope, storyboard_id: str) -> Storyboard | None:
        with self._session_factory() as session:
            row = session.scalar(self._storyboard_select(scope, storyboard_id))
            return None if row is None else self._storyboard(row.payload)

    def list(self, scope: StoryboardScope) -> tuple[Storyboard, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(StoryboardRow)
                .where(*self._scope_predicates(scope))
                .order_by(StoryboardRow.episode_id, StoryboardRow.storyboard_id)
            ).all()
            return tuple(self._storyboard(row.payload) for row in rows)

    def append_audit(self, event: AuditEvent) -> None:
        with self._session_factory() as session, session.begin():
            session.add(self._audit_row(event))

    def list_audit(self, scope: StoryboardScope, *, storyboard_id: str | None = None) -> tuple[AuditEvent, ...]:
        return self.query_audit(scope, storyboard_id=storyboard_id)

    def query_audit(
        self,
        scope: StoryboardScope,
        *,
        storyboard_id: str | None = None,
        request_id: str | None = None,
        actor_id: str | None = None,
        action: str | None = None,
    ) -> tuple[AuditEvent, ...]:
        predicates = [
            AuditRow.tenant_id == scope.tenant_id,
            AuditRow.workspace_id == scope.workspace_id,
            AuditRow.project_id == scope.project_id,
        ]
        if storyboard_id is not None:
            predicates.append(AuditRow.object_id == storyboard_id)
        if request_id is not None:
            predicates.append(AuditRow.request_id == request_id)
        if actor_id is not None:
            predicates.append(AuditRow.actor_id == actor_id)
        if action is not None:
            predicates.append(AuditRow.action == action)
        with self._session_factory() as session:
            rows = session.scalars(
                select(AuditRow).where(*predicates).order_by(AuditRow.occurred_at, AuditRow.audit_sequence)
            ).all()
            return tuple(self._audit_event(row) for row in rows)

    @staticmethod
    def _assert_scope(scope: StoryboardScope, storyboard: Storyboard) -> None:
        if (storyboard.tenant_id, storyboard.workspace_id, storyboard.project_id) != (
            scope.tenant_id,
            scope.workspace_id,
            scope.project_id,
        ):
            raise ContractViolation("STORYBOARD_SCOPE_MISMATCH")

    @staticmethod
    def _assert_audit_scope(scope: StoryboardScope, audit: AuditEvent) -> None:
        if (audit.tenant_id, audit.workspace_id, audit.project_id) != (
            scope.tenant_id,
            scope.workspace_id,
            scope.project_id,
        ):
            raise ContractViolation("AUDIT_SCOPE_MISMATCH")

    @staticmethod
    def _validate_version_transition(storyboard: Storyboard, expected_version: int | None) -> None:
        required_version = 1 if expected_version is None else expected_version + 1
        if storyboard.version != required_version:
            raise ContractViolation("INVALID_VERSION_TRANSITION")

    @staticmethod
    def _validate_idempotency(idempotency_key: str, fingerprint: str) -> None:
        if not idempotency_key.strip():
            raise ContractViolation("IDEMPOTENCY_KEY_REQUIRED")
        if not fingerprint.strip():
            raise ContractViolation("IDEMPOTENCY_FINGERPRINT_REQUIRED")

    @staticmethod
    def _row(storyboard: Storyboard) -> StoryboardRow:
        return StoryboardRow(
            tenant_id=storyboard.tenant_id,
            workspace_id=storyboard.workspace_id,
            project_id=storyboard.project_id,
            storyboard_id=storyboard.storyboard_id,
            episode_id=storyboard.episode_id,
            version=storyboard.version,
            payload=storyboard.to_dict(),
        )

    @staticmethod
    def _storyboard(payload: object) -> Storyboard:
        if not isinstance(payload, Mapping):
            raise ContractViolation("INVALID_PERSISTED_STATE")
        return Storyboard.from_dict(cast(Mapping[str, object], payload))

    @staticmethod
    def _assert_fingerprint(receipt: IdempotencyRow, fingerprint: str) -> None:
        if receipt.fingerprint != fingerprint:
            raise IdempotencyConflict("IDEMPOTENCY_KEY_REUSED")

    @staticmethod
    def _audit_row(event: AuditEvent) -> AuditRow:
        return AuditRow(
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            project_id=event.project_id,
            request_id=event.request_id,
            actor_id=event.actor_id,
            action=event.action,
            object_id=event.object_id,
            result=event.result,
            occurred_at=event.occurred_at,
            before_payload=event.before,
            after_payload=event.after,
            error_code=event.error_code,
        )

    @staticmethod
    def _mapping_payload(value: object) -> dict[str, object]:
        try:
            copied = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError) as error:
            raise ContractViolation("NON_SERIALIZABLE_CONTRACT") from error
        if not isinstance(copied, dict):
            raise ContractViolation("INVALID_SERIALIZED_CONTRACT")
        return cast(dict[str, object], copied)

    @staticmethod
    def _audit_event(row: AuditRow) -> AuditEvent:
        occurred_at = row.occurred_at
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return AuditEvent(
            tenant_id=row.tenant_id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            request_id=row.request_id,
            actor_id=row.actor_id,
            action=row.action,
            object_id=row.object_id,
            result=row.result,
            occurred_at=occurred_at,
            before=row.before_payload,
            after=row.after_payload,
            error_code=row.error_code,
        )

    @staticmethod
    def _scope_predicates(
        scope: StoryboardScope,
    ) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[bool]]:
        return (
            StoryboardRow.tenant_id == scope.tenant_id,
            StoryboardRow.workspace_id == scope.workspace_id,
            StoryboardRow.project_id == scope.project_id,
        )

    @classmethod
    def _storyboard_select(cls, scope: StoryboardScope, storyboard_id: str):
        return select(StoryboardRow).where(
            *cls._scope_predicates(scope),
            StoryboardRow.storyboard_id == storyboard_id,
        )

    @staticmethod
    def _receipt_select(scope: StoryboardScope, operation_kind: str, idempotency_key: str):
        return select(IdempotencyRow).where(
            IdempotencyRow.tenant_id == scope.tenant_id,
            IdempotencyRow.workspace_id == scope.workspace_id,
            IdempotencyRow.project_id == scope.project_id,
            IdempotencyRow.operation_kind == operation_kind,
            IdempotencyRow.idempotency_key == idempotency_key,
        )


class SqlAlchemyStoryboardUploadRepository:
    """持久化对象存储引用和导入上传生命周期元数据。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create(
        self,
        scope: StoryboardScope,
        *,
        upload_id: str,
        object_key: str,
        filename: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        source: str,
        lifecycle: str,
        idempotency_key: str,
        fingerprint: str,
        created_at: datetime,
        audit: AuditEvent,
    ) -> tuple[ImportUploadMetadata, bool]:
        SqlAlchemyStoryboardRepository._assert_audit_scope(scope, audit)
        SqlAlchemyStoryboardRepository._validate_idempotency(idempotency_key, fingerprint)
        metadata = ImportUploadMetadata(
            upload_id=upload_id,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            object_key=object_key,
            filename=filename,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            source=source,
            lifecycle=lifecycle,
            status=ImportUploadStatus.PENDING,
            created_at=created_at,
        )
        try:
            with self._session_factory() as session, session.begin():
                receipt = session.scalar(
                    SqlAlchemyStoryboardRepository._receipt_select(scope, "upload.create", idempotency_key)
                )
                if receipt is not None:
                    SqlAlchemyStoryboardRepository._assert_fingerprint(receipt, fingerprint)
                    return self._metadata(receipt.result_payload), True
                session.add(self._row(metadata))
                session.add(
                    IdempotencyRow(
                        tenant_id=scope.tenant_id,
                        workspace_id=scope.workspace_id,
                        project_id=scope.project_id,
                        operation_kind="upload.create",
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        result_payload=metadata.to_dict(),
                        created_at=created_at,
                    )
                )
                session.add(SqlAlchemyStoryboardRepository._audit_row(audit))
        except IntegrityError as error:
            replayed = self.replay(
                scope,
                operation="create",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replayed is not None:
                return replayed, True
            existing = self.get(scope, upload_id)
            if existing is not None:
                raise ContractViolation("UPLOAD_ALREADY_EXISTS") from error
            raise
        return metadata, False

    def get(self, scope: StoryboardScope, upload_id: str) -> ImportUploadMetadata | None:
        with self._session_factory() as session:
            row = session.scalar(self._upload_select(scope, upload_id))
            return None if row is None else self._from_row(row)

    def replay(
        self,
        scope: StoryboardScope,
        *,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> ImportUploadMetadata | None:
        operation_kind = self._operation_kind(operation)
        SqlAlchemyStoryboardRepository._validate_idempotency(idempotency_key, fingerprint)
        with self._session_factory() as session:
            receipt = session.scalar(
                SqlAlchemyStoryboardRepository._receipt_select(scope, operation_kind, idempotency_key)
            )
            if receipt is None:
                return None
            SqlAlchemyStoryboardRepository._assert_fingerprint(receipt, fingerprint)
            return self._metadata(receipt.result_payload)

    def complete(
        self,
        scope: StoryboardScope,
        upload_id: str,
        *,
        size_bytes: int,
        sha256: str,
        idempotency_key: str,
        fingerprint: str,
        completed_at: datetime,
        audit: AuditEvent,
    ) -> tuple[ImportUploadMetadata, bool]:
        SqlAlchemyStoryboardRepository._assert_audit_scope(scope, audit)
        SqlAlchemyStoryboardRepository._validate_idempotency(idempotency_key, fingerprint)
        operation_kind = self._operation_kind("complete")
        with self._session_factory() as session, session.begin():
            receipt = session.scalar(
                SqlAlchemyStoryboardRepository._receipt_select(scope, operation_kind, idempotency_key)
            )
            if receipt is not None:
                SqlAlchemyStoryboardRepository._assert_fingerprint(receipt, fingerprint)
                return self._metadata(receipt.result_payload), True
            row = session.scalar(self._upload_select(scope, upload_id))
            if row is None:
                raise ContractViolation("UPLOAD_NOT_FOUND")
            current = self._from_row(row)
            if current.size_bytes != size_bytes or current.sha256 != sha256:
                raise ContractViolation("UPLOAD_METADATA_MISMATCH")
            if current.status is not ImportUploadStatus.PENDING:
                raise ContractViolation("UPLOAD_STATE_CONFLICT")
            completed = replace(
                current,
                status=ImportUploadStatus.COMPLETED,
                completed_at=completed_at,
            )
            result = session.execute(
                update(ImportUploadRow)
                .where(
                    ImportUploadRow.tenant_id == scope.tenant_id,
                    ImportUploadRow.workspace_id == scope.workspace_id,
                    ImportUploadRow.project_id == scope.project_id,
                    ImportUploadRow.upload_id == upload_id,
                    ImportUploadRow.status == ImportUploadStatus.PENDING.value,
                )
                .values(
                    status=ImportUploadStatus.COMPLETED.value,
                    completed_at=completed_at,
                )
            )
            if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                receipt = session.scalar(
                    SqlAlchemyStoryboardRepository._receipt_select(scope, operation_kind, idempotency_key)
                )
                if receipt is not None:
                    SqlAlchemyStoryboardRepository._assert_fingerprint(receipt, fingerprint)
                    return self._metadata(receipt.result_payload), True
                raise ContractViolation("UPLOAD_STATE_CONFLICT")
            session.add(
                IdempotencyRow(
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                    operation_kind=operation_kind,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    result_payload=completed.to_dict(),
                    created_at=completed_at,
                )
            )
            session.add(SqlAlchemyStoryboardRepository._audit_row(audit))
        return completed, False

    def consume(
        self,
        scope: StoryboardScope,
        upload_id: str,
        *,
        storyboard_id: str,
        idempotency_key: str,
        fingerprint: str,
        consumed_at: datetime,
        audit: AuditEvent,
    ) -> tuple[ImportUploadMetadata, bool]:
        SqlAlchemyStoryboardRepository._assert_audit_scope(scope, audit)
        SqlAlchemyStoryboardRepository._validate_idempotency(idempotency_key, fingerprint)
        operation_kind = self._operation_kind("consume")
        with self._session_factory() as session, session.begin():
            receipt = session.scalar(
                SqlAlchemyStoryboardRepository._receipt_select(scope, operation_kind, idempotency_key)
            )
            if receipt is not None:
                SqlAlchemyStoryboardRepository._assert_fingerprint(receipt, fingerprint)
                return self._metadata(receipt.result_payload), True
            row = session.scalar(self._upload_select(scope, upload_id))
            if row is None:
                raise ContractViolation("UPLOAD_NOT_FOUND")
            current = self._from_row(row)
            if current.status is not ImportUploadStatus.COMPLETED:
                raise ContractViolation("UPLOAD_STATE_CONFLICT")
            storyboard = session.scalar(SqlAlchemyStoryboardRepository._storyboard_select(scope, storyboard_id))
            if storyboard is None:
                raise ContractViolation("STORYBOARD_NOT_FOUND")
            consumed = replace(
                current,
                status=ImportUploadStatus.CONSUMED,
                consumed_at=consumed_at,
                storyboard_id=storyboard_id,
            )
            result = session.execute(
                update(ImportUploadRow)
                .where(
                    ImportUploadRow.tenant_id == scope.tenant_id,
                    ImportUploadRow.workspace_id == scope.workspace_id,
                    ImportUploadRow.project_id == scope.project_id,
                    ImportUploadRow.upload_id == upload_id,
                    ImportUploadRow.status == ImportUploadStatus.COMPLETED.value,
                )
                .values(
                    status=ImportUploadStatus.CONSUMED.value,
                    consumed_at=consumed_at,
                    storyboard_id=storyboard_id,
                )
            )
            if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                receipt = session.scalar(
                    SqlAlchemyStoryboardRepository._receipt_select(scope, operation_kind, idempotency_key)
                )
                if receipt is not None:
                    SqlAlchemyStoryboardRepository._assert_fingerprint(receipt, fingerprint)
                    return self._metadata(receipt.result_payload), True
                raise ContractViolation("UPLOAD_STATE_CONFLICT")
            session.add(
                IdempotencyRow(
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                    operation_kind=operation_kind,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    result_payload=consumed.to_dict(),
                    created_at=consumed_at,
                )
            )
            session.add(SqlAlchemyStoryboardRepository._audit_row(audit))
        return consumed, False

    @staticmethod
    def _row(metadata: ImportUploadMetadata) -> ImportUploadRow:
        return ImportUploadRow(
            tenant_id=metadata.tenant_id,
            workspace_id=metadata.workspace_id,
            project_id=metadata.project_id,
            upload_id=metadata.upload_id,
            object_key=metadata.object_key,
            filename=metadata.filename,
            media_type=metadata.media_type,
            size_bytes=metadata.size_bytes,
            sha256=metadata.sha256,
            source=metadata.source,
            lifecycle=metadata.lifecycle,
            status=metadata.status.value,
            created_at=metadata.created_at,
            completed_at=metadata.completed_at,
            consumed_at=metadata.consumed_at,
            storyboard_id=metadata.storyboard_id,
        )

    @staticmethod
    def _from_row(row: ImportUploadRow) -> ImportUploadMetadata:
        return ImportUploadMetadata(
            upload_id=row.upload_id,
            tenant_id=row.tenant_id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            object_key=row.object_key,
            filename=row.filename,
            media_type=row.media_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            source=row.source,
            lifecycle=row.lifecycle,
            status=ImportUploadStatus(row.status),
            created_at=_as_utc(row.created_at),
            completed_at=_as_optional_utc(row.completed_at),
            consumed_at=_as_optional_utc(row.consumed_at),
            storyboard_id=row.storyboard_id,
        )

    @staticmethod
    def _metadata(payload: object) -> ImportUploadMetadata:
        if not isinstance(payload, Mapping):
            raise ContractViolation("INVALID_PERSISTED_STATE")
        return ImportUploadMetadata.from_dict(cast(Mapping[str, object], payload))

    @staticmethod
    def _upload_select(scope: StoryboardScope, upload_id: str):
        return select(ImportUploadRow).where(
            ImportUploadRow.tenant_id == scope.tenant_id,
            ImportUploadRow.workspace_id == scope.workspace_id,
            ImportUploadRow.project_id == scope.project_id,
            ImportUploadRow.upload_id == upload_id,
        )

    @staticmethod
    def _operation_kind(operation: str) -> str:
        if operation not in {"create", "complete", "consume"}:
            raise ContractViolation("INVALID_UPLOAD_OPERATION")
        return f"upload.{operation}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)


def _required_text(value: Mapping[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise ContractViolation("INVALID_PERSISTED_STATE")
    return raw


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ContractViolation("INVALID_PERSISTED_STATE")
    return raw


def _required_int(value: Mapping[str, object], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ContractViolation("INVALID_PERSISTED_STATE")
    return raw


def _required_datetime(value: Mapping[str, object], key: str) -> datetime:
    raw = _required_text(value, key)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractViolation("INVALID_PERSISTED_STATE") from error


def _optional_datetime(value: Mapping[str, object], key: str) -> datetime | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ContractViolation("INVALID_PERSISTED_STATE")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractViolation("INVALID_PERSISTED_STATE") from error
