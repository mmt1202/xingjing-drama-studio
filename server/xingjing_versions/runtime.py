"""Project-authorized version reads, durable checkpoints and domain command recovery."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_editing.ids import Uuid7IdGenerator
from server.xingjing_identity_context import PlatformSessionGateway, TrustedWorkspaceContextResolver
from server.xingjing_platform_persistence.persistence import OutboxRow, ProjectRow

from .actions import VersionActions
from .contracts import KINDS, VIEW_PERMISSIONS, Ref, ReportRequest, VersionAction, VersionFailure, require_kind
from .models import VersionAuditRow, VersionCheckpointRow, VersionCommandRow, VersionReportRow
from .reader import VersionReader, canonical, check_source, compare_entries, iso, scoped


def authorize_project(session: Session, context, project_id):
    if "project.view" not in context.permissions:
        raise VersionFailure("VERSION_PERMISSION_DENIED", 403)
    if context.role.casefold() == "api_key":
        raise VersionFailure("VERSION_INTERACTIVE_SESSION_REQUIRED", 403)
    check_source(session, "XINGJING_PROJECT_DATABASE_URL")
    if (
        session.scalar(
            select(ProjectRow.id).where(
                *scoped(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None)
            )
        )
        is None
    ):
        raise VersionFailure("VERSION_PROJECT_NOT_FOUND", 404)
    if context.role.casefold() in {"owner", "admin", "platform_admin"}:
        return context
    check_source(session, "XINGJING_TEAM_DATABASE_URL")
    params = {
        "tenant": context.tenant_id,
        "workspace": context.workspace_id,
        "project": project_id,
        "actor": context.actor_id,
    }
    assignment = session.execute(
        text("""SELECT permissions,data_scope FROM xingjing_project_members
        WHERE tenant_id=:tenant AND workspace_id=:workspace AND project_id=:project AND member_id=:actor
        AND active IS TRUE"""),
        params,
    ).one_or_none()
    if assignment is None:
        raise VersionFailure("VERSION_PROJECT_SCOPE_DENIED", 403)
    permissions, data_scope = assignment
    permissions = json.loads(permissions) if isinstance(permissions, str) else permissions
    data_scope = json.loads(data_scope) if isinstance(data_scope, str) else data_scope
    if (
        not isinstance(data_scope, list)
        or not data_scope
        or any(value != f"project:{project_id}" for value in data_scope)
    ):
        raise VersionFailure("VERSION_NARROW_SCOPE_UNSUPPORTED", 403)
    workspace_permissions = session.execute(
        text("""SELECT role.permissions FROM xingjing_team_members AS member
        JOIN xingjing_team_roles AS role ON role.tenant_id=member.tenant_id AND role.workspace_id=member.workspace_id
        AND role.role_id=member.role_id WHERE member.tenant_id=:tenant AND member.workspace_id=:workspace
        AND member.member_id=:actor AND member.active IS TRUE"""),
        params,
    ).scalar_one_or_none()
    workspace_permissions = (
        json.loads(workspace_permissions) if isinstance(workspace_permissions, str) else workspace_permissions
    )
    if (
        not isinstance(permissions, list)
        or not all(isinstance(value, str) for value in permissions)
        or not isinstance(workspace_permissions, list)
        or not all(isinstance(value, str) for value in workspace_permissions)
    ):
        raise VersionFailure("VERSION_PROJECT_SCOPE_DENIED", 403)
    context = replace(
        context, permissions=context.permissions & frozenset(permissions) & frozenset(workspace_permissions)
    )
    if "project.view" not in context.permissions:
        raise VersionFailure("VERSION_PROJECT_SCOPE_DENIED", 403)
    return context


class VersionRuntime:
    def __init__(
        self,
        sessions: Callable[[], Session] | None,
        resolver,
        *,
        async_sessions=None,
        quality_policy=None,
        close=None,
        async_close=None,
        unavailable_code=None,
    ):
        self.sessions, self.resolver = sessions, resolver
        self.reader = VersionReader(
            quality_policy_available=quality_policy is not None, final_selection_available=async_sessions is not None
        )
        self.actions = VersionActions(sessions, async_sessions, quality_policy=quality_policy) if sessions else None
        self._close, self._async_close = close, async_close
        self.unavailable_code = unavailable_code
        self.ids = Uuid7IdGenerator()

    def session(self):
        if self.sessions is None:
            raise VersionFailure(self.unavailable_code or "VERSION_DATABASE_UNAVAILABLE", 503)
        return self.sessions()

    async def close(self):
        if self._async_close:
            await self._async_close()
        if self._close:
            self._close()

    async def context(self, request: Request, project_id: str):
        value = self.resolver(request)
        context = await value if inspect.isawaitable(value) else value
        for header, expected in (("X-Workspace-Id", context.workspace_id), ("X-Project-Id", project_id)):
            if request.headers.get(header) and request.headers[header] != expected:
                raise VersionFailure("VERSION_CONTEXT_MISMATCH", 403)
        return await asyncio.to_thread(self.authorize, context, project_id)

    def authorize(self, context, project_id):
        with self.session() as session:
            return authorize_project(session, context, project_id)

    @staticmethod
    def _scope(context, project_id):
        return {"workspaceId": context.workspace_id, "projectId": project_id, "actorId": context.actor_id}

    def list(
        self,
        context,
        project_id,
        *,
        kind=None,
        q="",
        episode_id=None,
        object_id=None,
        shot_id=None,
        state=None,
        offset=0,
        limit=25,
    ):
        with self.session() as session:
            entries = self.reader.entries(session, context, project_id, kind=kind, object_id=object_id, shot_id=shot_id)
        nodes = [entry.node for entry in entries]
        if q.strip():
            needle = q.strip().casefold()
            nodes = [
                node
                for node in nodes
                if needle in " ".join(str(node.get(field, "")) for field in ("label", "summary", "ref")).casefold()
            ]
        if episode_id:
            nodes = [node for node in nodes if node["episodeId"] == episode_id]
        if state:
            nodes = (
                [node for node in nodes if node["actions"]["canRestore"]]
                if state == "restorable"
                else [node for node in nodes if node[state]]
            )
        kinds = [item for item in KINDS if VIEW_PERMISSIONS[item] in context.permissions]
        today = datetime.now(UTC).date().isoformat()
        return {
            "scope": self._scope(context, project_id),
            "permissions": {
                "canSnapshot": "project.manage" in context.permissions and bool(kinds),
                "canReport": bool(kinds),
            },
            "items": nodes[offset : offset + limit],
            "kinds": kinds,
            "total": len(nodes),
            "offset": offset,
            "limit": limit,
            "summary": {
                "totalVersions": len(nodes),
                "todayAdded": sum(bool(node["createdAt"]) and node["createdAt"][:10] == today for node in nodes),
                "restorable": sum(bool(node["actions"]["canRestore"]) for node in nodes),
                "conflicts": None,
                "unknownTime": sum(node["createdAt"] is None for node in nodes),
            },
        }

    def detail(self, context, project_id, ref: Ref):
        with self.session() as session:
            entries = self.reader.entries(
                session, context, project_id, kind=ref.kind, object_id=ref.objectId, shot_id=ref.shotId
            )
            entry = next((item for item in entries if item.node["ref"] == ref.dto()), None)
            if entry is None:
                raise VersionFailure("VERSION_NOT_FOUND", 404)
            events = session.scalars(
                select(VersionAuditRow)
                .where(
                    *scoped(VersionAuditRow, context),
                    VersionAuditRow.project_id == project_id,
                    VersionAuditRow.ref["kind"].as_string() == ref.kind,
                    VersionAuditRow.ref["objectId"].as_string() == ref.objectId,
                )
                .order_by(VersionAuditRow.created_at.desc(), VersionAuditRow.id.desc())
                .limit(50)
            ).all()
            return {
                "node": entry.node,
                "versions": [item.node for item in entries],
                "content": entry.content,
                "history": [
                    {"action": event.action, "actorId": event.actor_id, "createdAt": iso(event.created_at)}
                    for event in events
                ],
            }

    def compare(self, context, project_id, baseline: Ref, candidate: Ref):
        with self.session() as session:
            return compare_entries(
                self.reader.get(session, context, project_id, baseline),
                self.reader.get(session, context, project_id, candidate),
            )

    @staticmethod
    def checkpoint_dto(row):
        return {
            "id": row.id,
            "ref": row.ref,
            "sourceDigest": row.source_digest,
            "label": row.label,
            "note": row.note,
            "node": row.snapshot,
            "createdAt": iso(row.created_at),
            "createdBy": row.created_by,
            "sourceRevision": row.snapshot["headRevision"],
        }

    def checkpoints(self, context, project_id, offset, limit):
        kinds = [kind for kind in KINDS if VIEW_PERMISSIONS[kind] in context.permissions]
        conditions = [
            *scoped(VersionCheckpointRow, context),
            VersionCheckpointRow.project_id == project_id,
            VersionCheckpointRow.ref["kind"].as_string().in_(kinds),
        ]
        with self.session() as session:
            total = session.scalar(select(func.count()).select_from(VersionCheckpointRow).where(*conditions)) or 0
            rows = session.scalars(
                select(VersionCheckpointRow)
                .where(*conditions)
                .order_by(VersionCheckpointRow.created_at.desc(), VersionCheckpointRow.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return {
                "items": [self.checkpoint_dto(row) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    @staticmethod
    def _fingerprint(project_id, action, payload):
        return hashlib.sha256(canonical([project_id, action, payload])).hexdigest()

    @staticmethod
    def _domain_key(context, project_id, key):
        return (
            "versions:"
            + hashlib.sha256(
                canonical([context.tenant_id, context.workspace_id, project_id, context.actor_id, key])
            ).hexdigest()
        )

    @staticmethod
    def _lock_project(session, context, project_id):
        session.execute(
            update(ProjectRow)
            .where(*scoped(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None))
            .values(updated_at=ProjectRow.updated_at)
        )
        if (
            session.scalar(
                select(ProjectRow.id)
                .where(*scoped(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None))
                .with_for_update()
            )
            is None
        ):
            raise VersionFailure("VERSION_PROJECT_NOT_FOUND", 404)

    @staticmethod
    def _receipt(session, context, project_id, key, fingerprint):
        row = session.get(VersionCommandRow, (context.tenant_id, context.workspace_id, context.actor_id, key))
        if row and (row.project_id != project_id or row.fingerprint != fingerprint):
            raise VersionFailure("VERSION_IDEMPOTENCY_CONFLICT", 409)
        return row

    def _evidence(self, session, context, project_id, action, ref, before, result, now):
        event_id = self.ids.new_id("version_event")
        session.add(
            VersionAuditRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                id=event_id,
                project_id=project_id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                action=action,
                ref=ref,
                before=before,
                after=result,
                created_at=now,
            )
        )
        session.add(
            OutboxRow(
                id=event_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                event_type=f"version.{action}",
                aggregate_type="version_operation",
                aggregate_id=event_id,
                payload={
                    "projectId": project_id,
                    "ref": ref,
                    "actorId": context.actor_id,
                    "requestId": context.request_id,
                },
                schema_version=1,
                created_at=now,
            )
        )

    def _claim(self, context, project_id, command: VersionAction, key, fingerprint):
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            self.reader.assert_scope(session, context, project_id, command.ref)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt:
                return copy.deepcopy(receipt.response), receipt.domain_key, False
            entry = self.reader.get(session, context, project_id, command.ref)
            if (
                entry.node["headRevision"] != command.expectedRevision
                or entry.node["contentDigest"] != command.sourceDigest
            ):
                raise VersionFailure("VERSION_CONFLICT", 409)
            capability = {"snapshot": "canSnapshot", "restore": "canRestore", "lock": "canLock"}[command.action]
            if not entry.node["actions"][capability]:
                reason = entry.node["actions"].get("reason", "VERSION_ACTION_UNAVAILABLE")
                raise VersionFailure(reason, 503 if reason == "QUALITY_POLICY_REQUIRED" else 409)
            domain_key = self._domain_key(context, project_id, key)
            session.add(
                VersionCommandRow(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    idempotency_key=key,
                    project_id=project_id,
                    action=command.action,
                    fingerprint=fingerprint,
                    domain_key=domain_key,
                    payload={"command": command.model_dump(mode="json"), "before": entry.node},
                    response=None,
                    created_at=datetime.now(UTC),
                )
            )
            return None, domain_key, True

    async def command(self, context, project_id, command: VersionAction, key):
        context = await asyncio.to_thread(self.authorize, context, project_id)
        require_kind(context, command.ref.kind, manage=command.action != "snapshot")
        if command.action == "snapshot" and "project.manage" not in context.permissions:
            raise VersionFailure("VERSION_PERMISSION_DENIED", 403)
        payload = command.model_dump(mode="json")
        fingerprint = self._fingerprint(project_id, command.action, payload)
        result, domain_key, fresh = await asyncio.to_thread(self._claim, context, project_id, command, key, fingerprint)
        if result is not None:
            return result
        if command.action == "snapshot":
            return await asyncio.to_thread(self._checkpoint, context, project_id, command, key, fingerprint)
        if self.actions is None:
            raise VersionFailure("VERSION_DATABASE_UNAVAILABLE", 503)
        outcome = await self.actions.execute(
            context, project_id, command.ref.dto(), command.action, command.expectedRevision, domain_key, command.note
        )
        return await asyncio.to_thread(self._finish, context, project_id, command, key, fingerprint, outcome)

    def _checkpoint(self, context, project_id, command, key, fingerprint):
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt.response is not None:
                return receipt.response
            now = datetime.now(UTC)
            node = copy.deepcopy(receipt.payload["before"])
            row = VersionCheckpointRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                id=self.ids.new_id("checkpoint"),
                project_id=project_id,
                ref=command.ref.dto(),
                source_digest=command.sourceDigest,
                label=command.label or node["label"][:160],
                note=command.note,
                snapshot=node,
                created_by=context.actor_id,
                created_at=now,
            )
            session.add(row)
            result = {"checkpoint": self.checkpoint_dto(row)}
            receipt.response = result
            self._evidence(
                session, context, project_id, "snapshot", command.ref.dto(), receipt.payload["before"], result, now
            )
            return result

    def _finish(self, context, project_id, command, key, fingerprint, outcome):
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt.response is not None:
                return receipt.response
            ref = Ref.model_validate(outcome["ref"])
            node = self.reader.get(session, context, project_id, ref).node
            result = {"node": node, "result": outcome, "appliedRevision": outcome["revision"]}
            receipt.response = result
            self._evidence(
                session,
                context,
                project_id,
                command.action,
                ref.dto(),
                receipt.payload["before"],
                result,
                datetime.now(UTC),
            )
            return result

    def report(self, context, project_id, payload: ReportRequest, key):
        for ref in (payload.baseline, payload.candidate):
            require_kind(context, ref.kind)
        fingerprint = self._fingerprint(project_id, "report", payload.model_dump(mode="json"))
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            for ref in (payload.baseline, payload.candidate):
                self.reader.assert_scope(session, context, project_id, ref)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt and receipt.response:
                return receipt.response
            report = compare_entries(
                self.reader.get(session, context, project_id, payload.baseline),
                self.reader.get(session, context, project_id, payload.candidate),
            )
            now = datetime.now(UTC)
            row = VersionReportRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                id=self.ids.new_id("version_report"),
                project_id=project_id,
                report=report,
                created_by=context.actor_id,
                created_at=now,
            )
            session.add(row)
            result = {"reportId": row.id, "createdAt": iso(now), "createdBy": context.actor_id, "report": report}
            session.add(
                VersionCommandRow(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    idempotency_key=key,
                    project_id=project_id,
                    action="report",
                    fingerprint=fingerprint,
                    domain_key=self._domain_key(context, project_id, key),
                    payload=payload.model_dump(mode="json"),
                    response=result,
                    created_at=now,
                )
            )
            self._evidence(session, context, project_id, "report", payload.candidate.dto(), None, result, now)
            return result


def create_production_version_runtime(*, database_url=None, context_resolver=None, quality_policy=None):
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    url = database_url if database_url is not None else os.environ.get("XINGJING_VERSION_DATABASE_URL", "").strip()
    if not url:
        return VersionRuntime(None, resolver, unavailable_code="XINGJING_VERSION_DATABASE_URL_REQUIRED")
    try:
        parsed = make_url(url)
        if parsed.get_driver_name() in {"asyncpg", "aiosqlite", "asyncmy", "aiomysql"}:
            raise ValueError("synchronous URL required")
        engine = create_engine(parsed, pool_pre_ping=True)
        driver = {"sqlite": "sqlite+aiosqlite", "postgresql": "postgresql+asyncpg"}.get(parsed.get_backend_name())
        async_engine = create_async_engine(parsed.set(drivername=driver), pool_pre_ping=True) if driver else None
    except (SQLAlchemyError, ValueError, ModuleNotFoundError):
        return VersionRuntime(None, resolver, unavailable_code="VERSION_DATABASE_UNAVAILABLE")
    return VersionRuntime(
        sessionmaker(engine, expire_on_commit=False),
        resolver,
        async_sessions=async_sessionmaker(async_engine, expire_on_commit=False) if async_engine else None,
        quality_policy=quality_policy,
        close=engine.dispose,
        async_close=async_engine.dispose if async_engine else None,
    )
