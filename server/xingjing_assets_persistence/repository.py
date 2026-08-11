from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import (
    JSON,
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
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from server.xingjing_assets.contracts import (
    Asset,
    AssetKind,
    AssetReference,
    AssetSource,
    AssetVersion,
    FrozenScriptSnapshotRef,
    RightsEvidence,
)
from server.xingjing_assets.errors import VersionConflict


class Base(DeclarativeBase):
    """独立 metadata；运行环境必须通过主线 Alembic 迁移建表。"""


class AssetRow(Base):
    __tablename__ = "xingjing_asset_records"
    __table_args__ = (
        Index("ix_xj_asset_scope_project", "tenant_id", "workspace_id", "owner_project_id", "asset_id"),
        CheckConstraint("revision >= 1", name="ck_xj_asset_revision_positive"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    frozen_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    current_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class AssetVersionRow(Base):
    __tablename__ = "xingjing_asset_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "asset_id"],
            [
                "xingjing_asset_records.tenant_id",
                "xingjing_asset_records.workspace_id",
                "xingjing_asset_records.asset_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "asset_id", "sequence", name="uq_xj_asset_version_sequence"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    change_note: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    derived_from_version_id: Mapped[str | None] = mapped_column(String(128))


class RightsRow(Base):
    __tablename__ = "xingjing_asset_rights_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "asset_id"],
            [
                "xingjing_asset_records.tenant_id",
                "xingjing_asset_records.workspace_id",
                "xingjing_asset_records.asset_id",
            ],
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    rights_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ReferenceRow(Base):
    __tablename__ = "xingjing_asset_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "asset_id"],
            [
                "xingjing_asset_records.tenant_id",
                "xingjing_asset_records.workspace_id",
                "xingjing_asset_records.asset_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "asset_id", "project_id", name="uq_xj_asset_reference_project"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reference_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class IdempotencyRow(Base):
    __tablename__ = "xingjing_asset_idempotency"
    __table_args__ = (Index("ix_xj_asset_idempotency_asset", "tenant_id", "workspace_id", "asset_id"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRow(Base):
    __tablename__ = "xingjing_asset_audit_events"
    __table_args__ = (
        Index(
            "ix_xj_asset_audit_scope_object_time", "tenant_id", "workspace_id", "asset_id", "occurred_at", "event_id"
        ),
        Index("ix_xj_asset_audit_scope_request", "tenant_id", "workspace_id", "request_id"),
        Index("ix_xj_asset_audit_scope_actor", "tenant_id", "workspace_id", "actor_id", "occurred_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class AssetScope:
    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.workspace_id.strip():
            raise ValueError("tenant_id and workspace_id are required")


@dataclass(frozen=True, slots=True)
class AssetAuditEvent:
    request_id: str
    actor_id: str
    action: str
    asset_id: str
    result: str
    occurred_at: datetime
    before_payload: dict[str, object]
    after_payload: dict[str, object]

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")


class SqlAlchemyAssetRepository:
    """资产聚合根的真实数据库仓储；所有读取和写入都必须携带租户作用域。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def commit(
        self,
        scope: AssetScope,
        asset: Asset,
        *,
        expected_revision: int | None,
        idempotency_key: str,
        fingerprint: str,
        audit: AssetAuditEvent,
    ) -> tuple[Asset, bool]:
        self._assert_scope(scope, asset, audit)
        if not idempotency_key or not fingerprint:
            raise ValueError("idempotency_key and fingerprint are required")
        try:
            with self._session_factory() as session, session.begin():
                receipt = session.scalar(self._receipt_select(scope, idempotency_key))
                if receipt is not None:
                    self._assert_fingerprint(receipt, fingerprint)
                    return self._asset(cast(Mapping[str, object], receipt.result_payload)), True
                current = session.scalar(self._asset_select(scope, asset.asset_id))
                self._validate_transition(current, asset, expected_revision)
                if current is None:
                    session.add(self._asset_row(scope, asset))
                    self._append_children(session, scope, asset)
                else:
                    self._update_asset(session, scope, asset, expected_revision)
                    self._append_new_children(session, scope, current, asset)
                session.add(
                    IdempotencyRow(
                        tenant_id=scope.tenant_id,
                        workspace_id=scope.workspace_id,
                        operation_kind="asset.commit",
                        idempotency_key=idempotency_key,
                        fingerprint=fingerprint,
                        asset_id=asset.asset_id,
                        result_payload=asset.to_dict(),
                        created_at=audit.occurred_at,
                    )
                )
                session.add(self._audit_row(scope, audit, idempotency_key))
        except IntegrityError as error:
            replayed = self.replay(scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
            if replayed is not None:
                return replayed, True
            raise VersionConflict("VERSION_CONFLICT") from error
        return asset, False

    def replay(self, scope: AssetScope, *, idempotency_key: str, fingerprint: str) -> Asset | None:
        with self._session_factory() as session:
            receipt = session.scalar(self._receipt_select(scope, idempotency_key))
            if receipt is None:
                return None
            self._assert_fingerprint(receipt, fingerprint)
            return self._asset(cast(Mapping[str, object], receipt.result_payload))

    def get(self, scope: AssetScope, asset_id: str) -> Asset | None:
        with self._session_factory() as session:
            row = session.scalar(self._asset_select(scope, asset_id))
            return None if row is None else self._load_asset(session, scope, row)

    def list_for_project(
        self,
        scope: AssetScope,
        project_id: str,
        *,
        search: str | None = None,
        kind: AssetKind | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Asset, ...]:
        """Return assets owned by, or explicitly referenced from, one project.

        The project predicate is deliberately evaluated in the database.  A
        caller cannot list a workspace's other project assets merely by
        supplying a project ID in the URL.
        """
        if limit < 1 or limit > 201 or offset < 0:
            raise ValueError("invalid asset page")
        with self._session_factory() as session:
            references = select(ReferenceRow.asset_id).where(
                ReferenceRow.tenant_id == scope.tenant_id,
                ReferenceRow.workspace_id == scope.workspace_id,
                ReferenceRow.project_id == project_id,
            )
            query = select(AssetRow).where(
                AssetRow.tenant_id == scope.tenant_id,
                AssetRow.workspace_id == scope.workspace_id,
                (AssetRow.owner_project_id == project_id) | AssetRow.asset_id.in_(references),
            )
            if search:
                query = query.where(AssetRow.name.ilike(f"%{search.strip()}%"))
            if kind is not None:
                query = query.where(AssetRow.kind == kind.value)
            rows = session.scalars(query.order_by(AssetRow.asset_id).offset(offset).limit(limit)).all()
            return tuple(self._load_asset(session, scope, row) for row in rows)

    def list_for_workspace(
        self,
        scope: AssetScope,
        *,
        search: str | None = None,
        kind: AssetKind | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Asset, ...]:
        """List all assets the current trusted workspace is entitled to see."""
        if limit < 1 or limit > 201 or offset < 0:
            raise ValueError("invalid asset page")
        with self._session_factory() as session:
            query = select(AssetRow).where(
                AssetRow.tenant_id == scope.tenant_id,
                AssetRow.workspace_id == scope.workspace_id,
            )
            if search:
                query = query.where(AssetRow.name.ilike(f"%{search.strip()}%"))
            if kind is not None:
                query = query.where(AssetRow.kind == kind.value)
            rows = session.scalars(
                query.order_by(AssetRow.owner_project_id, AssetRow.asset_id).offset(offset).limit(limit)
            ).all()
            return tuple(self._load_asset(session, scope, row) for row in rows)

    def list_audit(
        self,
        scope: AssetScope,
        *,
        asset_id: str | None = None,
        request_id: str | None = None,
        actor_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AssetAuditEvent, ...]:
        if limit < 1 or limit > 200 or offset < 0:
            raise ValueError("invalid asset audit page")
        with self._session_factory() as session:
            query = select(AuditRow).where(
                AuditRow.tenant_id == scope.tenant_id, AuditRow.workspace_id == scope.workspace_id
            )
            if asset_id is not None:
                query = query.where(AuditRow.asset_id == asset_id)
            if request_id is not None:
                query = query.where(AuditRow.request_id == request_id)
            if actor_id is not None:
                query = query.where(AuditRow.actor_id == actor_id)
            if action is not None:
                query = query.where(AuditRow.action == action)
            rows = session.scalars(
                query.order_by(AuditRow.occurred_at.desc(), AuditRow.event_id.desc()).offset(offset).limit(limit)
            ).all()
            return tuple(self._audit(row) for row in rows)

    @staticmethod
    def _assert_scope(scope: AssetScope, asset: Asset, audit: AssetAuditEvent) -> None:
        if asset.workspace_id != scope.workspace_id or audit.asset_id != asset.asset_id:
            raise ValueError("asset and audit must belong to the supplied workspace and asset")

    @staticmethod
    def _assert_fingerprint(receipt: IdempotencyRow, fingerprint: str) -> None:
        if receipt.fingerprint != fingerprint:
            raise VersionConflict("IDEMPOTENCY_CONFLICT")

    @staticmethod
    def _asset_select(scope: AssetScope, asset_id: str):
        return select(AssetRow).where(
            AssetRow.tenant_id == scope.tenant_id,
            AssetRow.workspace_id == scope.workspace_id,
            AssetRow.asset_id == asset_id,
        )

    @staticmethod
    def _receipt_select(scope: AssetScope, idempotency_key: str):
        return select(IdempotencyRow).where(
            IdempotencyRow.tenant_id == scope.tenant_id,
            IdempotencyRow.workspace_id == scope.workspace_id,
            IdempotencyRow.operation_kind == "asset.commit",
            IdempotencyRow.idempotency_key == idempotency_key,
        )

    @staticmethod
    def _validate_transition(current: AssetRow | None, asset: Asset, expected_revision: int | None) -> None:
        if current is None:
            if expected_revision is not None or asset.revision != 1:
                raise VersionConflict("VERSION_CONFLICT")
            return
        if expected_revision != current.revision or asset.revision != current.revision + 1:
            raise VersionConflict("VERSION_CONFLICT")

    @staticmethod
    def _asset_row(scope: AssetScope, asset: Asset) -> AssetRow:
        return AssetRow(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            asset_id=asset.asset_id,
            owner_project_id=asset.owner_project_id,
            kind=asset.kind.value,
            name=asset.name,
            source=asset.source.to_dict(),
            frozen_snapshot=asset.frozen_snapshot.to_dict(),
            current_version_id=asset.current_version_id,
            revision=asset.revision,
        )

    @staticmethod
    def _append_children(session: Session, scope: AssetScope, asset: Asset) -> None:
        for version in asset.versions:
            session.add(SqlAlchemyAssetRepository._version_row(scope, asset.asset_id, version))
        for rights in asset.rights:
            session.add(
                RightsRow(
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    asset_id=asset.asset_id,
                    rights_id=rights.rights_id,
                    payload=rights.to_dict(),
                )
            )
        for reference in asset.references:
            session.add(SqlAlchemyAssetRepository._reference_row(scope, reference))

    def _append_new_children(self, session: Session, scope: AssetScope, current: AssetRow, asset: Asset) -> None:
        stored = self._load_asset(session, scope, current)
        if (
            asset.source != stored.source
            or asset.frozen_snapshot != stored.frozen_snapshot
            or asset.owner_project_id != stored.owner_project_id
            or asset.kind != stored.kind
            or asset.versions[: len(stored.versions)] != stored.versions
            or asset.rights[: len(stored.rights)] != stored.rights
            or asset.references[: len(stored.references)] != stored.references
        ):
            raise VersionConflict("IMMUTABLE_HISTORY_CONFLICT")
        self._append_children(
            session,
            scope,
            Asset(
                asset.asset_id,
                asset.workspace_id,
                asset.owner_project_id,
                asset.kind,
                asset.name,
                asset.source,
                asset.frozen_snapshot,
                asset.current_version_id,
                asset.revision,
                asset.versions[len(stored.versions) :],
                asset.rights[len(stored.rights) :],
                asset.references[len(stored.references) :],
            ),
        )

    @staticmethod
    def _update_asset(session: Session, scope: AssetScope, asset: Asset, expected_revision: int | None) -> None:
        result = cast(
            CursorResult[object],
            session.execute(
                update(AssetRow)
                .where(
                    AssetRow.tenant_id == scope.tenant_id,
                    AssetRow.workspace_id == scope.workspace_id,
                    AssetRow.asset_id == asset.asset_id,
                    AssetRow.revision == expected_revision,
                )
                .values(name=asset.name, current_version_id=asset.current_version_id, revision=asset.revision)
            ),
        )
        if result.rowcount != 1:
            raise VersionConflict("VERSION_CONFLICT")

    @staticmethod
    def _version_row(scope: AssetScope, asset_id: str, version: AssetVersion) -> AssetVersionRow:
        return AssetVersionRow(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            asset_id=asset_id,
            version_id=version.version_id,
            sequence=version.sequence,
            content=version.content,
            content_sha256=version.content_sha256,
            change_note=version.change_note,
            created_at=version.created_at,
            derived_from_version_id=version.derived_from_version_id,
        )

    @staticmethod
    def _reference_row(scope: AssetScope, reference: AssetReference) -> ReferenceRow:
        return ReferenceRow(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            asset_id=reference.asset_id,
            reference_id=reference.reference_id,
            project_id=reference.project_id,
            payload=reference.to_dict(),
        )

    @staticmethod
    def _audit_row(scope: AssetScope, audit: AssetAuditEvent, idempotency_key: str) -> AuditRow:
        return AuditRow(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            event_id=f"{audit.request_id}:{idempotency_key}",
            request_id=audit.request_id,
            actor_id=audit.actor_id,
            action=audit.action,
            asset_id=audit.asset_id,
            result=audit.result,
            occurred_at=audit.occurred_at,
            before_payload=audit.before_payload,
            after_payload=audit.after_payload,
        )

    def _load_asset(self, session: Session, scope: AssetScope, row: AssetRow) -> Asset:
        versions = session.scalars(
            select(AssetVersionRow)
            .where(
                AssetVersionRow.tenant_id == scope.tenant_id,
                AssetVersionRow.workspace_id == scope.workspace_id,
                AssetVersionRow.asset_id == row.asset_id,
            )
            .order_by(AssetVersionRow.sequence)
        ).all()
        rights = session.scalars(
            select(RightsRow)
            .where(
                RightsRow.tenant_id == scope.tenant_id,
                RightsRow.workspace_id == scope.workspace_id,
                RightsRow.asset_id == row.asset_id,
            )
            .order_by(RightsRow.rights_id)
        ).all()
        references = session.scalars(
            select(ReferenceRow)
            .where(
                ReferenceRow.tenant_id == scope.tenant_id,
                ReferenceRow.workspace_id == scope.workspace_id,
                ReferenceRow.asset_id == row.asset_id,
            )
            .order_by(ReferenceRow.reference_id)
        ).all()
        return Asset(
            row.asset_id,
            row.workspace_id,
            row.owner_project_id,
            AssetKind(row.kind),
            row.name,
            AssetSource.from_dict(cast(dict[str, object], row.source)),
            FrozenScriptSnapshotRef.from_dict(cast(dict[str, object], row.frozen_snapshot)),
            row.current_version_id,
            row.revision,
            tuple(
                AssetVersion(
                    item.version_id,
                    item.sequence,
                    item.content,
                    item.content_sha256,
                    item.change_note,
                    item.created_at,
                    item.derived_from_version_id,
                )
                for item in versions
            ),
            tuple(RightsEvidence.from_dict(cast(dict[str, object], item.payload)) for item in rights),
            tuple(AssetReference.from_dict(cast(dict[str, object], item.payload)) for item in references),
        )

    @staticmethod
    def _asset(payload: Mapping[str, object]) -> Asset:
        return Asset.from_dict(cast(dict[str, object], dict(payload)))

    @staticmethod
    def _audit(row: AuditRow) -> AssetAuditEvent:
        occurred_at = row.occurred_at
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return AssetAuditEvent(
            row.request_id,
            row.actor_id,
            row.action,
            row.asset_id,
            row.result,
            occurred_at.astimezone(UTC),
            cast(dict[str, object], row.before_payload),
            cast(dict[str, object], row.after_payload),
        )
