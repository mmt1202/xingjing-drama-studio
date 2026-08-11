"""CommercialRepository 的同步 SQLAlchemy 生产适配器。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from contextvars import Token
from datetime import UTC, datetime
from types import TracebackType
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.xingjing_commercial import AuditEvent, CommercialOrder, IdempotencyConflict, VersionConflict
from server.xingjing_commercial.models import CommandRecord
from server.xingjing_commercial.ports import CommercialUnitOfWork

from .models import CommercialAuditRow, CommercialCommandRow, CommercialOrderRow
from .transaction_context import bind_commercial_session, reset_commercial_session

type SessionFactory = Callable[[], Session]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class _SqlAlchemyCommercialUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_order(self, owner_workspace_id: str, order_id: str) -> CommercialOrder | None:
        row = self._session.scalar(
            select(CommercialOrderRow).where(
                CommercialOrderRow.owner_workspace_id == owner_workspace_id,
                CommercialOrderRow.id == order_id,
            )
        )
        return None if row is None else CommercialOrder.from_dict(row.aggregate)

    def put_order(self, order: CommercialOrder, expected_version: int | None) -> None:
        if expected_version is None:
            if order.version != 1:
                raise VersionConflict()
            row = CommercialOrderRow(
                owner_workspace_id=order.owner_workspace_id,
                id=order.id,
                status=order.status.value,
                version=order.version,
                aggregate=order.to_dict(),
                created_at=order.created_at,
                updated_at=order.updated_at,
            )
            try:
                with self._session.begin_nested():
                    self._session.add(row)
                    self._session.flush()
            except IntegrityError as exc:
                existing_id = self._session.scalar(
                    select(CommercialOrderRow.id).where(
                        CommercialOrderRow.owner_workspace_id == order.owner_workspace_id,
                        CommercialOrderRow.id == order.id,
                    )
                )
                if existing_id is not None:
                    raise VersionConflict() from exc
                raise
            return

        if order.version != expected_version + 1:
            raise VersionConflict()
        result = cast(
            CursorResult[tuple[object, ...]],
            self._session.execute(
                update(CommercialOrderRow)
                .where(
                    CommercialOrderRow.owner_workspace_id == order.owner_workspace_id,
                    CommercialOrderRow.id == order.id,
                    CommercialOrderRow.version == expected_version,
                )
                .values(
                    status=order.status.value,
                    version=order.version,
                    aggregate=order.to_dict(),
                    updated_at=order.updated_at,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            raise VersionConflict()

    def get_command(self, workspace_id: str, scope: str, idempotency_key: str) -> CommandRecord | None:
        row = self._session.scalar(
            select(CommercialCommandRow).where(
                CommercialCommandRow.workspace_id == workspace_id,
                CommercialCommandRow.scope == scope,
                CommercialCommandRow.idempotency_key == idempotency_key,
            )
        )
        if row is None:
            return None
        return CommandRecord(
            workspace_id=row.workspace_id,
            scope=row.scope,
            idempotency_key=row.idempotency_key,
            fingerprint=row.fingerprint,
            result_json=row.result_json,
            created_at=_as_utc(row.created_at),
        )

    def put_command(self, record: CommandRecord) -> None:
        row = CommercialCommandRow(
            workspace_id=record.workspace_id,
            scope=record.scope,
            idempotency_key=record.idempotency_key,
            fingerprint=record.fingerprint,
            result_json=record.result_json,
            created_at=record.created_at,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as exc:
            existing_key = self._session.scalar(
                select(CommercialCommandRow.idempotency_key).where(
                    CommercialCommandRow.workspace_id == record.workspace_id,
                    CommercialCommandRow.scope == record.scope,
                    CommercialCommandRow.idempotency_key == record.idempotency_key,
                )
            )
            if existing_key is not None:
                raise IdempotencyConflict() from exc
            raise

    def append_audit(self, event: AuditEvent) -> None:
        self._session.add(
            CommercialAuditRow(
                workspace_id=event.workspace_id,
                event_id=event.event_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                actor_id=event.actor_id,
                object_type=event.object_type,
                object_id=event.object_id,
                request_id=event.request_id,
                before=event.before,
                after=event.after,
                result=event.result,
            )
        )
        self._session.flush()

    def list_audit(self, workspace_id: str, object_id: str) -> tuple[AuditEvent, ...]:
        rows = self._session.scalars(
            select(CommercialAuditRow)
            .where(
                CommercialAuditRow.workspace_id == workspace_id,
                CommercialAuditRow.object_id == object_id,
            )
            .order_by(CommercialAuditRow.occurred_at, CommercialAuditRow.event_id)
        ).all()
        return tuple(
            AuditEvent(
                event_id=row.event_id,
                event_type=row.event_type,
                occurred_at=_as_utc(row.occurred_at),
                actor_id=row.actor_id,
                workspace_id=row.workspace_id,
                object_type=row.object_type,
                object_id=row.object_id,
                request_id=row.request_id,
                before=row.before,
                after=row.after,
                result=row.result,
            )
            for row in rows
        )


class _Atomic(AbstractContextManager[CommercialUnitOfWork]):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._context_token: Token[Session | None] | None = None

    def __enter__(self) -> CommercialUnitOfWork:
        session = self._session_factory()
        self._session = session
        try:
            session.begin()
            self._context_token = bind_commercial_session(session)
        except BaseException:
            session.close()
            self._session = None
            raise
        return _SqlAlchemyCommercialUnitOfWork(session)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        session = self._session
        if session is None:
            return False
        try:
            if exc_type is None:
                session.commit()
            else:
                session.rollback()
        except BaseException:
            session.rollback()
            raise
        finally:
            if self._context_token is not None:
                reset_commercial_session(self._context_token)
                self._context_token = None
            session.close()
            self._session = None
        return False


class SqlAlchemyCommercialRepository:
    """真实数据库事务实现；调用方负责提供生产 Engine 对应的 Session factory。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def atomic(self) -> AbstractContextManager[CommercialUnitOfWork]:
        return _Atomic(self._session_factory)
