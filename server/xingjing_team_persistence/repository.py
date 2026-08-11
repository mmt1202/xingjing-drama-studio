"""PostgreSQL-backed implementation of the M10 ``TeamRepository`` port.

The metadata in this module is intentionally isolated.  Deployment must add it
to Alembic explicitly; this adapter never calls ``create_all`` or creates
tables as a side effect of construction.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy import (
    update as sa_update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from server.xingjing_identity_context.permissions import DEFAULT_ROLE_PERMISSIONS
from server.xingjing_team.errors import (
    IdempotencyConflict,
    InvitationStateError,
    NotFound,
    SeatLimitReached,
    VersionConflict,
)
from server.xingjing_team.models import (
    Actor,
    AuditEvent,
    EnterpriseProfile,
    EnterpriseProfileUpdate,
    Invitation,
    InvitationState,
    Role,
    RoleUpdate,
    SeatUsage,
)


class TeamPersistenceBase(DeclarativeBase):
    """独立 metadata，由后续正式 Alembic 迁移接入。"""


class WorkspaceRow(TeamPersistenceBase):
    __tablename__ = "xingjing_team_workspaces"
    __table_args__ = (CheckConstraint("seat_limit > 0", name="ck_xj_team_workspace_seat_limit"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    permission_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RoleRow(TeamPersistenceBase):
    __tablename__ = "xingjing_team_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["xingjing_team_workspaces.tenant_id", "xingjing_team_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_xj_team_role_workspace",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MemberRow(TeamPersistenceBase):
    __tablename__ = "xingjing_team_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "role_id"],
            ["xingjing_team_roles.tenant_id", "xingjing_team_roles.workspace_id", "xingjing_team_roles.role_id"],
            ondelete="RESTRICT",
            name="fk_xj_team_member_role",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "email", name="uq_xj_team_member_email"),
        Index("ix_xj_team_member_active", "tenant_id", "workspace_id", "active", "member_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role_id: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InvitationRow(TeamPersistenceBase):
    __tablename__ = "xingjing_team_invitations"
    __table_args__ = (
        CheckConstraint("state IN ('pending', 'accepted', 'revoked', 'expired')", name="ck_xj_team_invitation_state"),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "role_id"],
            ["xingjing_team_roles.tenant_id", "xingjing_team_roles.workspace_id", "xingjing_team_roles.role_id"],
            ondelete="RESTRICT",
            name="fk_xj_team_invitation_role",
        ),
        Index(
            "uq_xj_team_pending_invitation",
            "tenant_id",
            "workspace_id",
            "email",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_xj_team_invitation_scope", "tenant_id", "workspace_id", "invitation_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    invitation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invited_by: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_by: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class EnterpriseProfileRow(TeamPersistenceBase):
    __tablename__ = "xingjing_team_enterprise_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["xingjing_team_workspaces.tenant_id", "xingjing_team_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_xj_team_enterprise_workspace",
        ),
        CheckConstraint("data_retention_days IS NULL OR data_retention_days > 0", name="ck_xj_team_profile_retention"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    legal_name: Mapped[str | None] = mapped_column(String(512))
    invoice_title: Mapped[str | None] = mapped_column(String(512))
    data_retention_days: Mapped[int | None] = mapped_column(Integer)
    security_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    dedicated_deployment: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRow(TeamPersistenceBase):
    __tablename__ = "xingjing_team_idempotency"
    __table_args__ = (Index("ix_xj_team_idempotency_scope", "tenant_id", "workspace_id", "created_at"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    result_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRow(TeamPersistenceBase):
    __tablename__ = "xingjing_team_audit_events"
    __table_args__ = (
        Index("ix_xj_team_audit_scope_time", "tenant_id", "workspace_id", "occurred_at", "event_id"),
        Index("ix_xj_team_audit_scope_request", "tenant_id", "workspace_id", "request_id", "event_id"),
        Index("ix_xj_team_audit_scope_actor", "tenant_id", "workspace_id", "actor_id", "occurred_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class TeamPersistenceScope:
    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.workspace_id.strip():
            raise ValueError("tenant_id and workspace_id are required")


class SqlAlchemyTeamRepository:
    """同步 PostgreSQL ``TeamRepository`` 适配器，实例绑定且强制一个工作区范围。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        scope: TeamPersistenceScope,
        project_identity_membership: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._scope = scope
        self._project_identity_membership = project_identity_membership

    def create_workspace(self, workspace_id: str, *, seat_limit: int, owner: Actor) -> None:
        self._assert_workspace(workspace_id)
        if seat_limit < 1:
            raise ValueError("seat_limit must be positive")
        with self._session_factory() as session, session.begin():
            session.add(WorkspaceRow(tenant_id=self._scope.tenant_id, workspace_id=workspace_id, seat_limit=seat_limit))
            for role_id, name, permissions in self._default_roles():
                session.add(
                    RoleRow(
                        tenant_id=self._scope.tenant_id,
                        workspace_id=workspace_id,
                        role_id=role_id,
                        name=name,
                        permissions=sorted(permissions),
                    )
                )
            session.add(
                MemberRow(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=workspace_id,
                    member_id=owner.id,
                    email=owner.email.casefold(),
                    role_id="owner",
                )
            )

    def permission_version(self, workspace_id: str) -> int:
        self._assert_workspace(workspace_id)
        with self._session_factory() as session:
            row = session.scalar(self._workspace_query())
            if row is None:
                raise NotFound(workspace_id)
            return row.permission_version

    def permissions_for(self, workspace_id: str, member_id: str) -> frozenset[str] | None:
        self._assert_workspace(workspace_id)
        with self._session_factory() as session:
            row = session.scalar(
                select(RoleRow.permissions)
                .join(
                    MemberRow,
                    and_(
                        MemberRow.tenant_id == RoleRow.tenant_id,
                        MemberRow.workspace_id == RoleRow.workspace_id,
                        MemberRow.role_id == RoleRow.role_id,
                    ),
                )
                .where(*self._scope_clauses(RoleRow), MemberRow.member_id == member_id, MemberRow.active.is_(True))
            )
            return None if row is None else frozenset(row)

    def create_invitation(
        self,
        *,
        workspace_id: str,
        email: str,
        role_id: str,
        expires_at: datetime,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> Invitation:
        self._assert_workspace(workspace_id)
        normalized_email = email.casefold()
        fingerprint = self._fingerprint(
            {
                "actor_id": actor_id,
                "email": normalized_email,
                "expires_at": _utc(expires_at).isoformat(),
                "role_id": role_id,
            }
        )
        with self._session_factory() as session, session.begin():
            replay = self._replay(session, "invitation.create", idempotency_key, fingerprint)
            if replay is not None:
                return self._invitation_from_payload(replay)
            if (
                session.scalar(select(RoleRow.role_id).where(*self._scope_clauses(RoleRow), RoleRow.role_id == role_id))
                is None
            ):
                raise NotFound(role_id)
            invitation = Invitation(
                str(uuid4()),
                workspace_id,
                normalized_email,
                role_id,
                InvitationState.PENDING,
                _utc(expires_at),
                actor_id,
            )
            session.add(self._invitation_row(invitation))
            payload = self._invitation_payload(invitation)
            self._append_audit(
                session, actor_id, "invitation.create", "invitation", invitation.id, None, payload, request_id, now
            )
            self._remember(session, "invitation.create", idempotency_key, fingerprint, payload, now)
            return invitation

    def invitation(self, invitation_id: str, *, now: datetime) -> Invitation:
        with self._session_factory() as session, session.begin():
            row = session.scalar(self._invitation_query(invitation_id).with_for_update())
            if row is None:
                raise NotFound(invitation_id)
            invitation = self._invitation(row)
            if invitation.state is InvitationState.PENDING and invitation.expires_at <= _utc(now):
                before = self._invitation_payload(invitation)
                updated = session.execute(
                    sa_update(InvitationRow)
                    .where(
                        *self._scope_clauses(InvitationRow),
                        InvitationRow.invitation_id == invitation_id,
                        InvitationRow.state == "pending",
                        InvitationRow.version == invitation.version,
                    )
                    .values(state=InvitationState.EXPIRED.value, version=InvitationRow.version + 1)
                )
                if updated.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                    raise InvitationStateError("pending")
                invitation = Invitation(
                    invitation.id,
                    invitation.workspace_id,
                    invitation.email,
                    invitation.role_id,
                    InvitationState.EXPIRED,
                    invitation.expires_at,
                    invitation.invited_by,
                    invitation.accepted_by,
                    invitation.version + 1,
                )
                self._append_audit(
                    session,
                    "system",
                    "invitation.expire",
                    "invitation",
                    invitation.id,
                    before,
                    self._invitation_payload(invitation),
                    f"expire:{invitation.id}",
                    now,
                )
            return invitation

    def accept_invitation(
        self, *, invitation_id: str, actor: Actor, request_id: str, idempotency_key: str, now: datetime
    ) -> Invitation:
        with self._session_factory() as session, session.begin():
            row = session.scalar(self._invitation_query(invitation_id).with_for_update())
            if row is None:
                raise NotFound(invitation_id)
            invitation = self._invitation(row)
            fingerprint = self._fingerprint(
                {"actor_email": actor.email.casefold(), "actor_id": actor.id, "invitation_id": invitation_id}
            )
            replay = self._replay(session, "invitation.accept", idempotency_key, fingerprint)
            if replay is not None:
                accepted = self._invitation_from_payload(replay)
                if self._project_identity_membership:
                    self._upsert_identity_membership(
                        session,
                        actor=actor,
                        role_id=accepted.role_id,
                        now=_utc(now),
                    )
                return accepted
            now_utc = _utc(now)
            if invitation.state is not InvitationState.PENDING or invitation.expires_at <= now_utc:
                raise InvitationStateError(invitation.state.value)
            if invitation.email != actor.email.casefold():
                raise InvitationStateError("invitation recipient mismatch")
            workspace = session.scalar(self._workspace_query().with_for_update())
            if workspace is None:
                raise NotFound(self._scope.workspace_id)
            occupied = (
                session.scalar(
                    select(func.count())
                    .select_from(MemberRow)
                    .where(*self._scope_clauses(MemberRow), MemberRow.active.is_(True))
                )
                or 0
            )
            if occupied >= workspace.seat_limit:
                raise SeatLimitReached(self._scope.workspace_id)
            before = self._invitation_payload(invitation)
            if self._project_identity_membership:
                self._upsert_identity_membership(
                    session,
                    actor=actor,
                    role_id=invitation.role_id,
                    now=now_utc,
                )
            session.add(
                MemberRow(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=self._scope.workspace_id,
                    member_id=actor.id,
                    email=actor.email.casefold(),
                    role_id=invitation.role_id,
                )
            )
            changed = session.execute(
                sa_update(InvitationRow)
                .where(
                    *self._scope_clauses(InvitationRow),
                    InvitationRow.invitation_id == invitation_id,
                    InvitationRow.state == "pending",
                    InvitationRow.version == invitation.version,
                )
                .values(state="accepted", accepted_by=actor.id, version=InvitationRow.version + 1)
            )
            if changed.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                raise InvitationStateError("pending")
            accepted = Invitation(
                invitation.id,
                invitation.workspace_id,
                invitation.email,
                invitation.role_id,
                InvitationState.ACCEPTED,
                invitation.expires_at,
                invitation.invited_by,
                actor.id,
                invitation.version + 1,
            )
            payload = self._invitation_payload(accepted)
            self._append_audit(
                session, actor.id, "invitation.accept", "invitation", invitation.id, before, payload, request_id, now
            )
            self._remember(session, "invitation.accept", idempotency_key, fingerprint, payload, now)
            return accepted

    def _upsert_identity_membership(
        self,
        session: Session,
        *,
        actor: Actor,
        role_id: str,
        now: datetime,
    ) -> None:
        identity_role = role_id.strip().upper()
        if identity_role not in {"ADMIN", "MEMBER"}:
            identity_role = "MEMBER"
        result = session.execute(
            text(
                """
                INSERT INTO identity.workspace_members AS target
                  (workspace_id, user_id, role_key, status, joined_at,
                   created_at, created_by, updated_at, updated_by, version)
                SELECT CAST(:workspace_id AS uuid), identity_user.id, :role_key, 'ACTIVE', :now,
                       :now, identity_user.id, :now, identity_user.id, 0
                  FROM identity.users AS identity_user
                 WHERE identity_user.id = CAST(:user_id AS uuid) AND identity_user.status = 'ACTIVE'
                ON CONFLICT (workspace_id, user_id) DO UPDATE
                  SET role_key = CASE
                        WHEN target.role_key = 'OWNER' THEN target.role_key
                        ELSE EXCLUDED.role_key
                      END,
                      status = 'ACTIVE',
                      joined_at = CASE
                        WHEN target.status = 'ACTIVE' THEN target.joined_at
                        ELSE EXCLUDED.joined_at
                      END,
                      updated_at = EXCLUDED.updated_at,
                      updated_by = EXCLUDED.updated_by,
                      version = target.version + 1
                WHERE target.status <> 'ACTIVE'
                   OR (target.role_key <> 'OWNER'
                       AND target.role_key <> EXCLUDED.role_key)
                """
            ),
            {
                "workspace_id": self._scope.workspace_id,
                "user_id": actor.id,
                "role_key": identity_role,
                "now": now,
            },
        )
        if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
            membership_exists = session.scalar(
                text(
                    """
                    SELECT EXISTS(
                      SELECT 1 FROM identity.workspace_members
                       WHERE workspace_id = CAST(:workspace_id AS uuid)
                         AND user_id = CAST(:user_id AS uuid)
                         AND status = 'ACTIVE'
                    )
                    """
                ),
                {"workspace_id": self._scope.workspace_id, "user_id": actor.id},
            )
            if not membership_exists:
                raise NotFound(actor.id)

    def revoke_invitation(
        self, *, invitation_id: str, actor_id: str, request_id: str, idempotency_key: str, now: datetime
    ) -> Invitation:
        with self._session_factory() as session, session.begin():
            row = session.scalar(self._invitation_query(invitation_id).with_for_update())
            if row is None:
                raise NotFound(invitation_id)
            invitation = self._invitation(row)
            fingerprint = self._fingerprint({"actor_id": actor_id, "invitation_id": invitation_id})
            replay = self._replay(session, "invitation.revoke", idempotency_key, fingerprint)
            if replay is not None:
                return self._invitation_from_payload(replay)
            if invitation.state is not InvitationState.PENDING:
                raise InvitationStateError(invitation.state.value)
            before = self._invitation_payload(invitation)
            changed = session.execute(
                sa_update(InvitationRow)
                .where(
                    *self._scope_clauses(InvitationRow),
                    InvitationRow.invitation_id == invitation_id,
                    InvitationRow.state == "pending",
                    InvitationRow.version == invitation.version,
                )
                .values(state="revoked", version=InvitationRow.version + 1)
            )
            if changed.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                raise InvitationStateError("pending")
            revoked = Invitation(
                invitation.id,
                invitation.workspace_id,
                invitation.email,
                invitation.role_id,
                InvitationState.REVOKED,
                invitation.expires_at,
                invitation.invited_by,
                invitation.accepted_by,
                invitation.version + 1,
            )
            payload = self._invitation_payload(revoked)
            self._append_audit(
                session, actor_id, "invitation.revoke", "invitation", invitation.id, before, payload, request_id, now
            )
            self._remember(session, "invitation.revoke", idempotency_key, fingerprint, payload, now)
            return revoked

    def seat_usage(self, workspace_id: str) -> SeatUsage:
        self._assert_workspace(workspace_id)
        with self._session_factory() as session:
            workspace = session.scalar(self._workspace_query())
            if workspace is None:
                raise NotFound(workspace_id)
            occupied = (
                session.scalar(
                    select(func.count())
                    .select_from(MemberRow)
                    .where(*self._scope_clauses(MemberRow), MemberRow.active.is_(True))
                )
                or 0
            )
            return SeatUsage(workspace.seat_limit, int(occupied))

    def update_role(
        self,
        *,
        workspace_id: str,
        role_id: str,
        update: RoleUpdate,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> Role:
        self._assert_workspace(workspace_id)
        fingerprint = self._fingerprint(
            {
                "actor_id": actor_id,
                "expected_version": expected_version,
                "name": update.name,
                "permissions": sorted(update.permissions),
                "role_id": role_id,
            }
        )
        with self._session_factory() as session, session.begin():
            replay = self._replay(session, "role.update", idempotency_key, fingerprint)
            if replay is not None:
                return self._role_from_payload(replay)
            row = session.scalar(
                select(RoleRow).where(*self._scope_clauses(RoleRow), RoleRow.role_id == role_id).with_for_update()
            )
            if row is None:
                if expected_version != 0:
                    raise VersionConflict(role_id)
                role = Role(role_id, workspace_id, update.name, update.permissions, 1)
                session.add(
                    RoleRow(
                        tenant_id=self._scope.tenant_id,
                        workspace_id=workspace_id,
                        role_id=role_id,
                        name=update.name,
                        permissions=sorted(update.permissions),
                    )
                )
                before: dict[str, object] | None = None
            else:
                if row.version != expected_version:
                    raise VersionConflict(role_id)
                before = self._role_payload(self._role(row))
                changed = session.execute(
                    sa_update(RoleRow)
                    .where(
                        *self._scope_clauses(RoleRow), RoleRow.role_id == role_id, RoleRow.version == expected_version
                    )
                    .values(name=update.name, permissions=sorted(update.permissions), version=RoleRow.version + 1)
                )
                if changed.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                    raise VersionConflict(role_id)
                role = Role(role_id, workspace_id, update.name, update.permissions, expected_version + 1)
            bumped = session.execute(
                sa_update(WorkspaceRow)
                .where(*self._scope_clauses(WorkspaceRow))
                .values(permission_version=WorkspaceRow.permission_version + 1)
            )
            if bumped.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                raise NotFound(workspace_id)
            payload = self._role_payload(role)
            self._append_audit(session, actor_id, "role.update", "role", role_id, before, payload, request_id, now)
            self._remember(session, "role.update", idempotency_key, fingerprint, payload, now)
            return role

    def add_member(self, workspace_id: str, member_id: str, email: str, role_id: str) -> None:
        self._assert_workspace(workspace_id)
        with self._session_factory() as session, session.begin():
            if (
                session.scalar(select(RoleRow.role_id).where(*self._scope_clauses(RoleRow), RoleRow.role_id == role_id))
                is None
            ):
                raise NotFound(role_id)
            session.add(
                MemberRow(
                    tenant_id=self._scope.tenant_id,
                    workspace_id=workspace_id,
                    member_id=member_id,
                    email=email.casefold(),
                    role_id=role_id,
                )
            )

    def update_enterprise_profile(
        self,
        *,
        workspace_id: str,
        update: EnterpriseProfileUpdate,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> EnterpriseProfile:
        self._assert_workspace(workspace_id)
        if update.data_retention_days is not None and update.data_retention_days < 1:
            raise ValueError("data_retention_days must be positive")
        fingerprint = self._fingerprint(
            {
                "actor_id": actor_id,
                "data_retention_days": update.data_retention_days,
                "dedicated_deployment": update.dedicated_deployment,
                "expected_version": expected_version,
                "invoice_title": update.invoice_title,
                "legal_name": update.legal_name,
                "security_policy": update.security_policy,
            }
        )
        with self._session_factory() as session, session.begin():
            replay = self._replay(session, "enterprise.update", idempotency_key, fingerprint)
            if replay is not None:
                return self._profile_from_payload(replay)
            row = session.scalar(
                select(EnterpriseProfileRow).where(*self._scope_clauses(EnterpriseProfileRow)).with_for_update()
            )
            current_version = 0 if row is None else row.version
            if current_version != expected_version:
                raise VersionConflict(workspace_id)
            before = None if row is None else self._profile_payload(self._profile(row))
            profile = EnterpriseProfile(
                workspace_id,
                update.legal_name,
                update.invoice_title,
                update.data_retention_days,
                dict(update.security_policy),
                dict(update.dedicated_deployment),
                current_version + 1,
                _utc(now),
            )
            if row is None:
                session.add(
                    EnterpriseProfileRow(
                        tenant_id=self._scope.tenant_id,
                        workspace_id=workspace_id,
                        legal_name=profile.legal_name,
                        invoice_title=profile.invoice_title,
                        data_retention_days=profile.data_retention_days,
                        security_policy=profile.security_policy,
                        dedicated_deployment=profile.dedicated_deployment,
                        version=profile.version,
                        updated_at=profile.updated_at,
                    )
                )
            else:
                changed = session.execute(
                    sa_update(EnterpriseProfileRow)
                    .where(*self._scope_clauses(EnterpriseProfileRow), EnterpriseProfileRow.version == expected_version)
                    .values(
                        legal_name=profile.legal_name,
                        invoice_title=profile.invoice_title,
                        data_retention_days=profile.data_retention_days,
                        security_policy=profile.security_policy,
                        dedicated_deployment=profile.dedicated_deployment,
                        version=EnterpriseProfileRow.version + 1,
                        updated_at=profile.updated_at,
                    )
                )
                if changed.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                    raise VersionConflict(workspace_id)
            payload = self._profile_payload(profile)
            self._append_audit(
                session,
                actor_id,
                "enterprise.update",
                "enterprise_profile",
                workspace_id,
                before,
                payload,
                request_id,
                now,
            )
            self._remember(session, "enterprise.update", idempotency_key, fingerprint, payload, now)
            return profile

    def enterprise_profile(self, workspace_id: str) -> EnterpriseProfile | None:
        self._assert_workspace(workspace_id)
        with self._session_factory() as session:
            row = session.scalar(select(EnterpriseProfileRow).where(*self._scope_clauses(EnterpriseProfileRow)))
            return None if row is None else self._profile(row)

    def audit_events(
        self,
        workspace_id: str,
        *,
        request_id: str | None = None,
        search: str | None = None,
        actor_id: str | None = None,
        action: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 51,
    ) -> list[AuditEvent]:
        self._assert_workspace(workspace_id)
        bounded_limit = max(1, min(limit, 101))
        with self._session_factory() as session:
            query = select(AuditRow).where(*self._scope_clauses(AuditRow))
            if request_id is not None:
                query = query.where(AuditRow.request_id == request_id)
            if search is not None:
                pattern = f"%{search}%"
                query = query.where(
                    or_(
                        AuditRow.request_id.ilike(pattern),
                        AuditRow.actor_id.ilike(pattern),
                        AuditRow.action.ilike(pattern),
                        AuditRow.object_type.ilike(pattern),
                        AuditRow.object_id.ilike(pattern),
                    )
                )
            if actor_id is not None:
                query = query.where(AuditRow.actor_id == actor_id)
            if action is not None:
                query = query.where(AuditRow.action == action)
            if object_type is not None:
                query = query.where(AuditRow.object_type == object_type)
            if object_id is not None:
                query = query.where(AuditRow.object_id == object_id)
            if cursor is not None:
                cursor_time, cursor_id = cursor
                query = query.where(
                    (AuditRow.occurred_at < cursor_time)
                    | ((AuditRow.occurred_at == cursor_time) & (AuditRow.event_id < cursor_id))
                )
            return [
                self._audit(row)
                for row in session.scalars(
                    query.order_by(AuditRow.occurred_at.desc(), AuditRow.event_id.desc()).limit(bounded_limit)
                ).all()
            ]

    def _replay(self, session: Session, action: str, key: str, fingerprint: str) -> dict[str, object] | None:
        row = session.scalar(
            select(IdempotencyRow)
            .where(
                *self._scope_clauses(IdempotencyRow),
                IdempotencyRow.action == action,
                IdempotencyRow.idempotency_key == key,
            )
            .with_for_update()
        )
        if row is None:
            return None
        if row.fingerprint != fingerprint:
            raise IdempotencyConflict(key)
        return dict(row.result_payload)

    def _remember(
        self, session: Session, action: str, key: str, fingerprint: str, result: dict[str, object], now: datetime
    ) -> None:
        session.add(
            IdempotencyRow(
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                action=action,
                idempotency_key=key,
                fingerprint=fingerprint,
                result_payload=result,
                created_at=_utc(now),
            )
        )

    def _append_audit(
        self,
        session: Session,
        actor_id: str,
        action: str,
        object_type: str,
        object_id: str,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
        request_id: str,
        now: datetime,
    ) -> None:
        session.add(
            AuditRow(
                tenant_id=self._scope.tenant_id,
                workspace_id=self._scope.workspace_id,
                event_id=str(uuid4()),
                actor_id=actor_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                before_payload=before,
                after_payload=after,
                request_id=request_id,
                result="success",
                occurred_at=_utc(now),
            )
        )

    def _workspace_query(self):
        return select(WorkspaceRow).where(*self._scope_clauses(WorkspaceRow))

    def _invitation_query(self, invitation_id: str):
        return select(InvitationRow).where(
            *self._scope_clauses(InvitationRow), InvitationRow.invitation_id == invitation_id
        )

    def _scope_clauses(self, row_type: type[Any]) -> tuple[Any, Any]:
        return row_type.tenant_id == self._scope.tenant_id, row_type.workspace_id == self._scope.workspace_id

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self._scope.workspace_id:
            raise NotFound(workspace_id)

    @staticmethod
    def _default_roles() -> tuple[tuple[str, str, frozenset[str]], ...]:
        return (
            ("owner", "所有者", DEFAULT_ROLE_PERMISSIONS["owner"]),
            ("admin", "管理员", DEFAULT_ROLE_PERMISSIONS["admin"]),
            ("member", "成员", DEFAULT_ROLE_PERMISSIONS["member"]),
        )

    @staticmethod
    def _fingerprint(value: Mapping[str, object]) -> str:
        import hashlib

        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

    def _invitation_row(self, value: Invitation) -> InvitationRow:
        return InvitationRow(
            tenant_id=self._scope.tenant_id,
            workspace_id=self._scope.workspace_id,
            invitation_id=value.id,
            email=value.email,
            role_id=value.role_id,
            state=value.state.value,
            expires_at=_utc(value.expires_at),
            invited_by=value.invited_by,
            accepted_by=value.accepted_by,
            version=value.version,
        )

    @staticmethod
    def _invitation(row: InvitationRow) -> Invitation:
        return Invitation(
            row.invitation_id,
            row.workspace_id,
            row.email,
            row.role_id,
            InvitationState(row.state),
            _utc(row.expires_at),
            row.invited_by,
            row.accepted_by,
            row.version,
        )

    @staticmethod
    def _invitation_payload(value: Invitation) -> dict[str, object]:
        return {
            "id": value.id,
            "workspace_id": value.workspace_id,
            "email": value.email,
            "role_id": value.role_id,
            "state": value.state.value,
            "expires_at": _utc(value.expires_at).isoformat(),
            "invited_by": value.invited_by,
            "accepted_by": value.accepted_by,
            "version": value.version,
        }

    @staticmethod
    def _invitation_from_payload(value: Mapping[str, object]) -> Invitation:
        return Invitation(
            str(value["id"]),
            str(value["workspace_id"]),
            str(value["email"]),
            str(value["role_id"]),
            InvitationState(str(value["state"])),
            datetime.fromisoformat(str(value["expires_at"])),
            str(value["invited_by"]),
            cast(str | None, value.get("accepted_by")),
            _integer(value["version"]),
        )

    @staticmethod
    def _role(row: RoleRow) -> Role:
        return Role(row.role_id, row.workspace_id, row.name, frozenset(row.permissions), row.version)

    @staticmethod
    def _role_payload(value: Role) -> dict[str, object]:
        return {
            "id": value.id,
            "workspace_id": value.workspace_id,
            "name": value.name,
            "permissions": sorted(value.permissions),
            "version": value.version,
        }

    @staticmethod
    def _role_from_payload(value: Mapping[str, object]) -> Role:
        return Role(
            str(value["id"]),
            str(value["workspace_id"]),
            str(value["name"]),
            frozenset(cast(list[str], value["permissions"])),
            _integer(value["version"]),
        )

    @staticmethod
    def _profile(row: EnterpriseProfileRow) -> EnterpriseProfile:
        return EnterpriseProfile(
            row.workspace_id,
            row.legal_name,
            row.invoice_title,
            row.data_retention_days,
            dict(row.security_policy),
            dict(row.dedicated_deployment),
            row.version,
            _utc(row.updated_at),
        )

    @staticmethod
    def _profile_payload(value: EnterpriseProfile) -> dict[str, object]:
        return {
            "workspace_id": value.workspace_id,
            "legal_name": value.legal_name,
            "invoice_title": value.invoice_title,
            "data_retention_days": value.data_retention_days,
            "security_policy": value.security_policy,
            "dedicated_deployment": value.dedicated_deployment,
            "version": value.version,
            "updated_at": _utc(value.updated_at).isoformat(),
        }

    @staticmethod
    def _profile_from_payload(value: Mapping[str, object]) -> EnterpriseProfile:
        return EnterpriseProfile(
            str(value["workspace_id"]),
            cast(str | None, value.get("legal_name")),
            cast(str | None, value.get("invoice_title")),
            cast(int | None, value.get("data_retention_days")),
            dict(cast(Mapping[str, object], value["security_policy"])),
            dict(cast(Mapping[str, object], value["dedicated_deployment"])),
            _integer(value["version"]),
            datetime.fromisoformat(str(value["updated_at"])),
        )

    @staticmethod
    def _audit(row: AuditRow) -> AuditEvent:
        return AuditEvent(
            row.event_id,
            row.workspace_id,
            row.actor_id,
            row.action,
            row.object_type,
            row.object_id,
            row.before_payload,
            row.after_payload,
            row.request_id,
            row.result,
            _utc(row.occurred_at),
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None or value.utcoffset() is None else value.astimezone(UTC)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError("integer payload value required")
    return int(value)
