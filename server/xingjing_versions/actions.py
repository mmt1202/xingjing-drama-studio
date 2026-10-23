from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from server.xingjing_assets.contracts import Asset
from server.xingjing_assets.errors import AssetDomainError, AssetNotFound
from server.xingjing_assets.service import AssetService
from server.xingjing_assets_persistence.repository import AssetAuditEvent, AssetScope, SqlAlchemyAssetRepository
from server.xingjing_content.models import ScriptDocument, script_from_dict
from server.xingjing_content.repository import FileContentRepository
from server.xingjing_content.service import ContentConflict, ContentService
from server.xingjing_content_persistence.repository import (
    AuditContext,
    ContentAuditRow,
    SqlAlchemyContentRepository,
)
from server.xingjing_content_persistence.repository import (
    IdempotencyRow as ScriptReceiptRow,
)
from server.xingjing_editing.contracts import AccessContext as EditingContext
from server.xingjing_editing.contracts import Permission, canonical_sha256, validate_idempotency_key
from server.xingjing_editing.errors import EditingError
from server.xingjing_editing.ids import Uuid7IdGenerator
from server.xingjing_editing.render_service import FinalVersionSelectionService
from server.xingjing_editing_persistence.models import FinalVideoSelectionReceiptRow, FinalVideoVersionRow
from server.xingjing_editing_persistence.repositories import SqlAlchemyAuditRecorder, SqlAlchemyRenderRepository
from server.xingjing_identity_context.trusted_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence.persistence import ProjectRow
from server.xingjing_storyboard.errors import StoryboardError
from server.xingjing_storyboard.models import Storyboard, StoryboardStatus
from server.xingjing_storyboard.ports import StoryboardScope
from server.xingjing_storyboard.quality import QualityPolicy
from server.xingjing_storyboard.service import AccessContext as StoryboardContext
from server.xingjing_storyboard.service import StoryboardService
from server.xingjing_storyboard_persistence.repository import IdempotencyRow as StoryboardReceiptRow
from server.xingjing_storyboard_persistence.repository import SqlAlchemyStoryboardRepository
from server.xingjing_storyboard_runtime.runtime import PersistentAssetReferenceValidator


class VersionActionFailure(ValueError):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class VersionActions:
    """Routes already project-authorized commands to each domain's real write service.

    The caller claims the command and verifies its source digest before execution.
    A resumed claim keeps the original domain key so its durable receipt can recover
    the applied version even when the live aggregate has changed afterward.
    """

    def __init__(
        self,
        sessions: Callable[[], Session],
        async_sessions: async_sessionmaker[AsyncSession] | None = None,
        *,
        quality_policy: QualityPolicy | None = None,
    ) -> None:
        self._sessions = sessions
        self._async_sessions = async_sessions
        self._quality_policy = quality_policy

    async def execute(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        ref: Mapping[str, Any],
        action: str,
        expected_revision: int,
        key: str,
        reason: str,
    ) -> dict[str, Any]:
        kind, object_id, version_id, shot_id = _reference(ref)
        if action not in {"lock", "restore"}:
            raise VersionActionFailure("VERSION_ACTION_UNSUPPORTED", 400)
        if type(expected_revision) is not int or expected_revision < (0 if kind == "final" else 1):
            raise VersionActionFailure("INVALID_EXPECTED_REVISION", 400)
        if not reason.strip() or len(reason) > 1000:
            raise VersionActionFailure("VERSION_ACTION_NOTE_REQUIRED", 400)
        validate_idempotency_key(key)
        permission_kind = "shot" if kind == "storyboard" else kind
        if not {f"{permission_kind}.view", f"{permission_kind}.manage"}.issubset(context.permissions):
            raise VersionActionFailure("PERMISSION_DENIED", 403)
        await asyncio.to_thread(self._require_project, context, project_id)
        if action == "lock" and kind not in {"script", "storyboard"}:
            raise VersionActionFailure("VERSION_LOCK_UNSUPPORTED", 409)
        try:
            if kind == "script":
                return await asyncio.to_thread(
                    self._script, context, project_id, object_id, version_id, action, expected_revision, key, reason
                )
            if kind == "asset":
                return await asyncio.to_thread(
                    self._asset, context, project_id, object_id, version_id, expected_revision, key, reason
                )
            if kind in {"storyboard", "shot"}:
                return await asyncio.to_thread(
                    self._storyboard,
                    context,
                    project_id,
                    object_id,
                    version_id,
                    shot_id,
                    action,
                    expected_revision,
                    key,
                    reason,
                )
            return await self._final(context, project_id, object_id, version_id, expected_revision, key)
        except VersionActionFailure:
            raise
        except EditingError as error:
            raise VersionActionFailure(error.code, error.status_code) from error
        except StoryboardError as error:
            status = 400
            if error.code in {
                "VERSION_CONFLICT",
                "IDEMPOTENCY_KEY_REUSED",
                "IDEMPOTENCY_CONFLICT",
                "STORYBOARD_FROZEN",
                "QUALITY_GATE_FAILED",
            }:
                status = 409
            elif error.code == "PERMISSION_DENIED":
                status = 403
            elif error.code.endswith("NOT_FOUND"):
                status = 404
            elif error.code in {"QUALITY_POLICY_REQUIRED", "ASSET_REFERENCE_VALIDATION_UNAVAILABLE"}:
                status = 503
            raise VersionActionFailure(error.code, status) from error
        except ContentConflict as error:
            raise VersionActionFailure(str(error), 409) from error
        except AssetNotFound as error:
            raise VersionActionFailure("ASSET_VERSION_NOT_FOUND", 404) from error
        except AssetDomainError as error:
            raise VersionActionFailure(str(error), 409) from error
        except KeyError as error:
            raise VersionActionFailure(str(error.args[0]), 404) from error
        except ValueError as error:
            code = str(error)
            status = 409 if code in {"SCRIPT_FROZEN", "VERSION_CONFLICT", "IDEMPOTENCY_KEY_REUSED"} else 400
            raise VersionActionFailure(code, status) from error

    def _require_project(self, context: TrustedWorkspaceContext, project_id: str) -> None:
        with self._sessions() as session:
            found = session.scalar(
                select(ProjectRow.id).where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == context.tenant_id,
                    ProjectRow.workspace_id == context.workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
            )
        if found is None:
            raise VersionActionFailure("PROJECT_NOT_FOUND", 404)

    def _script(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        object_id: str,
        version_id: str,
        action: str,
        expected_revision: int,
        key: str,
        reason: str,
    ) -> dict[str, Any]:
        source_version = _number(version_id)
        repository = SqlAlchemyContentRepository(
            self._sessions,
            audit_context=AuditContext(
                request_id=context.request_id,
                actor_id=context.actor_id,
                occurred_at=datetime.now(UTC),
                action=f"content.script.version_{action}",
            ),
        )
        service = ContentService(cast(FileContentRepository, repository))
        current = service.get_script(workspace_id=context.workspace_id, script_id=object_id)
        if current.project_id != project_id:
            raise VersionActionFailure("SCRIPT_NOT_FOUND", 404)
        if not any(item.number == source_version for item in current.versions):
            raise VersionActionFailure("SCRIPT_VERSION_NOT_FOUND", 404)
        with self._sessions() as session:
            replayed = session.get(ScriptReceiptRow, (context.workspace_id, key)) is not None
        if action == "lock":
            if not replayed and current.current_version.number != source_version:
                raise VersionActionFailure("VERSION_LOCK_REQUIRES_CURRENT", 409)
            if not replayed and current.locked_version_number is not None:
                raise VersionActionFailure("SCRIPT_FROZEN", 409)
            service.freeze_script(
                workspace_id=context.workspace_id,
                script_id=object_id,
                expected_revision=expected_revision,
                idempotency_key=key,
            )
        else:
            service.restore_script_version(
                workspace_id=context.workspace_id,
                script_id=object_id,
                source_version_number=source_version,
                expected_revision=expected_revision,
                idempotency_key=key,
                reason=reason,
            )
        # The content receipt resolves the live document. Its atomic audit retains
        # the original result and prevents an interrupted command returning a later edit.
        with self._sessions() as session:
            audit = session.scalar(
                select(ContentAuditRow).where(
                    ContentAuditRow.workspace_id == context.workspace_id,
                    ContentAuditRow.project_id == project_id,
                    ContentAuditRow.script_id == object_id,
                    ContentAuditRow.actor_id == context.actor_id,
                    ContentAuditRow.event_id.endswith(f":{key}", autoescape=True),
                )
            )
            if audit is None:
                raise VersionActionFailure("VERSION_RECEIPT_INCOMPLETE", 503)
            applied: ScriptDocument = script_from_dict(audit.after_payload)
        return _result(
            "script", object_id, str(applied.current_version.number), applied.revision, replayed, f"script.{action}"
        )

    def _asset(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        object_id: str,
        version_id: str,
        expected_revision: int,
        key: str,
        reason: str,
    ) -> dict[str, Any]:
        repository = SqlAlchemyAssetRepository(self._sessions)
        scope = AssetScope(context.tenant_id, context.workspace_id)
        current = repository.get(scope, object_id)
        if current is None or current.owner_project_id != project_id:
            raise VersionActionFailure("ASSET_NOT_FOUND", 404)
        if not any(item.version_id == version_id for item in current.versions):
            raise VersionActionFailure("ASSET_VERSION_NOT_FOUND", 404)
        fingerprint = canonical_sha256(
            {
                "action": "asset.restore",
                "project": project_id,
                "asset": object_id,
                "source": version_id,
                "expected_revision": expected_revision,
                "reason": reason,
            }
        )
        replay = repository.replay(scope, idempotency_key=key, fingerprint=fingerprint)
        if replay is not None:
            return _result("asset", object_id, replay.current_version_id, replay.revision, True, "asset.restore")
        bound = _AssetCommand(repository, scope, context, current, key, fingerprint, reason)
        AssetService(bound, _ShotImpacts(self._sessions, context, project_id)).rollback(
            workspace_id=context.workspace_id,
            asset_id=object_id,
            target_version_id=version_id,
            expected_revision=expected_revision,
        )
        if bound.stored is None:
            raise VersionActionFailure("VERSION_RECEIPT_INCOMPLETE", 503)
        return _result(
            "asset", object_id, bound.stored.current_version_id, bound.stored.revision, bound.replayed, "asset.restore"
        )

    def _storyboard(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        object_id: str,
        version_id: str,
        shot_id: str | None,
        action: str,
        expected_revision: int,
        key: str,
        reason: str,
    ) -> dict[str, Any]:
        source_version = _number(version_id)
        board_context = StoryboardContext(
            context.tenant_id,
            context.workspace_id,
            project_id,
            context.actor_id,
            context.request_id,
            context.permissions,
        )
        repository = SqlAlchemyStoryboardRepository(self._sessions)
        current = repository.get(board_context.scope, object_id)
        if current is None:
            raise VersionActionFailure("STORYBOARD_NOT_FOUND", 404)
        source = next((item for item in current.versions if item.number == source_version), None)
        if source is None:
            raise VersionActionFailure("STORYBOARD_VERSION_NOT_FOUND", 404)
        if shot_id is not None and not any(item.shot_id == shot_id for item in source.shots):
            raise VersionActionFailure("VERSIONED_SHOT_NOT_FOUND", 404)
        if action == "lock":
            if source_version != expected_revision:
                raise VersionActionFailure("VERSION_LOCK_REQUIRES_CURRENT", 409)
            recovered = self._frozen_receipt(board_context, object_id, expected_revision, key)
            if recovered is not None:
                return _result(
                    "storyboard", object_id, str(recovered.version), recovered.version, True, "storyboard.lock"
                )
        service = StoryboardService(
            repository,
            PersistentAssetReferenceValidator(SqlAlchemyAssetRepository(self._sessions)),
            quality_policy=self._quality_policy,
        )
        if action == "lock":
            outcome = service.freeze_storyboard(
                board_context,
                storyboard_id=object_id,
                expected_version=expected_revision,
                idempotency_key=key,
            )
        elif shot_id is not None:
            outcome = service.restore_shot_version(
                board_context,
                storyboard_id=object_id,
                expected_version=expected_revision,
                shot_id=shot_id,
                source_version=source_version,
                idempotency_key=key,
            )
        else:
            outcome = service.restore_storyboard_version(
                board_context,
                storyboard_id=object_id,
                expected_version=expected_revision,
                source_version=source_version,
                idempotency_key=key,
                reason=reason,
            )
        return _result(
            "shot" if shot_id is not None else "storyboard",
            object_id,
            str(outcome.storyboard.version),
            outcome.storyboard.version,
            outcome.replayed,
            f"{'shot' if shot_id is not None else 'storyboard'}.{action}",
            shot_id=shot_id,
        )

    def _frozen_receipt(
        self,
        context: StoryboardContext,
        object_id: str,
        expected_revision: int,
        key: str,
    ) -> Storyboard | None:
        # The outer command claim has already bound this stable domain key to the
        # exact request. Recover a completed freeze before loading today's policy.
        with self._sessions() as session:
            receipt = session.get(
                StoryboardReceiptRow,
                (
                    context.tenant_id,
                    context.workspace_id,
                    context.project_id,
                    "storyboard",
                    key,
                ),
            )
            if receipt is None:
                return None
            applied = Storyboard.from_dict(receipt.result_payload)
        last = applied.versions[-1] if applied.versions else None
        snapshot = applied.frozen_snapshot
        if (
            (applied.tenant_id, applied.workspace_id, applied.project_id, applied.storyboard_id)
            != (
                context.tenant_id,
                context.workspace_id,
                context.project_id,
                object_id,
            )
            or applied.version != expected_revision + 1
            or applied.status is not StoryboardStatus.FROZEN
            or last is None
            or last.action != "storyboard.frozen"
            or last.actor_id != context.actor_id
            or snapshot is None
            or snapshot.source_version != applied.version
            or snapshot.shots != applied.shots
            or last.shots != applied.shots
        ):
            raise VersionActionFailure("IDEMPOTENCY_KEY_REUSED", 409)
        return applied

    async def _final(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        object_id: str,
        version_id: str,
        expected_revision: int,
        key: str,
    ) -> dict[str, Any]:
        if self._async_sessions is None:
            raise VersionActionFailure("VERSION_WRITE_RUNTIME_UNAVAILABLE", 503)
        async with self._async_sessions() as session:
            source = await session.scalar(
                select(FinalVideoVersionRow.version_id).where(
                    FinalVideoVersionRow.tenant_id == context.tenant_id,
                    FinalVideoVersionRow.workspace_id == context.workspace_id,
                    FinalVideoVersionRow.project_id == project_id,
                    FinalVideoVersionRow.final_video_id == object_id,
                    FinalVideoVersionRow.version_id == version_id,
                )
            )
            replayed = (
                await session.get(FinalVideoSelectionReceiptRow, (context.tenant_id, context.workspace_id, key))
                is not None
            )
        if source is None:
            raise VersionActionFailure("FINAL_VERSION_NOT_FOUND", 404)
        service = FinalVersionSelectionService(
            repository=SqlAlchemyRenderRepository(self._async_sessions),
            audit=SqlAlchemyAuditRecorder(self._async_sessions),
            ids=Uuid7IdGenerator(),
            clock=_UtcClock(),
        )
        outcome = await service.select_final_version(
            EditingContext(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                permissions=frozenset(Permission(item) for item in context.permissions if item in Permission),
            ),
            project_id=project_id,
            final_video_id=object_id,
            version_id=version_id,
            expected_revision=expected_revision,
            idempotency_key=key,
        )
        return _result("final", object_id, outcome.selected_version_id, outcome.revision, replayed, "final.restore")


class _AssetCommand:
    def __init__(
        self,
        repository: SqlAlchemyAssetRepository,
        scope: AssetScope,
        context: TrustedWorkspaceContext,
        before: Asset,
        key: str,
        fingerprint: str,
        reason: str,
    ) -> None:
        self._repository, self._scope, self._context = repository, scope, context
        self._before, self._key, self._fingerprint, self._reason = before, key, fingerprint, reason
        self.stored: Asset | None = None
        self.replayed = False

    def get(self, workspace_id: str, asset_id: str) -> Asset | None:
        if workspace_id != self._scope.workspace_id or asset_id != self._before.asset_id:
            return None
        return self._repository.get(self._scope, asset_id)

    def save(self, asset: Asset, *, expected_revision: int | None) -> None:
        if asset.owner_project_id != self._before.owner_project_id or asset.asset_id != self._before.asset_id:
            raise VersionActionFailure("ASSET_NOT_FOUND", 404)
        self.stored, self.replayed = self._repository.commit(
            self._scope,
            asset,
            expected_revision=expected_revision,
            idempotency_key=self._key,
            fingerprint=self._fingerprint,
            audit=AssetAuditEvent(
                request_id=self._context.request_id,
                actor_id=self._context.actor_id,
                action="asset.version_restored",
                asset_id=asset.asset_id,
                result="succeeded",
                occurred_at=datetime.now(UTC),
                before_payload=self._before.to_dict(),
                after_payload=cast(dict[str, object], asset.to_dict() | {"restore_reason": self._reason}),
            ),
        )


class _ShotImpacts:
    def __init__(self, sessions: Callable[[], Session], context: TrustedWorkspaceContext, project_id: str) -> None:
        self._boards = SqlAlchemyStoryboardRepository(sessions)
        self._scope = StoryboardScope(context.tenant_id, context.workspace_id, project_id)

    def affected_shot_ids(self, *, workspace_id: str, project_id: str, asset_id: str) -> tuple[str, ...]:
        if workspace_id != self._scope.workspace_id or project_id != self._scope.project_id:
            return ()
        return tuple(
            shot.shot_id
            for board in self._boards.list(self._scope)
            for shot in board.shots
            if any(ref.asset_id == asset_id for ref in shot.asset_references)
        )


class _UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _number(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise VersionActionFailure("INVALID_VERSION_REFERENCE", 400)
    return int(value)


def _reference(ref: Mapping[str, Any]) -> tuple[str, str, str, str | None]:
    kind, object_id, version_id, shot_id = (ref.get(item) for item in ("kind", "objectId", "versionId", "shotId"))
    if (
        not isinstance(kind, str)
        or kind not in {"script", "asset", "shot", "storyboard", "final"}
        or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in (object_id, version_id))
    ):
        raise VersionActionFailure("INVALID_VERSION_REFERENCE", 400)
    if kind == "shot":
        if not isinstance(shot_id, str) or not shot_id.strip() or len(shot_id) > 128:
            raise VersionActionFailure("INVALID_VERSION_REFERENCE", 400)
    elif shot_id is not None:
        raise VersionActionFailure("INVALID_VERSION_REFERENCE", 400)
    return cast(str, kind), cast(str, object_id), cast(str, version_id), cast(str | None, shot_id)


def _result(
    kind: str,
    object_id: str,
    version_id: str,
    revision: int,
    replayed: bool,
    domain_action: str,
    *,
    shot_id: str | None = None,
) -> dict[str, Any]:
    ref = {"kind": kind, "objectId": object_id, "versionId": version_id}
    if shot_id is not None:
        ref["shotId"] = shot_id
    return {"ref": ref, "revision": revision, "replayed": replayed, "domainAction": domain_action}
