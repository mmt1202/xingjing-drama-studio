from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from server.xingjing_team.errors import IdempotencyConflict, NotFound, VersionConflict
from server.xingjing_team_persistence import TeamPersistenceScope

SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class ProjectAccessAssignment:
    project_id: str
    member_id: str
    email: str
    workspace_role: str
    production_role: str
    data_scope: tuple[str, ...]
    permissions: tuple[str, ...]
    active: bool
    version: int
    updated_at: datetime


class ProjectAccessRepository:
    def __init__(self, session_factory: SessionFactory, scope: TeamPersistenceScope) -> None:
        self._session_factory = session_factory
        self._scope = scope

    def list_members(
        self,
        project_id: str,
        *,
        query: str | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> tuple[tuple[ProjectAccessAssignment, ...], str | None]:
        self._assert_project(project_id)
        size = min(max(page_size, 1), 100)
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT assignment.project_id, assignment.member_id, member.email,
                           member.role_id AS workspace_role, assignment.production_role,
                           assignment.data_scope, assignment.permissions, assignment.active,
                           assignment.version, assignment.updated_at
                    FROM xingjing_project_members AS assignment
                    JOIN xingjing_team_members AS member
                      ON member.tenant_id = assignment.tenant_id
                     AND member.workspace_id = assignment.workspace_id
                     AND member.member_id = assignment.member_id
                    WHERE assignment.tenant_id = :tenant_id
                      AND assignment.workspace_id = :workspace_id
                      AND assignment.project_id = :project_id
                      AND (:page_token IS NULL OR assignment.member_id > :page_token)
                      AND (
                        :query IS NULL
                        OR member.email ILIKE '%' || :query || '%'
                        OR assignment.production_role ILIKE '%' || :query || '%'
                      )
                    ORDER BY assignment.member_id
                    LIMIT :limit
                    """
                ),
                {
                    **self._scope_values(),
                    "project_id": project_id,
                    "page_token": page_token,
                    "query": query.strip() if query and query.strip() else None,
                    "limit": size + 1,
                },
            ).mappings().all()
        assignments = tuple(self._assignment(row) for row in rows[:size])
        next_token = assignments[-1].member_id if len(rows) > size and assignments else None
        return assignments, next_token

    def save_member(
        self,
        project_id: str,
        *,
        member_id: str,
        production_role: str,
        data_scope: tuple[str, ...],
        permissions: tuple[str, ...],
        active: bool,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> ProjectAccessAssignment:
        normalized = self._validate_assignment(
            member_id=member_id,
            production_role=production_role,
            data_scope=data_scope,
            permissions=permissions,
        )
        command = {
            "memberId": normalized[0],
            "productionRole": normalized[1],
            "dataScope": normalized[2],
            "permissions": normalized[3],
            "active": active,
            "expectedVersion": expected_version,
        }
        fingerprint = hashlib.sha256(
            json.dumps(command, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            self._lock_command(session, project_id, idempotency_key)
            replay = self._receipt(session, project_id, "project-member.save", idempotency_key)
            if replay is not None:
                if replay["fingerprint"] != fingerprint:
                    raise IdempotencyConflict(idempotency_key)
                return self._assignment(replay["result_payload"])
            self._require_project_and_member(session, project_id, normalized[0])
            existing = session.execute(
                text(
                    """
                    SELECT project_id, member_id, production_role, data_scope, permissions,
                           active, version, updated_at
                    FROM xingjing_project_members
                    WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
                      AND project_id=:project_id AND member_id=:member_id
                    FOR UPDATE
                    """
                ),
                {**self._scope_values(), "project_id": project_id, "member_id": normalized[0]},
            ).mappings().one_or_none()
            current_version = 0 if existing is None else int(existing["version"])
            if expected_version != current_version:
                raise VersionConflict(normalized[0])
            next_version = current_version + 1
            session.execute(
                text(
                    """
                    INSERT INTO xingjing_project_members (
                        tenant_id, workspace_id, project_id, member_id, production_role,
                        data_scope, permissions, active, version, created_at, updated_at
                    ) VALUES (
                        :tenant_id, :workspace_id, :project_id, :member_id, :production_role,
                        CAST(:data_scope AS jsonb), CAST(:permissions AS jsonb), :active,
                        :version, :now, :now
                    )
                    ON CONFLICT (tenant_id, workspace_id, project_id, member_id) DO UPDATE SET
                        production_role=excluded.production_role,
                        data_scope=excluded.data_scope,
                        permissions=excluded.permissions,
                        active=excluded.active,
                        version=excluded.version,
                        updated_at=excluded.updated_at
                    """
                ),
                {
                    **self._scope_values(),
                    "project_id": project_id,
                    "member_id": normalized[0],
                    "production_role": normalized[1],
                    "data_scope": json.dumps(normalized[2]),
                    "permissions": json.dumps(normalized[3]),
                    "active": active,
                    "version": next_version,
                    "now": now,
                },
            )
            stored = self._read_member(session, project_id, normalized[0])
            payload = self._payload(stored)
            session.execute(
                text(
                    """
                    INSERT INTO xingjing_project_access_receipts (
                        tenant_id,workspace_id,project_id,action,idempotency_key,
                        fingerprint,result_payload,created_at
                    ) VALUES (
                        :tenant_id,:workspace_id,:project_id,'project-member.save',
                        :idempotency_key,:fingerprint,CAST(:payload AS jsonb),:created_at
                    )
                    """
                ),
                {
                    **self._scope_values(),
                    "project_id": project_id,
                    "idempotency_key": idempotency_key,
                    "fingerprint": fingerprint,
                    "payload": json.dumps(payload, default=str),
                    "created_at": now,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO xingjing_project_access_audit_events (
                        event_id,tenant_id,workspace_id,project_id,request_id,actor_id,
                        action,member_id,before_payload,after_payload,result,occurred_at
                    ) VALUES (
                        :event_id,:tenant_id,:workspace_id,:project_id,:request_id,:actor_id,
                        'project-member.save',:member_id,CAST(:before_payload AS jsonb),
                        CAST(:after_payload AS jsonb),'SUCCESS',:occurred_at
                    )
                    """
                ),
                {
                    **self._scope_values(),
                    "event_id": str(uuid4()),
                    "project_id": project_id,
                    "request_id": request_id,
                    "actor_id": actor_id,
                    "member_id": normalized[0],
                    "before_payload": json.dumps(dict(existing), default=str) if existing else None,
                    "after_payload": json.dumps(payload, default=str),
                    "occurred_at": now,
                },
            )
            return stored

    def _assert_project(self, project_id: str) -> None:
        with self._session_factory() as session:
            exists = session.execute(
                text(
                    """
                    SELECT 1 FROM xingjing_projects
                    WHERE id=:project_id AND tenant_id=:tenant_id
                      AND workspace_id=:workspace_id AND deleted_at IS NULL
                    """
                ),
                {**self._scope_values(), "project_id": project_id},
            ).scalar_one_or_none()
        if exists is None:
            raise NotFound(project_id)

    def _require_project_and_member(self, session: Session, project_id: str, member_id: str) -> None:
        project = session.execute(
            text(
                """
                SELECT 1 FROM xingjing_projects
                WHERE id=:project_id AND tenant_id=:tenant_id
                  AND workspace_id=:workspace_id AND deleted_at IS NULL
                """
            ),
            {**self._scope_values(), "project_id": project_id},
        ).scalar_one_or_none()
        member = session.execute(
            text(
                """
                SELECT 1 FROM xingjing_team_members
                WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
                  AND member_id=:member_id AND active IS TRUE
                """
            ),
            {**self._scope_values(), "member_id": member_id},
        ).scalar_one_or_none()
        if project is None or member is None:
            raise NotFound(project_id if project is None else member_id)

    def _read_member(self, session: Session, project_id: str, member_id: str) -> ProjectAccessAssignment:
        row = session.execute(
            text(
                """
                SELECT assignment.project_id, assignment.member_id, member.email,
                       member.role_id AS workspace_role, assignment.production_role,
                       assignment.data_scope, assignment.permissions, assignment.active,
                       assignment.version, assignment.updated_at
                FROM xingjing_project_members AS assignment
                JOIN xingjing_team_members AS member
                  ON member.tenant_id=assignment.tenant_id
                 AND member.workspace_id=assignment.workspace_id
                 AND member.member_id=assignment.member_id
                WHERE assignment.tenant_id=:tenant_id AND assignment.workspace_id=:workspace_id
                  AND assignment.project_id=:project_id AND assignment.member_id=:member_id
                """
            ),
            {**self._scope_values(), "project_id": project_id, "member_id": member_id},
        ).mappings().one()
        return self._assignment(row)

    def _receipt(
        self,
        session: Session,
        project_id: str,
        action: str,
        idempotency_key: str,
    ) -> dict[str, object] | None:
        row = session.execute(
            text(
                """
                SELECT fingerprint,result_payload
                FROM xingjing_project_access_receipts
                WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
                  AND project_id=:project_id AND action=:action
                  AND idempotency_key=:idempotency_key
                """
            ),
            {
                **self._scope_values(),
                "project_id": project_id,
                "action": action,
                "idempotency_key": idempotency_key,
            },
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    def _lock_command(self, session: Session, project_id: str, idempotency_key: str) -> None:
        if not idempotency_key.strip():
            raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self._scope.workspace_id}:{project_id}:{idempotency_key}"},
        )

    def _scope_values(self) -> dict[str, str]:
        return {"tenant_id": self._scope.tenant_id, "workspace_id": self._scope.workspace_id}

    @staticmethod
    def _validate_assignment(
        *,
        member_id: str,
        production_role: str,
        data_scope: tuple[str, ...],
        permissions: tuple[str, ...],
    ) -> tuple[str, str, list[str], list[str]]:
        normalized_member = member_id.strip()
        normalized_role = production_role.strip()
        normalized_scope = sorted({value.strip() for value in data_scope if value.strip()})
        normalized_permissions = sorted({value.strip() for value in permissions if value.strip()})
        if not normalized_member or not normalized_role or not normalized_scope or not normalized_permissions:
            raise ValueError("PROJECT_ACCESS_ASSIGNMENT_INCOMPLETE")
        return normalized_member, normalized_role, normalized_scope, normalized_permissions

    @staticmethod
    def _assignment(row: object) -> ProjectAccessAssignment:
        if not isinstance(row, Mapping):
            raise ValueError("PROJECT_ACCESS_PAYLOAD_INVALID")
        source: dict[str, object] = {str(key): value for key, value in row.items()}
        updated_at = source["updated_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        if not isinstance(updated_at, datetime):
            raise ValueError("PROJECT_ACCESS_UPDATED_AT_INVALID")
        data_scope = source["data_scope"]
        permissions = source["permissions"]
        version = source["version"]
        if not isinstance(data_scope, list | tuple) or not all(isinstance(value, str) for value in data_scope):
            raise ValueError("PROJECT_ACCESS_DATA_SCOPE_INVALID")
        if not isinstance(permissions, list | tuple) or not all(isinstance(value, str) for value in permissions):
            raise ValueError("PROJECT_ACCESS_PERMISSIONS_INVALID")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("PROJECT_ACCESS_VERSION_INVALID")
        return ProjectAccessAssignment(
            project_id=str(source["project_id"]),
            member_id=str(source["member_id"]),
            email=str(source.get("email", "")),
            workspace_role=str(source.get("workspace_role", "")),
            production_role=str(source["production_role"]),
            data_scope=tuple(data_scope),
            permissions=tuple(permissions),
            active=bool(source["active"]),
            version=version,
            updated_at=updated_at,
        )

    @staticmethod
    def _payload(assignment: ProjectAccessAssignment) -> dict[str, object]:
        return asdict(assignment)
