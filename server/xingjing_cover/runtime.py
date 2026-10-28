"""Authoritative cover drafts with durable, recoverable M06 submission claims."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import os
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Request
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_editing_persistence.models import FinalVideoVersionRow, TimelineVersionRow
from server.xingjing_generation import GenerationRequest, TaskStatus
from server.xingjing_generation_runtime.runtime import GenerationRuntime, GenerationRuntimeUnavailable
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import OutboxRow, ProjectRow

from .contracts import ACTION_MODELS, Command, CoverFailure, CreatePlan
from .media import verify_cover_file
from .models import CoverAuditRow, CoverCommandRow, CoverPlanRow, CoverSelectionRow

TARGETS = ["douyin", "kuaishou", "wechat", "xiaohongshu"]
RATIOS = ["9:16", "3:4", "1:1", "16:9"]


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.astimezone(UTC).isoformat()


def _scope(model, context: TrustedWorkspaceContext):
    return model.tenant_id == context.tenant_id, model.workspace_id == context.workspace_id


def _require(context: TrustedWorkspaceContext, *permissions: str) -> None:
    if any(permission not in context.permissions for permission in permissions):
        raise CoverFailure("COVER_PERMISSION_DENIED", 403)


def plan_dto(row: CoverPlanRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "finalVersionId": row.final_version_id,
        "episodeId": row.episode_id,
        "target": row.target,
        "prompt": row.prompt,
        "aspectRatio": row.aspect_ratio,
        "titleSafeArea": row.title_safe_area,
        "version": row.version,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
        "selectedVariantId": row.selected_variant_id,
        "recommendedVariantId": row.recommended_variant_id,
        "appliedSelectionId": row.applied_selection_id,
    }


class CoverRuntime:
    def __init__(
        self,
        sessions: Callable[[], Session] | None,
        resolver,
        generation: GenerationRuntime,
        *,
        close: Callable[[], None] | None = None,
        unavailable_code: str | None = None,
    ):
        self.sessions = sessions
        self.resolver = resolver
        self.generation = generation
        self._close = close
        self.unavailable_code = unavailable_code

    def close(self) -> None:
        if self._close:
            self._close()

    def session(self) -> Session:
        if self.sessions is None:
            raise CoverFailure(self.unavailable_code or "COVER_DATABASE_UNAVAILABLE", 503)
        return self.sessions()

    async def context(self, request: Request, project_id: str) -> TrustedWorkspaceContext:
        resolved = self.resolver(request)
        context = await resolved if inspect.isawaitable(resolved) else resolved
        _require(context, "settings.view", "project.view", "final.view")
        if context.role.upper() == "API_KEY":
            raise CoverFailure("COVER_INTERACTIVE_SESSION_REQUIRED", 403)
        expected = request.headers.get("X-Workspace-Id")
        if expected and expected != context.workspace_id:
            raise CoverFailure("COVER_WORKSPACE_MISMATCH", 403)
        expected_project = request.headers.get("X-Project-Id")
        if expected_project and expected_project != project_id:
            raise CoverFailure("COVER_PROJECT_MISMATCH", 403)
        context = await asyncio.to_thread(self._check_project, context, project_id)
        _require(context, "settings.view", "project.view", "final.view")
        return context

    def _check_project(self, context, project_id):
        with self.session() as session:
            self._source_database(session, "XINGJING_PROJECT_DATABASE_URL", "XINGJING_EDITING_DATABASE_URL")
            if (
                session.scalar(
                    select(ProjectRow.id).where(
                        *_scope(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None)
                    )
                )
                is None
            ):
                raise CoverFailure("COVER_PROJECT_NOT_FOUND", 404)
            if context.role.casefold() not in {"owner", "admin", "platform_admin"}:
                self._source_database(session, "XINGJING_TEAM_DATABASE_URL")
                assignment = session.execute(
                    text("""
                    SELECT permissions, data_scope FROM xingjing_project_members
                    WHERE tenant_id=:tenant AND workspace_id=:workspace AND project_id=:project
                    AND member_id=:actor AND active IS TRUE
                """),
                    {
                        "tenant": context.tenant_id,
                        "workspace": context.workspace_id,
                        "project": project_id,
                        "actor": context.actor_id,
                    },
                ).one_or_none()
                if assignment is None:
                    raise CoverFailure("COVER_PROJECT_SCOPE_DENIED", 403)
                raw_permissions, raw_scope = assignment
                permissions = json.loads(raw_permissions) if isinstance(raw_permissions, str) else raw_permissions
                data_scope = json.loads(raw_scope) if isinstance(raw_scope, str) else raw_scope
                if (
                    not isinstance(data_scope, list)
                    or not data_scope
                    or any(value != f"project:{project_id}" for value in data_scope)
                ):
                    raise CoverFailure("COVER_NARROW_SCOPE_UNSUPPORTED", 403)
                workspace_permissions = session.execute(
                    text("""
                    SELECT role.permissions FROM xingjing_team_members AS member
                    JOIN xingjing_team_roles AS role ON role.tenant_id=member.tenant_id
                    AND role.workspace_id=member.workspace_id AND role.role_id=member.role_id
                    WHERE member.tenant_id=:tenant AND member.workspace_id=:workspace
                    AND member.member_id=:actor AND member.active IS TRUE
                """),
                    {"tenant": context.tenant_id, "workspace": context.workspace_id, "actor": context.actor_id},
                ).scalar_one_or_none()
                workspace_permissions = (
                    json.loads(workspace_permissions)
                    if isinstance(workspace_permissions, str)
                    else workspace_permissions
                )
                if (
                    not isinstance(permissions, list)
                    or not all(isinstance(value, str) for value in permissions)
                    or not isinstance(workspace_permissions, list)
                    or not all(isinstance(value, str) for value in workspace_permissions)
                ):
                    raise CoverFailure("COVER_PROJECT_SCOPE_DENIED", 403)
                context = replace(
                    context, permissions=context.permissions & frozenset(permissions) & frozenset(workspace_permissions)
                )
        return context

    @staticmethod
    def _source_database(session, *variables):
        from server.xingjing_analytics.database import identity

        for variable in variables:
            source_url = os.environ.get(variable, "").strip()
            if source_url:
                try:
                    if identity(session.get_bind().url) != identity(source_url):
                        raise ValueError("source database differs")
                except (ValueError, TypeError, SQLAlchemyError) as error:
                    raise CoverFailure("COVER_SOURCE_DATABASE_MISMATCH", 503) from error

    @staticmethod
    def permissions(context) -> dict[str, bool]:
        manage = "settings.manage" in context.permissions
        return {
            "canView": True,
            "canManage": manage,
            "canGenerate": manage and {"generation.view", "generation.manage"} <= context.permissions,
            "canApply": manage and {"generation.view", "export.manage"} <= context.permissions,
        }

    @staticmethod
    def scope_dto(context, project_id):
        return {"workspaceId": context.workspace_id, "projectId": project_id, "actorId": context.actor_id}

    def _final(self, session, context, project_id, version_id):
        final = session.scalar(
            select(FinalVideoVersionRow).where(
                *_scope(FinalVideoVersionRow, context),
                FinalVideoVersionRow.project_id == project_id,
                FinalVideoVersionRow.version_id == version_id,
            )
        )
        if final is None:
            raise CoverFailure("COVER_FINAL_VERSION_NOT_FOUND", 404)
        timeline = session.scalar(
            select(TimelineVersionRow).where(
                *_scope(TimelineVersionRow, context),
                TimelineVersionRow.project_id == project_id,
                TimelineVersionRow.timeline_id == final.timeline_id,
                TimelineVersionRow.version_id == final.timeline_version_id,
            )
        )
        episode = timeline.snapshot.get("episode_id") if timeline else None
        if episode is None or not str(episode).strip():
            raise CoverFailure("COVER_FINAL_EPISODE_UNAVAILABLE", 422)
        output = final.snapshot.get("output", {})
        return {
            "id": final.version_id,
            "episodeId": str(episode),
            "revision": timeline.revision,
            "createdAt": _iso(final.created_at),
            "durationMs": output.get("duration_ms"),
        }

    async def catalog(self, context, project_id):
        value = await asyncio.to_thread(self._catalog, context, project_id)
        availability = None
        if "generation.view" in context.permissions:
            try:
                availability = asdict(await self.generation.read_model_availability())
            except GenerationRuntimeUnavailable:
                pass
        return {**value, "generationAvailability": availability}

    def _catalog(self, context, project_id):
        with self.session() as session:
            finals = session.scalars(
                select(FinalVideoVersionRow)
                .where(*_scope(FinalVideoVersionRow, context), FinalVideoVersionRow.project_id == project_id)
                .order_by(FinalVideoVersionRow.created_at.desc(), FinalVideoVersionRow.version_id.desc())
                .limit(201)
            ).all()
            if len(finals) > 200:
                raise CoverFailure("COVER_FINAL_CATALOG_LIMIT", 422)
            final_dtos = [self._final(session, context, project_id, final.version_id) for final in finals]
        return {
            "scope": self.scope_dto(context, project_id),
            "permissions": self.permissions(context),
            "targets": TARGETS,
            "aspectRatios": RATIOS,
            "finalVideos": final_dtos,
        }

    def list(
        self, context, project_id, *, final_version_id=None, episode_id=None, target=None, q="", offset=0, limit=20
    ):
        conditions = [*_scope(CoverPlanRow, context), CoverPlanRow.project_id == project_id]
        for column, value in (
            (CoverPlanRow.final_version_id, final_version_id),
            (CoverPlanRow.episode_id, episode_id),
            (CoverPlanRow.target, target),
        ):
            if value:
                conditions.append(column == value)
        if q.strip():
            conditions.append(CoverPlanRow.name.icontains(q.strip(), autoescape=True))
        with self.session() as session:
            total = session.scalar(select(func.count()).select_from(CoverPlanRow).where(*conditions)) or 0
            rows = session.scalars(
                select(CoverPlanRow)
                .where(*conditions)
                .order_by(CoverPlanRow.created_at.desc(), CoverPlanRow.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return {"items": [plan_dto(row) for row in rows], "total": total, "offset": offset, "limit": limit}

    def _plan(self, session, context, project_id, plan_id):
        row = session.get(CoverPlanRow, (context.tenant_id, context.workspace_id, plan_id))
        if row is None or row.project_id != project_id:
            raise CoverFailure("COVER_PLAN_NOT_FOUND", 404)
        return row

    async def detail(self, context, project_id, plan_id):
        with self.session() as session:
            row = self._plan(session, context, project_id, plan_id)
            value = {
                "scope": self.scope_dto(context, project_id),
                "permissions": self.permissions(context),
                "plan": plan_dto(row),
                "variants": copy.deepcopy(row.variants),
            }
            candidates = copy.deepcopy(row.candidates)
            task_ids = list(row.generation_task_ids)
            audits = session.scalars(
                select(CoverAuditRow)
                .where(
                    *_scope(CoverAuditRow, context),
                    CoverAuditRow.project_id == project_id,
                    CoverAuditRow.plan_id == plan_id,
                )
                .order_by(CoverAuditRow.occurred_at.desc(), CoverAuditRow.id.desc())
                .limit(50)
            ).all()
            value["history"] = [
                {
                    "id": audit.id,
                    "version": audit.after["plan"]["version"],
                    "action": audit.action,
                    "actorId": audit.actor_id,
                    "createdAt": _iso(audit.occurred_at),
                }
                for audit in audits
            ]
        tasks = []
        public_candidates = {}
        if "generation.view" in context.permissions:
            for task_id in task_ids:
                try:
                    task = await self.generation.get_task(context, project_id, task_id)
                    tasks.append(task.model_dump(mode="json"))
                    if task.status is TaskStatus.SUCCEEDED:
                        for asset in await self.generation.generated_assets(context, project_id, task_id):
                            if asset.media_type == "image":
                                candidates.setdefault(
                                    asset.asset_id, {"taskId": task_id, "createdAt": _iso(asset.created_at)}
                                )
                except (GenerationRuntimeUnavailable, LookupError):
                    tasks.append({"task_id": task_id, "status": "unavailable"})
            for asset_id, frozen in candidates.items():
                candidate = {
                    "assetId": asset_id,
                    "taskId": frozen["taskId"],
                    "createdAt": frozen["createdAt"],
                    "available": False,
                }
                try:
                    verified, _ = await self.asset(
                        context, project_id, asset_id, frozen if "contentSha256" in frozen else None
                    )
                    candidate.update(available=True, contentSha256=verified["contentSha256"])
                except (CoverFailure, GenerationRuntimeUnavailable, LookupError):
                    candidate["reasonCode"] = "COVER_MEDIA_UNAVAILABLE"
                public_candidates[asset_id] = candidate
        return {**value, "candidates": list(public_candidates.values()), "generationTasks": tasks}

    async def asset(self, context, project_id, asset_id, frozen=None):
        _require(context, "generation.view")
        asset, path = await self.generation.generated_artifact_file(context, project_id, asset_id)
        task = await self.generation.get_task(context, project_id, asset.task_id)
        if task.status is not TaskStatus.SUCCEEDED or asset.media_type != "image":
            raise CoverFailure("COVER_CANDIDATE_NOT_READY", 422)
        if (
            asset.workspace_id != context.workspace_id
            or asset.project_id != project_id
            or not asset.object_key.startswith(f"{context.workspace_id}/{project_id}/{asset.task_id}/")
        ):
            raise CoverFailure("COVER_CANDIDATE_SCOPE_MISMATCH", 404)
        snapshot = {
            "assetId": asset.asset_id,
            "taskId": asset.task_id,
            "objectKey": asset.object_key,
            "contentSha256": asset.content_sha256,
            "sizeBytes": asset.size_bytes,
            "createdAt": _iso(asset.created_at),
            "provenance": {"kind": "generation-output", "taskId": asset.task_id},
        }
        if frozen and any(
            snapshot.get(key) != frozen.get(key)
            for key in ("assetId", "taskId", "objectKey", "contentSha256", "sizeBytes")
        ):
            raise CoverFailure("COVER_CANDIDATE_CHANGED", 409)
        verified = await asyncio.to_thread(verify_cover_file, path, frozen or snapshot)
        return {**snapshot, **verified}, path

    async def media(self, context, project_id, plan_id, asset_id):
        with self.session() as session:
            plan = self._plan(session, context, project_id, plan_id)
            frozen = plan.candidates.get(asset_id)
            tasks = list(plan.generation_task_ids)
        verified, path = await self.asset(context, project_id, asset_id, frozen)
        if frozen is None and verified["taskId"] not in tasks:
            raise CoverFailure("COVER_CANDIDATE_NOT_FOUND", 404)
        return verified, path

    @staticmethod
    def _fingerprint(project_id, plan_id, action, payload):
        return hashlib.sha256(
            json.dumps(
                [project_id, plan_id, action, payload], sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def _receipt(self, session, context, project_id, key, fingerprint):
        receipt = session.get(CoverCommandRow, (context.tenant_id, context.workspace_id, context.actor_id, key))
        if receipt and (receipt.project_id != project_id or receipt.fingerprint != fingerprint):
            raise CoverFailure("COVER_IDEMPOTENCY_CONFLICT", 409)
        return receipt

    def _lock_project(self, session, context, project_id):
        session.execute(
            update(ProjectRow)
            .where(*_scope(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None))
            .values(updated_at=ProjectRow.updated_at)
        )
        project = session.scalar(
            select(ProjectRow)
            .where(*_scope(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None))
            .with_for_update()
        )
        if project is None:
            raise CoverFailure("COVER_PROJECT_NOT_FOUND", 404)

    def _write_receipt(self, session, context, project_id, row, key, fingerprint, result, now):
        receipt = self._receipt(session, context, project_id, key, fingerprint)
        if receipt:
            receipt.response = result
        else:
            session.add(
                CoverCommandRow(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    idempotency_key=key,
                    project_id=project_id,
                    plan_id=row.id,
                    fingerprint=fingerprint,
                    response=result,
                    created_at=now,
                )
            )

    @staticmethod
    def _snapshot(row):
        return {
            "plan": plan_dto(row),
            "variants": copy.deepcopy(row.variants),
            "candidates": copy.deepcopy(row.candidates),
            "generationTaskIds": list(row.generation_task_ids),
        }

    def _evidence(self, session, context, row, action, before, now):
        event_id = str(uuid4())
        session.add(
            CoverAuditRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                id=event_id,
                project_id=row.project_id,
                plan_id=row.id,
                action=action,
                actor_id=context.actor_id,
                request_id=context.request_id,
                before=before,
                after=self._snapshot(row),
                result="succeeded",
                occurred_at=now,
            )
        )
        session.add(
            OutboxRow(
                id=event_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                event_type=f"cover.{action}",
                aggregate_type="cover_plan",
                aggregate_id=row.id,
                payload={
                    "projectId": row.project_id,
                    "planId": row.id,
                    "version": row.version,
                    "actorId": context.actor_id,
                    "requestId": context.request_id,
                },
                schema_version=1,
                created_at=now,
            )
        )

    def create(self, context, project_id, payload: CreatePlan, key):
        _require(context, "settings.manage")
        data = payload.model_dump(mode="json")
        fingerprint = self._fingerprint(project_id, None, "create", data)
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt and receipt.response:
                return receipt.response
            final = self._final(session, context, project_id, payload.finalVersionId)
            now = datetime.now(UTC)
            row = CoverPlanRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                id=str(uuid4()),
                project_id=project_id,
                name=payload.name,
                final_version_id=payload.finalVersionId,
                episode_id=final["episodeId"],
                target=payload.target,
                prompt=payload.prompt,
                aspect_ratio=payload.aspectRatio,
                title_safe_area=data["titleSafeArea"],
                candidates={},
                variants=[],
                generation_task_ids=[],
                version=1,
                created_by=context.actor_id,
                updated_by=context.actor_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            result = {"plan": plan_dto(row)}
            self._evidence(session, context, row, "create", None, now)
            self._write_receipt(session, context, project_id, row, key, fingerprint, result, now)
            return result

    @staticmethod
    def _variant(row, variant_id):
        variant = next((item for item in row.variants if item["id"] == variant_id), None)
        if variant is None:
            raise CoverFailure("COVER_VARIANT_NOT_FOUND", 404)
        return variant

    @staticmethod
    def _evaluated(variant):
        if (
            variant.get("score") is None
            or not variant.get("evaluationNote")
            or not variant.get("evaluatedBy")
            or not variant.get("title")
            or not variant.get("description")
        ):
            raise CoverFailure("COVER_VARIANT_EVALUATION_REQUIRED", 422)

    async def command(self, context, project_id, plan_id, command: Command, key):
        _require(context, "settings.manage")
        action = command.action
        payload = ACTION_MODELS[action].model_validate(command.payload).model_dump(mode="json")
        if action == "generate" and not payload.get("inputAssetReferences"):
            payload.pop("inputAssetReferences", None)
        fingerprint = self._fingerprint(project_id, plan_id, action, {"version": command.version, "payload": payload})
        if action == "generate":
            _require(context, "generation.view", "generation.manage")
        if action == "apply":
            _require(context, "generation.view", "export.manage")
        with self.session() as session:
            row = self._plan(session, context, project_id, plan_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt and receipt.response:
                return receipt.response
            if row.version != command.version:
                raise CoverFailure("COVER_VERSION_CONFLICT", 409)
            if row.pending_command and row.pending_command != self._claim_id(context, key):
                raise CoverFailure("COVER_GENERATION_PENDING", 409)
            frozen = copy.deepcopy(row.candidates)
            variant = (
                self._variant(row, row.selected_variant_id)
                if action == "apply"
                else (
                    self._variant(row, payload["variantId"])
                    if action in {"selectVariant", "recommendVariant"}
                    else None
                )
            )
            asset_id = (
                payload.get("assetId") or payload.get("coverAssetId") or (variant["coverAssetId"] if variant else None)
            )
        verified = None
        if asset_id:
            verified, _ = await self.asset(context, project_id, asset_id, frozen.get(asset_id))
        if action == "generate":
            return await self._generate(context, project_id, plan_id, command, payload, key, fingerprint)
        return await asyncio.to_thread(
            self._mutate, context, project_id, plan_id, command, payload, key, fingerprint, verified
        )

    def _mutate(self, context, project_id, plan_id, command, payload, key, fingerprint, verified):
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            row = self._plan(session, context, project_id, plan_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt and receipt.response:
                return receipt.response
            if row.version != command.version or row.pending_command:
                raise CoverFailure("COVER_VERSION_CONFLICT", 409)
            before = self._snapshot(row)
            now = datetime.now(UTC)
            action = command.action
            result = {}
            if action == "update":
                row.name = payload["name"]
                row.prompt = payload["prompt"]
                row.aspect_ratio = payload["aspectRatio"]
                row.title_safe_area = payload["titleSafeArea"]
                row.selected_variant_id = row.recommended_variant_id = row.applied_selection_id = None
                row.variants = [
                    {**item, "score": None, "evaluationNote": "", "evaluatedBy": None, "evaluatedAt": None}
                    for item in row.variants
                ]
            elif action in {"attachCandidate", "saveVariant"}:
                if verified is None:
                    raise CoverFailure("COVER_MEDIA_UNAVAILABLE", 503)
                if len(row.candidates) >= 100 and verified["assetId"] not in row.candidates:
                    raise CoverFailure("COVER_CANDIDATE_LIMIT", 422)
                row.candidates = {**row.candidates, verified["assetId"]: verified}
                if action == "saveVariant":
                    variant_id = payload["id"] or str(uuid4())
                    if payload["id"]:
                        self._variant(row, variant_id)
                    elif len(row.variants) >= 20:
                        raise CoverFailure("COVER_VARIANT_LIMIT", 422)
                    variant = {
                        **payload,
                        "id": variant_id,
                        "tags": list(dict.fromkeys(payload["tags"])),
                        "score": None,
                        "evaluationNote": "",
                        "evaluatedBy": None,
                        "evaluatedAt": None,
                    }
                    row.variants = [item for item in row.variants if item["id"] != variant_id] + [variant]
                    if row.selected_variant_id == variant_id:
                        row.selected_variant_id = row.applied_selection_id = None
                    if row.recommended_variant_id == variant_id:
                        row.recommended_variant_id = None
                    result["variant"] = variant
            elif action == "evaluateVariant":
                variant = {
                    **self._variant(row, payload["variantId"]),
                    "score": payload["score"],
                    "evaluationNote": payload["note"],
                    "evaluatedBy": context.actor_id,
                    "evaluatedAt": _iso(now),
                }
                row.variants = [variant if item["id"] == variant["id"] else item for item in row.variants]
                if payload["score"] is None:
                    if row.selected_variant_id == variant["id"]:
                        row.selected_variant_id = row.applied_selection_id = None
                    if row.recommended_variant_id == variant["id"]:
                        row.recommended_variant_id = None
                result["variant"] = variant
            elif action in {"recommendVariant", "selectVariant", "apply"}:
                variant = self._variant(row, row.selected_variant_id if action == "apply" else payload["variantId"])
                self._evaluated(variant)
                if verified is None:
                    raise CoverFailure("COVER_MEDIA_UNAVAILABLE", 503)
                if action == "recommendVariant":
                    row.recommended_variant_id = variant["id"]
                elif action == "selectVariant":
                    row.selected_variant_id = variant["id"]
                    row.applied_selection_id = None
                else:
                    final = self._final(session, context, project_id, row.final_version_id)
                    if final["episodeId"] != row.episode_id:
                        raise CoverFailure("COVER_FINAL_EPISODE_CHANGED", 409)
                    selection_id = str(uuid4())
                    snapshot = {
                        "schema": "xingjing.release-creative/v1",
                        "selectionId": selection_id,
                        "planId": row.id,
                        "planVersion": row.version + 1,
                        "variantId": variant["id"],
                        "finalVersionId": row.final_version_id,
                        "episodeId": row.episode_id,
                        "target": row.target,
                        "title": variant["title"],
                        "description": variant["description"],
                        "tags": variant["tags"],
                        "aspectRatio": row.aspect_ratio,
                        "titleSafeArea": row.title_safe_area,
                        "cover": verified,
                    }
                    session.add(
                        CoverSelectionRow(
                            tenant_id=context.tenant_id,
                            workspace_id=context.workspace_id,
                            id=selection_id,
                            project_id=project_id,
                            final_version_id=row.final_version_id,
                            episode_id=row.episode_id,
                            target=row.target,
                            plan_id=row.id,
                            plan_version=row.version + 1,
                            variant_id=variant["id"],
                            snapshot=snapshot,
                            created_by=context.actor_id,
                            created_at=now,
                        )
                    )
                    row.applied_selection_id = selection_id
                    result["selection"] = {"id": selection_id, "snapshot": snapshot}
            row.version += 1
            row.updated_by, row.updated_at = context.actor_id, now
            result["plan"] = plan_dto(row)
            self._evidence(session, context, row, action, before, now)
            self._write_receipt(session, context, project_id, row, key, fingerprint, result, now)
            return result

    @staticmethod
    def _claim_id(context, key):
        return hashlib.sha256(f"{context.actor_id}:{key}".encode()).hexdigest()

    async def _generate(self, context, project_id, plan_id, command, payload, key, fingerprint):
        claim_id = self._claim_id(context, key)
        submission_key = (
            "cover:"
            + hashlib.sha256(
                f"{context.tenant_id}:{context.workspace_id}:{context.actor_id}:{key}".encode()
            ).hexdigest()
        )
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            row = self._plan(session, context, project_id, plan_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt and receipt.response:
                return receipt.response
            if row.version != command.version or row.pending_command not in {None, claim_id}:
                raise CoverFailure("COVER_VERSION_CONFLICT", 409)
            if len(row.generation_task_ids) >= 30:
                raise CoverFailure("COVER_GENERATION_LIMIT", 422)
            parameters = dict(payload["parameters"])
            if parameters.get("aspect_ratio", row.aspect_ratio) != row.aspect_ratio:
                raise CoverFailure("COVER_GENERATION_ASPECT_MISMATCH", 422)
            parameters["aspect_ratio"] = row.aspect_ratio
            request = GenerationRequest.model_validate(
                {
                    "workspace_id": context.workspace_id,
                    "project_id": project_id,
                    "media_type": "image",
                    "capability": "image_generation",
                    "prompt": payload["prompt"] or row.prompt,
                    "parameters": parameters,
                    "input_asset_ids": payload["inputAssetIds"],
                    "input_asset_references": payload.get("inputAssetReferences", []),
                    "requested_provider_id": payload["providerId"],
                    "requested_model_id": payload["modelId"],
                }
            )
            row.pending_command = claim_id
            if receipt is None:
                session.add(
                    CoverCommandRow(
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        actor_id=context.actor_id,
                        idempotency_key=key,
                        project_id=project_id,
                        plan_id=plan_id,
                        fingerprint=fingerprint,
                        response=None,
                        created_at=datetime.now(UTC),
                    )
                )
        repository = self.generation.repository
        if repository is None:
            await asyncio.to_thread(self._release_claim, context, project_id, plan_id, key, fingerprint)
            raise GenerationRuntimeUnavailable(self.generation.unavailable_code or "GENERATION_RUNTIME_UNAVAILABLE")
        task = await repository.get_by_idempotency_scope(
            f"{context.workspace_id}:{submission_key}", project_id=project_id
        )
        if task is not None and (
            task.request.media_type != request.media_type
            or task.request.capability != request.capability
            or task.request.prompt != request.prompt
            or task.request.parameters != request.parameters
            or task.request.input_asset_ids != request.input_asset_ids
            or task.request.input_asset_references != request.input_asset_references
            or request.requested_provider_id is not None
            and task.request.requested_provider_id != request.requested_provider_id
            or request.requested_model_id is not None
            and task.request.requested_model_id != request.requested_model_id
        ):
            await asyncio.to_thread(self._release_claim, context, project_id, plan_id, key, fingerprint)
            raise CoverFailure("COVER_GENERATION_IDEMPOTENCY_CONFLICT", 409)
        try:
            if task is None:
                task = await self.generation.submit(context, project_id, request, idempotency_key=submission_key)
        except Exception:
            accepted = await repository.get_by_idempotency_scope(
                f"{context.workspace_id}:{submission_key}", project_id=project_id
            )
            if accepted is None:
                await asyncio.to_thread(self._release_claim, context, project_id, plan_id, key, fingerprint)
            raise
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            row = self._plan(session, context, project_id, plan_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt and receipt.response:
                return receipt.response
            if row.version != command.version or row.pending_command != claim_id:
                raise CoverFailure("COVER_VERSION_CONFLICT", 409)
            before = self._snapshot(row)
            now = datetime.now(UTC)
            row.generation_task_ids = list(dict.fromkeys([*row.generation_task_ids, task.task_id]))
            row.pending_command = None
            row.version += 1
            row.updated_by, row.updated_at = context.actor_id, now
            result = {"plan": plan_dto(row), "task": task.model_dump(mode="json")}
            self._evidence(session, context, row, "generate", before, now)
            self._write_receipt(session, context, project_id, row, key, fingerprint, result, now)
            return result

    def _release_claim(self, context, project_id, plan_id, key, fingerprint):
        with self.session() as session, session.begin():
            self._lock_project(session, context, project_id)
            row = self._plan(session, context, project_id, plan_id)
            receipt = self._receipt(session, context, project_id, key, fingerprint)
            if receipt and receipt.response is None and row.pending_command == self._claim_id(context, key):
                row.pending_command = None
                session.delete(receipt)


def create_production_cover_runtime(
    *, generation_runtime: GenerationRuntime, database_url: str | None = None, context_resolver=None
) -> CoverRuntime:
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    url = database_url if database_url is not None else os.environ.get("XINGJING_COVER_DATABASE_URL", "").strip()
    if not url:
        return CoverRuntime(None, resolver, generation_runtime, unavailable_code="XINGJING_COVER_DATABASE_URL_REQUIRED")
    try:
        if make_url(url).get_driver_name() in {"asyncpg", "aiosqlite", "asyncmy", "aiomysql"}:
            raise ValueError("synchronous database URL required")
        engine = create_engine(url, pool_pre_ping=True)
    except (ValueError, ModuleNotFoundError, SQLAlchemyError):
        return CoverRuntime(None, resolver, generation_runtime, unavailable_code="COVER_DATABASE_UNAVAILABLE")
    return CoverRuntime(
        sessionmaker(engine, expire_on_commit=False), resolver, generation_runtime, close=engine.dispose
    )
