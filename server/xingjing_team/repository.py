from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from server.xingjing_identity_context.permissions import WORKSPACE_OWNER_PERMISSIONS

from .errors import IdempotencyConflict, InvitationStateError, NotFound, SeatLimitReached, VersionConflict
from .models import (
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

T = TypeVar("T")
ALL_OWNER_PERMISSIONS = WORKSPACE_OWNER_PERMISSIONS


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteTeamRepository:
    """SQLite 原子实现；可由实现 TeamRepository 的主线数据库适配器替换。"""

    def __init__(self, path: str | Path):
        self._path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS team_workspaces (
                    id TEXT PRIMARY KEY, seat_limit INTEGER NOT NULL CHECK (seat_limit > 0),
                    permission_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS team_roles (
                    workspace_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
                    permissions_json TEXT NOT NULL, version INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, id)
                );
                CREATE TABLE IF NOT EXISTS team_members (
                    workspace_id TEXT NOT NULL, member_id TEXT NOT NULL, email TEXT NOT NULL,
                    role_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (workspace_id, member_id), UNIQUE (workspace_id, email)
                );
                CREATE TABLE IF NOT EXISTS team_invitations (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL,
                    role_id TEXT NOT NULL, state TEXT NOT NULL, expires_at TEXT NOT NULL,
                    invited_by TEXT NOT NULL, accepted_by TEXT, version INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_team_invitation
                    ON team_invitations(workspace_id, email) WHERE state = 'pending';
                CREATE TABLE IF NOT EXISTS team_enterprise_profiles (
                    workspace_id TEXT PRIMARY KEY, legal_name TEXT, invoice_title TEXT,
                    data_retention_days INTEGER, security_policy_json TEXT NOT NULL,
                    dedicated_deployment_json TEXT NOT NULL, version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS team_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                    workspace_id TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
                    object_type TEXT NOT NULL, object_id TEXT NOT NULL, before_json TEXT,
                    after_json TEXT, request_id TEXT NOT NULL, result TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS team_idempotency (
                    workspace_id TEXT NOT NULL, action TEXT NOT NULL, key TEXT NOT NULL,
                    fingerprint_json TEXT NOT NULL, result_json TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, action, key)
                );
                CREATE TRIGGER IF NOT EXISTS team_audit_events_no_update
                    BEFORE UPDATE ON team_audit_events BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS team_audit_events_no_delete
                    BEFORE DELETE ON team_audit_events BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END;
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(team_idempotency)")}
            if "fingerprint_json" not in columns:
                connection.execute("ALTER TABLE team_idempotency ADD COLUMN fingerprint_json TEXT")

    def _write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def create_workspace(self, workspace_id: str, *, seat_limit: int, owner: Actor) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            connection.execute("INSERT INTO team_workspaces(id, seat_limit) VALUES (?, ?)", (workspace_id, seat_limit))
            roles = (
                ("owner", "所有者", ALL_OWNER_PERMISSIONS),
                ("admin", "管理员", ALL_OWNER_PERMISSIONS - {"workspace.manage"}),
                ("member", "成员", frozenset({"workspace.view"})),
            )
            connection.executemany(
                "INSERT INTO team_roles VALUES (?, ?, ?, ?, 1)",
                [
                    (workspace_id, role_id, name, json.dumps(sorted(permissions)))
                    for role_id, name, permissions in roles
                ],
            )
            connection.execute(
                "INSERT INTO team_members VALUES (?, ?, ?, 'owner', 1)",
                (workspace_id, owner.id, owner.email.casefold()),
            )

        self._write(operation)

    def permission_version(self, workspace_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT permission_version FROM team_workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise NotFound(workspace_id)
        return int(row[0])

    def permissions_for(self, workspace_id: str, member_id: str) -> frozenset[str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT r.permissions_json FROM team_members m JOIN team_roles r
                   ON r.workspace_id=m.workspace_id AND r.id=m.role_id
                   WHERE m.workspace_id=? AND m.member_id=? AND m.active=1""",
                (workspace_id, member_id),
            ).fetchone()
        return None if row is None else frozenset(json.loads(row[0]))

    def _replay(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        action: str,
        key: str,
        fingerprint: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT fingerprint_json, result_json FROM team_idempotency WHERE workspace_id=? AND action=? AND key=?",
            (workspace_id, action, key),
        ).fetchone()
        if row is None:
            return None
        if row[0] is None or row[0] != _canonical_json(fingerprint):
            raise IdempotencyConflict(key)
        return json.loads(row[1])

    def _remember(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        action: str,
        key: str,
        fingerprint: dict[str, Any],
        value: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO team_idempotency (workspace_id, action, key, fingerprint_json, result_json) VALUES (?, ?, ?, ?, ?)",
            (workspace_id, action, key, _canonical_json(fingerprint), _canonical_json(value)),
        )

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        actor_id: str,
        action: str,
        object_type: str,
        object_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        request_id: str,
        now: datetime,
    ) -> None:
        connection.execute(
            "INSERT INTO team_audit_events(id,workspace_id,actor_id,action,object_type,object_id,before_json,after_json,request_id,result,occurred_at) VALUES (?,?,?,?,?,?,?,?,?,'success',?)",
            (
                str(uuid4()),
                workspace_id,
                actor_id,
                action,
                object_type,
                object_id,
                None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
                None if after is None else json.dumps(after, ensure_ascii=False, sort_keys=True),
                request_id,
                _iso(now),
            ),
        )

    @staticmethod
    def _invitation(row: sqlite3.Row) -> Invitation:
        return Invitation(
            row["id"],
            row["workspace_id"],
            row["email"],
            row["role_id"],
            InvitationState(row["state"]),
            _dt(row["expires_at"]),
            row["invited_by"],
            row["accepted_by"],
            row["version"],
        )

    @staticmethod
    def _invitation_dict(value: Invitation) -> dict[str, Any]:
        return {
            "id": value.id,
            "workspace_id": value.workspace_id,
            "email": value.email,
            "role_id": value.role_id,
            "state": value.state.value,
            "expires_at": _iso(value.expires_at),
            "invited_by": value.invited_by,
            "accepted_by": value.accepted_by,
            "version": value.version,
        }

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
        def operation(connection: sqlite3.Connection) -> Invitation:
            fingerprint = {
                "actor_id": actor_id,
                "email": email.casefold(),
                "expires_at": _iso(expires_at),
                "role_id": role_id,
            }
            replay = self._replay(connection, workspace_id, "invitation.create", idempotency_key, fingerprint)
            if replay is not None:
                return self._invitation_from_dict(replay)
            if (
                connection.execute(
                    "SELECT 1 FROM team_roles WHERE workspace_id=? AND id=?", (workspace_id, role_id)
                ).fetchone()
                is None
            ):
                raise NotFound(role_id)
            invitation = Invitation(
                str(uuid4()), workspace_id, email.casefold(), role_id, InvitationState.PENDING, expires_at, actor_id
            )
            connection.execute(
                "INSERT INTO team_invitations VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    invitation.id,
                    workspace_id,
                    invitation.email,
                    role_id,
                    invitation.state.value,
                    _iso(expires_at),
                    actor_id,
                    None,
                    invitation.version,
                ),
            )
            data = self._invitation_dict(invitation)
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="invitation.create",
                object_type="invitation",
                object_id=invitation.id,
                before=None,
                after=data,
                request_id=request_id,
                now=now,
            )
            self._remember(connection, workspace_id, "invitation.create", idempotency_key, fingerprint, data)
            return invitation

        return self._write(operation)

    def _invitation_from_dict(self, value: dict[str, Any]) -> Invitation:
        return Invitation(
            value["id"],
            value["workspace_id"],
            value["email"],
            value["role_id"],
            InvitationState(value["state"]),
            _dt(value["expires_at"]),
            value["invited_by"],
            value.get("accepted_by"),
            value["version"],
        )

    def invitation(self, invitation_id: str, *, now: datetime) -> Invitation:
        def operation(connection: sqlite3.Connection) -> Invitation:
            row = connection.execute("SELECT * FROM team_invitations WHERE id=?", (invitation_id,)).fetchone()
            if row is None:
                raise NotFound(invitation_id)
            invitation = self._invitation(row)
            if invitation.state is InvitationState.PENDING and invitation.expires_at <= now:
                before = self._invitation_dict(invitation)
                connection.execute(
                    "UPDATE team_invitations SET state='expired', version=version+1 WHERE id=?", (invitation_id,)
                )
                invitation = Invitation(
                    **{**invitation.__dict__, "state": InvitationState.EXPIRED, "version": invitation.version + 1}
                )
                self._audit(
                    connection,
                    workspace_id=invitation.workspace_id,
                    actor_id="system",
                    action="invitation.expire",
                    object_type="invitation",
                    object_id=invitation.id,
                    before=before,
                    after=self._invitation_dict(invitation),
                    request_id=f"expire:{invitation.id}",
                    now=now,
                )
            return invitation

        return self._write(operation)

    def accept_invitation(
        self, *, invitation_id: str, actor: Actor, request_id: str, idempotency_key: str, now: datetime
    ) -> Invitation:
        def operation(connection: sqlite3.Connection) -> Invitation:
            row = connection.execute("SELECT * FROM team_invitations WHERE id=?", (invitation_id,)).fetchone()
            if row is None:
                raise NotFound(invitation_id)
            invitation = self._invitation(row)
            fingerprint = {
                "actor_email": actor.email.casefold(),
                "actor_id": actor.id,
                "invitation_id": invitation_id,
            }
            replay = self._replay(
                connection, invitation.workspace_id, "invitation.accept", idempotency_key, fingerprint
            )
            if replay is not None:
                return self._invitation_from_dict(replay)
            if invitation.state is not InvitationState.PENDING or invitation.expires_at <= now:
                raise InvitationStateError(invitation.state.value)
            if actor.email.casefold() != invitation.email:
                raise InvitationStateError("invitation recipient mismatch")
            usage = self._seat_usage(connection, invitation.workspace_id)
            if usage.occupied >= usage.limit:
                raise SeatLimitReached(invitation.workspace_id)
            before = self._invitation_dict(invitation)
            connection.execute(
                "INSERT INTO team_members VALUES (?,?,?,?,1)",
                (invitation.workspace_id, actor.id, actor.email.casefold(), invitation.role_id),
            )
            connection.execute(
                "UPDATE team_invitations SET state='accepted', accepted_by=?, version=version+1 WHERE id=? AND state='pending'",
                (actor.id, invitation.id),
            )
            accepted = Invitation(
                **{
                    **invitation.__dict__,
                    "state": InvitationState.ACCEPTED,
                    "accepted_by": actor.id,
                    "version": invitation.version + 1,
                }
            )
            data = self._invitation_dict(accepted)
            self._audit(
                connection,
                workspace_id=invitation.workspace_id,
                actor_id=actor.id,
                action="invitation.accept",
                object_type="invitation",
                object_id=invitation.id,
                before=before,
                after=data,
                request_id=request_id,
                now=now,
            )
            self._remember(connection, invitation.workspace_id, "invitation.accept", idempotency_key, fingerprint, data)
            return accepted

        return self._write(operation)

    def revoke_invitation(
        self, *, invitation_id: str, actor_id: str, request_id: str, idempotency_key: str, now: datetime
    ) -> Invitation:
        def operation(connection: sqlite3.Connection) -> Invitation:
            row = connection.execute("SELECT * FROM team_invitations WHERE id=?", (invitation_id,)).fetchone()
            if row is None:
                raise NotFound(invitation_id)
            invitation = self._invitation(row)
            fingerprint = {"actor_id": actor_id, "invitation_id": invitation_id}
            replay = self._replay(
                connection, invitation.workspace_id, "invitation.revoke", idempotency_key, fingerprint
            )
            if replay is not None:
                return self._invitation_from_dict(replay)
            if invitation.state is not InvitationState.PENDING:
                raise InvitationStateError(invitation.state.value)
            before = self._invitation_dict(invitation)
            connection.execute(
                "UPDATE team_invitations SET state='revoked', version=version+1 WHERE id=?", (invitation_id,)
            )
            revoked = Invitation(
                **{**invitation.__dict__, "state": InvitationState.REVOKED, "version": invitation.version + 1}
            )
            data = self._invitation_dict(revoked)
            self._audit(
                connection,
                workspace_id=invitation.workspace_id,
                actor_id=actor_id,
                action="invitation.revoke",
                object_type="invitation",
                object_id=invitation.id,
                before=before,
                after=data,
                request_id=request_id,
                now=now,
            )
            self._remember(connection, invitation.workspace_id, "invitation.revoke", idempotency_key, fingerprint, data)
            return revoked

        return self._write(operation)

    def _seat_usage(self, connection: sqlite3.Connection, workspace_id: str) -> SeatUsage:
        row = connection.execute("SELECT seat_limit FROM team_workspaces WHERE id=?", (workspace_id,)).fetchone()
        if row is None:
            raise NotFound(workspace_id)
        occupied = connection.execute(
            "SELECT COUNT(*) FROM team_members WHERE workspace_id=? AND active=1", (workspace_id,)
        ).fetchone()[0]
        return SeatUsage(int(row[0]), int(occupied))

    def seat_usage(self, workspace_id: str) -> SeatUsage:
        with self._connect() as connection:
            return self._seat_usage(connection, workspace_id)

    @staticmethod
    def _role(row: sqlite3.Row) -> Role:
        return Role(
            row["id"], row["workspace_id"], row["name"], frozenset(json.loads(row["permissions_json"])), row["version"]
        )

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
        def operation(connection: sqlite3.Connection) -> Role:
            fingerprint = {
                "actor_id": actor_id,
                "expected_version": expected_version,
                "name": update.name,
                "permissions": sorted(update.permissions),
                "role_id": role_id,
            }
            replay = self._replay(connection, workspace_id, "role.update", idempotency_key, fingerprint)
            if replay is not None:
                return Role(
                    replay["id"], workspace_id, replay["name"], frozenset(replay["permissions"]), replay["version"]
                )
            row = connection.execute(
                "SELECT * FROM team_roles WHERE workspace_id=? AND id=?", (workspace_id, role_id)
            ).fetchone()
            if row is None:
                if expected_version != 0:
                    raise VersionConflict(role_id)
                before = None
                version = 1
                connection.execute(
                    "INSERT INTO team_roles VALUES (?,?,?,?,?)",
                    (workspace_id, role_id, update.name, json.dumps(sorted(update.permissions)), version),
                )
            else:
                old = self._role(row)
                if old.version != expected_version:
                    raise VersionConflict(role_id)
                before = {"name": old.name, "permissions": sorted(old.permissions), "version": old.version}
                version = old.version + 1
                connection.execute(
                    "UPDATE team_roles SET name=?,permissions_json=?,version=? WHERE workspace_id=? AND id=?",
                    (update.name, json.dumps(sorted(update.permissions)), version, workspace_id, role_id),
                )
            connection.execute(
                "UPDATE team_workspaces SET permission_version=permission_version+1 WHERE id=?", (workspace_id,)
            )
            role = Role(role_id, workspace_id, update.name, update.permissions, version)
            after = {"id": role.id, "name": role.name, "permissions": sorted(role.permissions), "version": role.version}
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="role.update",
                object_type="role",
                object_id=role_id,
                before=before,
                after=after,
                request_id=request_id,
                now=now,
            )
            self._remember(connection, workspace_id, "role.update", idempotency_key, fingerprint, after)
            return role

        return self._write(operation)

    def add_member(self, workspace_id: str, member_id: str, email: str, role_id: str) -> None:
        self._write(
            lambda connection: connection.execute(
                "INSERT INTO team_members VALUES (?,?,?,?,1)", (workspace_id, member_id, email.casefold(), role_id)
            )
        )

    @staticmethod
    def _profile_from_dict(value: dict[str, Any]) -> EnterpriseProfile:
        return EnterpriseProfile(
            value["workspace_id"],
            value.get("legal_name"),
            value.get("invoice_title"),
            value.get("data_retention_days"),
            value["security_policy"],
            value["dedicated_deployment"],
            value["version"],
            _dt(value["updated_at"]),
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
        def operation(connection: sqlite3.Connection) -> EnterpriseProfile:
            fingerprint = {
                "actor_id": actor_id,
                "data_retention_days": update.data_retention_days,
                "dedicated_deployment": update.dedicated_deployment,
                "expected_version": expected_version,
                "invoice_title": update.invoice_title,
                "legal_name": update.legal_name,
                "security_policy": update.security_policy,
            }
            replay = self._replay(connection, workspace_id, "enterprise.update", idempotency_key, fingerprint)
            if replay is not None:
                return self._profile_from_dict(replay)
            row = connection.execute(
                "SELECT * FROM team_enterprise_profiles WHERE workspace_id=?", (workspace_id,)
            ).fetchone()
            current_version = 0 if row is None else int(row["version"])
            if current_version != expected_version:
                raise VersionConflict(workspace_id)
            if update.data_retention_days is not None and update.data_retention_days < 1:
                raise ValueError("data_retention_days must be positive")
            before = (
                None
                if row is None
                else {
                    "workspace_id": workspace_id,
                    "legal_name": row["legal_name"],
                    "invoice_title": row["invoice_title"],
                    "data_retention_days": row["data_retention_days"],
                    "security_policy": json.loads(row["security_policy_json"]),
                    "dedicated_deployment": json.loads(row["dedicated_deployment_json"]),
                    "version": row["version"],
                    "updated_at": row["updated_at"],
                }
            )
            profile = EnterpriseProfile(
                workspace_id,
                update.legal_name,
                update.invoice_title,
                update.data_retention_days,
                dict(update.security_policy),
                dict(update.dedicated_deployment),
                current_version + 1,
                now,
            )
            connection.execute(
                "INSERT INTO team_enterprise_profiles VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET legal_name=excluded.legal_name,invoice_title=excluded.invoice_title,data_retention_days=excluded.data_retention_days,security_policy_json=excluded.security_policy_json,dedicated_deployment_json=excluded.dedicated_deployment_json,version=excluded.version,updated_at=excluded.updated_at",
                (
                    workspace_id,
                    profile.legal_name,
                    profile.invoice_title,
                    profile.data_retention_days,
                    json.dumps(profile.security_policy, ensure_ascii=False, sort_keys=True),
                    json.dumps(profile.dedicated_deployment, ensure_ascii=False, sort_keys=True),
                    profile.version,
                    _iso(now),
                ),
            )
            data = {
                "workspace_id": workspace_id,
                "legal_name": profile.legal_name,
                "invoice_title": profile.invoice_title,
                "data_retention_days": profile.data_retention_days,
                "security_policy": profile.security_policy,
                "dedicated_deployment": profile.dedicated_deployment,
                "version": profile.version,
                "updated_at": _iso(now),
            }
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="enterprise.update",
                object_type="enterprise_profile",
                object_id=workspace_id,
                before=before,
                after=data,
                request_id=request_id,
                now=now,
            )
            self._remember(connection, workspace_id, "enterprise.update", idempotency_key, fingerprint, data)
            return profile

        return self._write(operation)

    def enterprise_profile(self, workspace_id: str) -> EnterpriseProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM team_enterprise_profiles WHERE workspace_id=?", (workspace_id,)
            ).fetchone()
        if row is None:
            return None
        return EnterpriseProfile(
            row["workspace_id"],
            row["legal_name"],
            row["invoice_title"],
            row["data_retention_days"],
            json.loads(row["security_policy_json"]),
            json.loads(row["dedicated_deployment_json"]),
            row["version"],
            _dt(row["updated_at"]),
        )

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
        query = "SELECT * FROM team_audit_events WHERE workspace_id=?"
        params: list[object] = [workspace_id]
        if request_id is not None:
            query += " AND request_id=?"
            params.append(request_id)
        if search is not None:
            query += " AND (request_id LIKE ? OR actor_id LIKE ? OR action LIKE ? OR object_type LIKE ? OR object_id LIKE ?)"
            pattern = f"%{search}%"
            params.extend((pattern, pattern, pattern, pattern, pattern))
        if actor_id is not None:
            query += " AND actor_id=?"
            params.append(actor_id)
        if action is not None:
            query += " AND action=?"
            params.append(action)
        if object_type is not None:
            query += " AND object_type=?"
            params.append(object_type)
        if object_id is not None:
            query += " AND object_id=?"
            params.append(object_id)
        if cursor is not None:
            query += " AND (occurred_at < ? OR (occurred_at = ? AND id < ?))"
            cursor_time, cursor_id = cursor
            params.extend((cursor_time.isoformat(), cursor_time.isoformat(), cursor_id))
        query += " ORDER BY occurred_at DESC,id DESC LIMIT ?"
        params.append(max(1, min(limit, 101)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            AuditEvent(
                row["id"],
                row["workspace_id"],
                row["actor_id"],
                row["action"],
                row["object_type"],
                row["object_id"],
                None if row["before_json"] is None else json.loads(row["before_json"]),
                None if row["after_json"] is None else json.loads(row["after_json"]),
                row["request_id"],
                row["result"],
                _dt(row["occurred_at"]),
            )
            for row in rows
        ]
