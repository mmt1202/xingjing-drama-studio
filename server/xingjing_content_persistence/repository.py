from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, TypeVar, cast

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from server.xingjing_content.models import (
    CharacterInsight,
    ContentAnalysis,
    DirectorProfile,
    RelationshipInsight,
    SceneHeading,
    ScriptDocument,
    ScriptParagraph,
    ScriptScene,
    ScriptVersion,
    SourceDocument,
    SourceMapping,
    StoryBeat,
    ValidationIssue,
)

T = TypeVar("T")
SessionFactory = Callable[[], Session]


class ContentPersistenceBase(DeclarativeBase):
    """M03 独立 metadata；生产建表须由主线 Alembic 迁移显式接线。"""


class ScriptRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_scripts"
    __table_args__ = (
        Index("ix_xj_content_script_scope_project", "workspace_id", "project_id", "title", "script_id"),
        CheckConstraint("revision >= 1", name="ck_xj_content_script_revision"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    script_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    source_content: Mapped[str] = mapped_column(String, nullable=False)
    locked_version_number: Mapped[int | None] = mapped_column(Integer)
    director_profile: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ScriptVersionRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_script_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "script_id"),
            ("xingjing_content_scripts.workspace_id", "xingjing_content_scripts.script_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("number >= 1", name="ck_xj_content_version_number"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    script_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_content: Mapped[str] = mapped_column(String, nullable=False)
    change_summary: Mapped[str] = mapped_column(String(2048), nullable=False)
    validation_errors: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    analysis: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ContentAiRequestRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_ai_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_xj_content_ai_request_status",
        ),
        CheckConstraint("attempts >= 1", name="ck_xj_content_ai_request_attempts"),
        Index(
            "ix_xj_content_ai_request_scope_updated",
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
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSON)
    failure_reason: Mapped[str | None] = mapped_column(String(1000))
    raw_response: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class ContentAiClaim:
    claimed: bool
    status: str
    result_payload: dict[str, object] | None


class SqlAlchemyContentAiRequestRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def claim(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        action: str,
        idempotency_key: str,
        request_fingerprint: str,
        now: datetime,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> ContentAiClaim:
        if not idempotency_key.strip():
            raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
        if len(request_fingerprint) != 64:
            raise ValueError("CONTENT_AI_FINGERPRINT_INVALID")
        key = (tenant_id, workspace_id, project_id, idempotency_key)
        try:
            with self._session_factory() as session, session.begin():
                row = session.get(ContentAiRequestRow, key, with_for_update=True)
                if row is None:
                    session.add(
                        ContentAiRequestRow(
                            tenant_id=tenant_id,
                            workspace_id=workspace_id,
                            project_id=project_id,
                            idempotency_key=idempotency_key,
                            action=action,
                            request_fingerprint=request_fingerprint,
                            status="running",
                            attempts=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    return ContentAiClaim(True, "running", None)
                return self._claim_existing(row, action, request_fingerprint, now, stale_after)
        except IntegrityError:
            with self._session_factory() as session, session.begin():
                row = session.get(ContentAiRequestRow, key, with_for_update=True)
                if row is None:
                    raise
                return self._claim_existing(row, action, request_fingerprint, now, stale_after)

    @staticmethod
    def _claim_existing(
        row: ContentAiRequestRow,
        action: str,
        fingerprint: str,
        now: datetime,
        stale_after: timedelta,
    ) -> ContentAiClaim:
        if row.action != action or row.request_fingerprint != fingerprint:
            raise ValueError("IDEMPOTENCY_KEY_REUSED")
        if row.status == "succeeded":
            return ContentAiClaim(False, row.status, row.result_payload)
        updated_at = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=UTC)
        if row.status == "running" and updated_at + stale_after > now:
            return ContentAiClaim(False, row.status, None)
        row.status = "running"
        row.attempts += 1
        row.failure_reason = None
        row.updated_at = now
        return ContentAiClaim(True, "running", None)

    def succeed(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        payload: dict[str, object],
        now: datetime,
        raw_response: str | None = None,
        evidence: dict[str, object] | None = None,
    ) -> None:
        self._finish(
            tenant_id,
            workspace_id,
            project_id,
            idempotency_key,
            request_fingerprint,
            "succeeded",
            payload,
            None,
            raw_response,
            evidence,
            now,
        )

    def fail(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        reason: str,
        now: datetime,
    ) -> None:
        self._finish(
            tenant_id,
            workspace_id,
            project_id,
            idempotency_key,
            request_fingerprint,
            "failed",
            None,
            reason[:1000],
            None,
            None,
            now,
        )

    def _finish(
        self,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        idempotency_key: str,
        fingerprint: str,
        status: str,
        payload: dict[str, object] | None,
        reason: str | None,
        raw_response: str | None,
        evidence: dict[str, object] | None,
        now: datetime,
    ) -> None:
        with self._session_factory() as session, session.begin():
            row = session.get(
                ContentAiRequestRow,
                (tenant_id, workspace_id, project_id, idempotency_key),
                with_for_update=True,
            )
            if row is None or row.request_fingerprint != fingerprint or row.status != "running":
                raise ValueError("CONTENT_AI_REQUEST_STATE_INVALID")
            row.status = status
            row.result_payload = payload
            row.failure_reason = reason
            row.raw_response = raw_response
            row.evidence = evidence
            row.updated_at = now


class ScriptSceneRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_script_scenes"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "script_id", "version_number"),
            (
                "xingjing_content_script_versions.workspace_id",
                "xingjing_content_script_versions.script_id",
                "xingjing_content_script_versions.number",
            ),
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    script_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    heading: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ScriptParagraphRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_script_paragraphs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "script_id", "version_number", "scene_sequence"),
            (
                "xingjing_content_script_scenes.workspace_id",
                "xingjing_content_script_scenes.script_id",
                "xingjing_content_script_scenes.version_number",
                "xingjing_content_script_scenes.sequence",
            ),
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    script_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    scene_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    paragraph_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    source_text: Mapped[str] = mapped_column(String, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(512))


class SourceMappingRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_source_mappings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "script_id", "version_number"),
            (
                "xingjing_content_script_versions.workspace_id",
                "xingjing_content_script_versions.script_id",
                "xingjing_content_script_versions.number",
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint("source_start >= 0", name="ck_xj_content_mapping_start"),
        CheckConstraint("source_end >= source_start", name="ck_xj_content_mapping_range"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    script_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)


class IdempotencyRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_idempotency"

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    script_id: Mapped[str] = mapped_column(String(128), nullable=False)


class ContentAuditRow(ContentPersistenceBase):
    __tablename__ = "xingjing_content_audit_events"
    __table_args__ = (
        Index("ix_xj_content_audit_scope_object_time", "workspace_id", "script_id", "occurred_at", "event_id"),
        Index("ix_xj_content_audit_scope_request", "workspace_id", "request_id"),
        Index("ix_xj_content_audit_scope_actor", "workspace_id", "actor_id", "occurred_at"),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    script_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


@dataclass(frozen=True, slots=True)
class AuditContext:
    request_id: str
    actor_id: str
    occurred_at: datetime
    action: str = "content.script.persisted"

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.actor_id.strip():
            raise ValueError("request_id and actor_id are required")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")


class SqlAlchemyContentRepository:
    """兼容 M03 同步仓储端口的真实 SQLAlchemy 实现。

    领域模型当前只携带 workspace_id；租户身份必须在上层请求上下文和未来公共
    模型扩展中补齐，不能由此适配器臆造或绕过。
    """

    def __init__(self, session_factory: SessionFactory, *, audit_context: AuditContext) -> None:
        self._session_factory = session_factory
        self._audit_context = audit_context

    def transact(
        self,
        workspace_id: str,
        idempotency_key: str,
        fingerprint: str,
        operation: Callable[[dict[str, ScriptDocument]], tuple[dict[str, ScriptDocument], T]],
    ) -> T:
        if not workspace_id.strip() or not idempotency_key.strip() or not fingerprint.strip():
            raise ValueError("workspace_id, idempotency_key and fingerprint are required")
        try:
            with self._session_factory() as session, session.begin():
                receipt = session.scalar(self._receipt_select(workspace_id, idempotency_key))
                if receipt is not None:
                    self._assert_fingerprint(receipt, fingerprint)
                    return cast(T, self._require_document(session, workspace_id, receipt.script_id))
                documents = self._read_all_in_session(session, workspace_id)
                prior_documents = dict(documents)
                updated, result = operation(documents)
                document = self._result_document(updated, result, workspace_id)
                before = prior_documents.get(document.script_id)
                self._persist_document(session, document, before)
                session.add(
                    IdempotencyRow(
                        workspace_id=workspace_id,
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        script_id=document.script_id,
                    )
                )
                session.add(self._audit_row(document, before, idempotency_key))
                return result
        except IntegrityError as error:
            replayed = self._replay(workspace_id, idempotency_key, fingerprint)
            if replayed is not None:
                return cast(T, replayed)
            raise ValueError("VERSION_CONFLICT") from error

    def read_all(self, workspace_id: str) -> dict[str, ScriptDocument]:
        with self._session_factory() as session:
            return self._read_all_in_session(session, workspace_id)

    def list_audit(
        self,
        workspace_id: str,
        *,
        project_id: str,
        script_id: str,
        request_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 500:
            raise ValueError("INVALID_AUDIT_LIMIT")
        predicates = [
            ContentAuditRow.workspace_id == workspace_id,
            ContentAuditRow.project_id == project_id,
            ContentAuditRow.script_id == script_id,
        ]
        if request_id:
            predicates.append(ContentAuditRow.request_id == request_id)
        if actor_id:
            predicates.append(ContentAuditRow.actor_id == actor_id)
        with self._session_factory() as session:
            rows = session.scalars(
                select(ContentAuditRow)
                .where(*predicates)
                .order_by(ContentAuditRow.occurred_at.desc(), ContentAuditRow.event_id.desc())
                .limit(limit)
            ).all()
            return tuple(
                {
                    "eventId": row.event_id,
                    "requestId": row.request_id,
                    "actorId": row.actor_id,
                    "action": row.action,
                    "result": row.result,
                    "occurredAt": row.occurred_at.isoformat(),
                    "before": row.before_payload,
                    "after": row.after_payload,
                }
                for row in rows
            )

    def _replay(self, workspace_id: str, idempotency_key: str, fingerprint: str) -> ScriptDocument | None:
        with self._session_factory() as session:
            receipt = session.scalar(self._receipt_select(workspace_id, idempotency_key))
            if receipt is None:
                return None
            self._assert_fingerprint(receipt, fingerprint)
            return self._require_document(session, workspace_id, receipt.script_id)

    @staticmethod
    def _receipt_select(workspace_id: str, idempotency_key: str):
        return select(IdempotencyRow).where(
            IdempotencyRow.workspace_id == workspace_id,
            IdempotencyRow.idempotency_key == idempotency_key,
        )

    @staticmethod
    def _assert_fingerprint(receipt: IdempotencyRow, fingerprint: str) -> None:
        if receipt.fingerprint != fingerprint:
            raise ValueError("IDEMPOTENCY_KEY_REUSED")

    def _read_all_in_session(self, session: Session, workspace_id: str) -> dict[str, ScriptDocument]:
        rows = session.scalars(
            select(ScriptRow)
            .where(ScriptRow.workspace_id == workspace_id)
            .order_by(ScriptRow.title, ScriptRow.script_id)
        ).all()
        return {row.script_id: self._load_document(session, row) for row in rows}

    def _require_document(self, session: Session, workspace_id: str, script_id: str) -> ScriptDocument:
        row = session.scalar(
            select(ScriptRow).where(ScriptRow.workspace_id == workspace_id, ScriptRow.script_id == script_id)
        )
        if row is None:
            raise ValueError("IDEMPOTENCY_RECEIPT_WITHOUT_SCRIPT")
        return self._load_document(session, row)

    @staticmethod
    def _result_document(documents: dict[str, ScriptDocument], result: object, workspace_id: str) -> ScriptDocument:
        script_id = getattr(result, "script_id", None)
        if not isinstance(script_id, str) or not isinstance(result, ScriptDocument):
            raise ValueError("CONTENT_OPERATION_MUST_RETURN_SCRIPT")
        document = documents.get(script_id)
        if document is None or document != result or document.workspace_id != workspace_id:
            raise ValueError("CONTENT_OPERATION_RETURNED_INVALID_SCOPE")
        return document

    def _persist_document(self, session: Session, document: ScriptDocument, before: ScriptDocument | None) -> None:
        stored_row = session.scalar(
            select(ScriptRow).where(
                ScriptRow.workspace_id == document.workspace_id,
                ScriptRow.script_id == document.script_id,
            )
        )
        if stored_row is None:
            if before is not None or document.revision != 1 or [version.number for version in document.versions] != [1]:
                raise ValueError("VERSION_CONFLICT")
            session.add(self._script_row(document))
            self._append_versions(session, document, document.versions)
            return

        stored = self._load_document(session, stored_row)
        if before != stored or document.revision != stored.revision + 1:
            raise ValueError("VERSION_CONFLICT")
        if self._immutable_document_fields(document) != self._immutable_document_fields(stored):
            raise ValueError("IMMUTABLE_HISTORY_CONFLICT")
        if document.versions[: len(stored.versions)] != stored.versions:
            raise ValueError("IMMUTABLE_HISTORY_CONFLICT")
        appended = document.versions[len(stored.versions) :]
        expected_numbers = list(range(len(stored.versions) + 1, len(document.versions) + 1))
        if [version.number for version in appended] != expected_numbers:
            raise ValueError("IMMUTABLE_HISTORY_CONFLICT")
        if appended:
            self._append_versions(session, document, appended)
        result = session.execute(
            update(ScriptRow)
            .where(
                ScriptRow.workspace_id == document.workspace_id,
                ScriptRow.script_id == document.script_id,
                ScriptRow.revision == stored.revision,
            )
            .values(
                revision=document.revision,
                locked_version_number=document.locked_version_number,
                director_profile=cast(dict[str, object], asdict(document.director_profile)),
            )
        )
        if cast(CursorResult[object], result).rowcount != 1:
            raise ValueError("VERSION_CONFLICT")

    @staticmethod
    def _immutable_document_fields(document: ScriptDocument) -> tuple[object, ...]:
        return (
            document.workspace_id,
            document.project_id,
            document.script_id,
            document.title,
            document.source_document,
        )

    @staticmethod
    def _script_row(document: ScriptDocument) -> ScriptRow:
        return ScriptRow(
            workspace_id=document.workspace_id,
            script_id=document.script_id,
            project_id=document.project_id,
            title=document.title,
            revision=document.revision,
            source_filename=document.source_document.filename,
            source_media_type=document.source_document.media_type,
            source_content=document.source_document.content,
            locked_version_number=document.locked_version_number,
            director_profile=cast(dict[str, object], asdict(document.director_profile)),
        )

    @staticmethod
    def _append_versions(session: Session, document: ScriptDocument, versions: tuple[ScriptVersion, ...]) -> None:
        for version in versions:
            session.add(
                ScriptVersionRow(
                    workspace_id=document.workspace_id,
                    script_id=document.script_id,
                    number=version.number,
                    source_content=version.source_content,
                    change_summary=version.change_summary,
                    validation_errors=[cast(dict[str, object], asdict(issue)) for issue in version.validation_errors],
                    analysis=cast(dict[str, object], asdict(version.analysis)),
                )
            )
            for scene_sequence, scene in enumerate(version.scenes):
                session.add(
                    ScriptSceneRow(
                        workspace_id=document.workspace_id,
                        script_id=document.script_id,
                        version_number=version.number,
                        sequence=scene_sequence,
                        scene_id=scene.scene_id,
                        heading=cast(dict[str, object], asdict(scene.heading)),
                    )
                )
                for paragraph_sequence, paragraph in enumerate(scene.paragraphs):
                    session.add(
                        ScriptParagraphRow(
                            workspace_id=document.workspace_id,
                            script_id=document.script_id,
                            version_number=version.number,
                            scene_sequence=scene_sequence,
                            sequence=paragraph_sequence,
                            paragraph_id=paragraph.paragraph_id,
                            kind=paragraph.kind,
                            text=paragraph.text,
                            source_text=paragraph.source_text,
                            speaker=paragraph.speaker,
                        )
                    )
            for mapping in version.source_mappings:
                session.add(
                    SourceMappingRow(
                        workspace_id=document.workspace_id,
                        script_id=document.script_id,
                        version_number=version.number,
                        target_id=mapping.target_id,
                        source_start=mapping.source_start,
                        source_end=mapping.source_end,
                    )
                )

    def _load_document(self, session: Session, row: ScriptRow) -> ScriptDocument:
        version_rows = session.scalars(
            select(ScriptVersionRow)
            .where(ScriptVersionRow.workspace_id == row.workspace_id, ScriptVersionRow.script_id == row.script_id)
            .order_by(ScriptVersionRow.number)
        ).all()
        versions = tuple(
            self._load_version(session, row.workspace_id, row.script_id, version) for version in version_rows
        )
        return ScriptDocument(
            script_id=row.script_id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            title=row.title,
            revision=row.revision,
            source_document=SourceDocument(row.source_filename, row.source_media_type, row.source_content),
            versions=versions,
            locked_version_number=row.locked_version_number,
            director_profile=self._profile(row.director_profile),
        )

    def _load_version(
        self, session: Session, workspace_id: str, script_id: str, row: ScriptVersionRow
    ) -> ScriptVersion:
        scenes = session.scalars(
            select(ScriptSceneRow)
            .where(
                ScriptSceneRow.workspace_id == workspace_id,
                ScriptSceneRow.script_id == script_id,
                ScriptSceneRow.version_number == row.number,
            )
            .order_by(ScriptSceneRow.sequence)
        ).all()
        mappings = session.scalars(
            select(SourceMappingRow)
            .where(
                SourceMappingRow.workspace_id == workspace_id,
                SourceMappingRow.script_id == script_id,
                SourceMappingRow.version_number == row.number,
            )
            .order_by(SourceMappingRow.target_id)
        ).all()
        return ScriptVersion(
            number=row.number,
            source_content=row.source_content,
            scenes=tuple(self._scene(session, scene) for scene in scenes),
            source_mappings=tuple(
                SourceMapping(mapping.target_id, mapping.source_start, mapping.source_end) for mapping in mappings
            ),
            validation_errors=tuple(self._issue(issue) for issue in row.validation_errors),
            change_summary=row.change_summary,
            analysis=self._analysis(row.analysis),
        )

    def _scene(self, session: Session, row: ScriptSceneRow) -> ScriptScene:
        paragraphs = session.scalars(
            select(ScriptParagraphRow)
            .where(
                ScriptParagraphRow.workspace_id == row.workspace_id,
                ScriptParagraphRow.script_id == row.script_id,
                ScriptParagraphRow.version_number == row.version_number,
                ScriptParagraphRow.scene_sequence == row.sequence,
            )
            .order_by(ScriptParagraphRow.sequence)
        ).all()
        heading = cast(Mapping[str, object], row.heading)
        return ScriptScene(
            scene_id=row.scene_id,
            heading=SceneHeading(
                location=cast(str, heading["location"]),
                time_of_day=cast(str | None, heading.get("time_of_day")),
                setting=cast(str | None, heading.get("setting")),
            ),
            paragraphs=tuple(self._paragraph(item) for item in paragraphs),
        )

    @staticmethod
    def _paragraph(row: ScriptParagraphRow) -> ScriptParagraph:
        if row.kind not in {"action", "dialogue", "narration"}:
            raise ValueError("INVALID_PERSISTED_PARAGRAPH_KIND")
        return ScriptParagraph(
            row.paragraph_id,
            cast(Literal["action", "dialogue", "narration"], row.kind),
            row.text,
            row.source_text,
            row.speaker,
        )

    @staticmethod
    def _issue(payload: dict[str, object]) -> ValidationIssue:
        severity = cast(str, payload.get("severity", "error"))
        if severity not in {"error", "warning"}:
            raise ValueError("INVALID_PERSISTED_VALIDATION_SEVERITY")
        return ValidationIssue(
            code=cast(str, payload["code"]),
            path=cast(str, payload["path"]),
            message=cast(str, payload["message"]),
            severity=cast(Literal["error", "warning"], severity),
        )

    @staticmethod
    def _profile(payload: dict[str, object]) -> DirectorProfile:
        return DirectorProfile(
            audience=cast(str | None, payload.get("audience")),
            pacing=cast(str | None, payload.get("pacing")),
            visual_style=cast(str | None, payload.get("visual_style")),
            camera_language=cast(str | None, payload.get("camera_language")),
            production_constraints=tuple(
                cast(str, item) for item in cast(list[object], payload["production_constraints"])
            ),
        )

    @staticmethod
    def _analysis(payload: dict[str, object]) -> ContentAnalysis:
        return ContentAnalysis(
            word_count=cast(int, payload.get("word_count", 0)),
            character_count=cast(int, payload.get("character_count", 0)),
            scene_count=cast(int, payload.get("scene_count", 0)),
            dialogue_count=cast(int, payload.get("dialogue_count", 0)),
            estimated_duration_ms=cast(int, payload.get("estimated_duration_ms", 0)),
            estimated_episode_count=cast(int, payload.get("estimated_episode_count", 1)),
            sensitive_terms=tuple(cast(list[str], payload.get("sensitive_terms", []))),
            characters=tuple(
                CharacterInsight(
                    name=cast(str, item["name"]),
                    appearances=cast(int, item["appearances"]),
                    description=cast(str, item.get("description", "")),
                )
                for item in cast(list[dict[str, object]], payload.get("characters", []))
            ),
            locations=tuple(cast(list[str], payload.get("locations", []))),
            props=tuple(cast(list[str], payload.get("props", []))),
            relationships=tuple(
                RelationshipInsight(
                    source=cast(str, item["source"]),
                    target=cast(str, item["target"]),
                    relation=cast(str, item["relation"]),
                )
                for item in cast(list[dict[str, object]], payload.get("relationships", []))
            ),
            story_beats=tuple(
                StoryBeat(
                    label=cast(str, item["label"]),
                    summary=cast(str, item["summary"]),
                    scene_ids=tuple(cast(list[str], item.get("scene_ids", []))),
                )
                for item in cast(list[dict[str, object]], payload.get("story_beats", []))
            ),
            episode_suggestions=tuple(cast(list[str], payload.get("episode_suggestions", []))),
        )

    def _audit_row(
        self, document: ScriptDocument, before: ScriptDocument | None, idempotency_key: str
    ) -> ContentAuditRow:
        context = self._audit_context
        if before is None:
            action = "content.script.imported"
        elif before.locked_version_number != document.locked_version_number:
            action = "content.script.frozen"
        elif before.director_profile != document.director_profile:
            action = "content.director_profile.updated"
        elif len(before.versions) < len(document.versions):
            summary = document.current_version.change_summary
            action = "content.script.ai_analyzed" if summary == "AI 结构化解析" else "content.script.revised"
        else:
            action = context.action
        return ContentAuditRow(
            workspace_id=document.workspace_id,
            event_id=f"{context.request_id}:{idempotency_key}",
            project_id=document.project_id,
            script_id=document.script_id,
            request_id=context.request_id,
            actor_id=context.actor_id,
            action=action,
            result="success",
            occurred_at=context.occurred_at.astimezone(UTC),
            before_payload={} if before is None else before.to_dict(),
            after_payload=document.to_dict(),
        )
