"""Bounded projections of SQL-backed source versions and sanitized business diffs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.xingjing_analytics.database import identity
from server.xingjing_assets_persistence.repository import AssetRow, AssetVersionRow
from server.xingjing_assets_persistence.repository import AuditRow as AssetAuditRow
from server.xingjing_content_persistence.repository import (
    ContentAuditRow,
    ScriptParagraphRow,
    ScriptRow,
    ScriptSceneRow,
    ScriptVersionRow,
    SourceMappingRow,
)
from server.xingjing_editing_persistence.models import FinalVideoSelectionRow, FinalVideoVersionRow, TimelineVersionRow
from server.xingjing_storyboard.models import Storyboard
from server.xingjing_storyboard_persistence.repository import StoryboardRow

from .contracts import (
    KINDS,
    MANAGE_PERMISSIONS,
    MAX_CHANGES,
    MAX_CONTENT_BYTES,
    SCAN_LIMIT,
    SOURCE_DATABASES,
    VIEW_PERMISSIONS,
    Ref,
    VersionFailure,
)


def iso(value: datetime | str | None) -> str | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def safe_content(value: Any, depth: int = 0) -> Any:
    if depth > 40:
        raise VersionFailure("VERSION_CONTENT_DEPTH_LIMIT", 422)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[_\-]", "", str(key)).casefold()
            if any(
                word in normalized
                for word in (
                    "objectkey",
                    "filepath",
                    "storagepath",
                    "secret",
                    "password",
                    "credential",
                    "accesstoken",
                    "refreshtoken",
                    "apikey",
                    "bearer",
                )
            ) or normalized.endswith(("url", "uri", "path")):
                continue
            result[str(key)] = safe_content(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > SCAN_LIMIT:
            raise VersionFailure("VERSION_CONTENT_COLLECTION_LIMIT", 422)
        return [safe_content(item, depth + 1) for item in value]
    if isinstance(value, str) and (
        re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*://|//|[a-zA-Z]:[\\/]|/[^\s])", value)
        or re.match(r"^[^\s]+/[^\s]+\.(?:png|jpg|jpeg|webp|mp4|mov|wav|mp3)(?:\?.*)?$", value, re.I)
    ):
        return None
    return value


def scoped(model, context):
    return model.tenant_id == context.tenant_id, model.workspace_id == context.workspace_id


def check_source(session: Session, *variables: str) -> None:
    import os

    for variable in variables:
        configured = os.environ.get(variable, "").strip()
        if configured:
            try:
                if identity(session.get_bind().engine.url) != identity(configured):
                    raise ValueError("different source database")
            except (ValueError, TypeError) as error:
                raise VersionFailure("VERSION_SOURCE_DATABASE_MISMATCH", 503) from error


@dataclass
class Entry:
    node: dict[str, Any]
    content: dict[str, Any]


class VersionReader:
    def __init__(self, *, quality_policy_available: bool = False, final_selection_available: bool = True):
        self.quality_policy_available = quality_policy_available
        self.final_selection_available = final_selection_available

    def entries(
        self,
        session: Session,
        context,
        project_id: str,
        *,
        kind: str | None = None,
        object_id: str | None = None,
        shot_id: str | None = None,
    ) -> list[Entry]:
        kinds = [kind] if kind else [item for item in KINDS if VIEW_PERMISSIONS[item] in context.permissions]
        result: list[Entry] = []
        budget = 0
        for source_kind in kinds:
            if VIEW_PERMISSIONS[source_kind] not in context.permissions:
                raise VersionFailure("VERSION_PERMISSION_DENIED", 403)
            check_source(session, SOURCE_DATABASES[source_kind])
            load = {
                "script": self._scripts,
                "asset": self._assets,
                "shot": self._boards,
                "storyboard": self._boards,
                "final": self._finals,
            }[source_kind]
            for item in load(session, context, project_id, source_kind, object_id, shot_id):
                size = len(canonical(item.content))
                budget += size
                if size > MAX_CONTENT_BYTES or budget > 32 * 1024 * 1024 or len(result) >= SCAN_LIMIT:
                    raise VersionFailure("VERSION_SOURCE_SCAN_LIMIT", 422)
                item.content = safe_content(item.content)
                result.append(item)
        return sorted(
            result,
            key=lambda item: (
                item.node["createdAt"] or "",
                item.node["ref"]["kind"],
                item.node["ref"]["objectId"],
                item.node["ref"].get("shotId", ""),
                item.node["versionNumber"],
                item.node["ref"]["versionId"],
            ),
            reverse=True,
        )

    def get(self, session, context, project_id, ref: Ref) -> Entry:
        entries = self.entries(session, context, project_id, kind=ref.kind, object_id=ref.objectId, shot_id=ref.shotId)
        entry = next((item for item in entries if item.node["ref"] == ref.dto()), None)
        if entry is None:
            raise VersionFailure("VERSION_NOT_FOUND", 404)
        return entry

    def assert_scope(self, session, context, project_id, ref: Ref) -> None:
        if VIEW_PERMISSIONS[ref.kind] not in context.permissions:
            raise VersionFailure("VERSION_PERMISSION_DENIED", 403)
        check_source(session, SOURCE_DATABASES[ref.kind])
        if ref.kind == "script":
            query = select(ScriptRow.script_id).where(
                ScriptRow.workspace_id == context.workspace_id,
                ScriptRow.project_id == project_id,
                ScriptRow.script_id == ref.objectId,
            )
        elif ref.kind == "asset":
            query = select(AssetRow.asset_id).where(
                *scoped(AssetRow, context), AssetRow.owner_project_id == project_id, AssetRow.asset_id == ref.objectId
            )
        elif ref.kind in {"shot", "storyboard"}:
            query = select(StoryboardRow.storyboard_id).where(
                *scoped(StoryboardRow, context),
                StoryboardRow.project_id == project_id,
                StoryboardRow.storyboard_id == ref.objectId,
            )
        else:
            query = select(FinalVideoVersionRow.version_id).where(
                *scoped(FinalVideoVersionRow, context),
                FinalVideoVersionRow.project_id == project_id,
                FinalVideoVersionRow.final_video_id == ref.objectId,
                FinalVideoVersionRow.version_id == ref.versionId,
            )
        if session.scalar(query.limit(1)) is None:
            raise VersionFailure("VERSION_NOT_FOUND", 404)

    @staticmethod
    def _rows(session, statement):
        count = 0
        for row in session.scalars(statement.limit(SCAN_LIMIT + 1).execution_options(yield_per=25)):
            count += 1
            if count > SCAN_LIMIT:
                raise VersionFailure("VERSION_SOURCE_SCAN_LIMIT", 422)
            yield row

    @staticmethod
    def _node(
        context,
        ref,
        label,
        number,
        head_revision,
        current,
        locked,
        content,
        module,
        source_id,
        *,
        created_at=None,
        actor_id=None,
        summary="",
        episode_id=None,
        parent=None,
        mutable=True,
        restorable=True,
        lock_available=True,
        reason=None,
    ):
        manage = MANAGE_PERMISSIONS[ref["kind"]] in context.permissions
        can_restore = manage and restorable and mutable and not current
        can_lock = manage and ref["kind"] in {"script", "storyboard"} and current and mutable and lock_available
        actions = {
            "canSnapshot": "project.manage" in context.permissions,
            "canLock": can_lock,
            "canRestore": can_restore,
        }
        if reason:
            actions["reason"] = reason
        elif not manage:
            actions["reason"] = "VERSION_PERMISSION_DENIED"
        elif not mutable:
            actions["reason"] = "VERSION_SOURCE_LOCKED"
        elif current:
            actions["reason"] = "VERSION_ALREADY_CURRENT"
        return {
            "ref": ref,
            "label": label,
            "versionNumber": number,
            "headRevision": head_revision,
            "current": bool(current),
            "locked": bool(locked),
            "immutable": True,
            "createdAt": iso(created_at),
            "actorId": actor_id,
            "summary": summary,
            "episodeId": episode_id,
            "contentDigest": digest(content),
            "source": {"module": module, "sourceId": source_id, **({"parentVersionId": parent} if parent else {})},
            "actions": actions,
        }

    def _scripts(self, session, context, project_id, kind, object_id, shot_id):
        statement = select(ScriptRow).where(
            ScriptRow.workspace_id == context.workspace_id, ScriptRow.project_id == project_id
        )
        if object_id:
            statement = statement.where(ScriptRow.script_id == object_id)
        for head in self._rows(session, statement):
            provenance = {}
            for audit in self._rows(
                session,
                select(ContentAuditRow)
                .where(
                    ContentAuditRow.workspace_id == context.workspace_id,
                    ContentAuditRow.project_id == project_id,
                    ContentAuditRow.script_id == head.script_id,
                    ContentAuditRow.result.in_(["succeeded", "success"]),
                )
                .order_by(ContentAuditRow.occurred_at, ContentAuditRow.event_id),
            ):
                before_versions = audit.before_payload.get("versions", [])
                after_versions = audit.after_payload.get("versions", [])
                if not isinstance(before_versions, list) or not isinstance(after_versions, list):
                    continue
                before_numbers = {item.get("number") for item in before_versions if isinstance(item, dict)}
                introduced = [
                    item
                    for item in after_versions
                    if isinstance(item, dict) and item.get("number") not in before_numbers
                ]
                if len(introduced) == 1 and len(after_versions) == len(before_versions) + 1:
                    version = introduced[0]
                    provenance.setdefault(
                        version.get("number"), (audit.actor_id, audit.occurred_at, version.get("source_content"))
                    )
            scenes = list(
                self._rows(
                    session,
                    select(ScriptSceneRow)
                    .where(
                        ScriptSceneRow.workspace_id == context.workspace_id, ScriptSceneRow.script_id == head.script_id
                    )
                    .order_by(ScriptSceneRow.version_number, ScriptSceneRow.sequence),
                )
            )
            paragraphs = list(
                self._rows(
                    session,
                    select(ScriptParagraphRow)
                    .where(
                        ScriptParagraphRow.workspace_id == context.workspace_id,
                        ScriptParagraphRow.script_id == head.script_id,
                    )
                    .order_by(
                        ScriptParagraphRow.version_number,
                        ScriptParagraphRow.scene_sequence,
                        ScriptParagraphRow.sequence,
                    ),
                )
            )
            mappings = list(
                self._rows(
                    session,
                    select(SourceMappingRow)
                    .where(
                        SourceMappingRow.workspace_id == context.workspace_id,
                        SourceMappingRow.script_id == head.script_id,
                    )
                    .order_by(SourceMappingRow.version_number, SourceMappingRow.target_id),
                )
            )
            versions = list(
                self._rows(
                    session,
                    select(ScriptVersionRow)
                    .where(
                        ScriptVersionRow.workspace_id == context.workspace_id,
                        ScriptVersionRow.script_id == head.script_id,
                    )
                    .order_by(ScriptVersionRow.number),
                )
            )
            if not versions:
                raise VersionFailure("VERSION_SOURCE_INVALID", 503)
            latest = versions[-1].number
            for row in versions:
                fact = provenance.get(row.number)
                if fact is not None and fact[2] != row.source_content:
                    fact = None
                content = {
                    "sourceContent": row.source_content,
                    "analysis": row.analysis,
                    "validationErrors": row.validation_errors,
                    "scenes": [
                        {
                            "scene_id": scene.scene_id,
                            "sequence": scene.sequence,
                            "heading": scene.heading,
                            "paragraphs": [
                                {
                                    "paragraph_id": p.paragraph_id,
                                    "sequence": p.sequence,
                                    "kind": p.kind,
                                    "text": p.text,
                                    "source_text": p.source_text,
                                    "speaker": p.speaker,
                                }
                                for p in paragraphs
                                if p.version_number == row.number and p.scene_sequence == scene.sequence
                            ],
                        }
                        for scene in scenes
                        if scene.version_number == row.number
                    ],
                    "sourceMappings": [
                        {
                            "target_id": mapping.target_id,
                            "source_start": mapping.source_start,
                            "source_end": mapping.source_end,
                        }
                        for mapping in mappings
                        if mapping.version_number == row.number
                    ],
                }
                ref = {"kind": "script", "objectId": head.script_id, "versionId": str(row.number)}
                node = self._node(
                    context,
                    ref,
                    head.title,
                    row.number,
                    head.revision,
                    row.number == latest,
                    row.number == head.locked_version_number,
                    content,
                    "content",
                    head.script_id,
                    summary=row.change_summary,
                    parent=str(row.number - 1) if row.number > 1 else None,
                    mutable=head.locked_version_number is None,
                    created_at=fact[1] if fact else None,
                    actor_id=fact[0] if fact else None,
                )
                yield Entry(node, content)

    def _assets(self, session, context, project_id, kind, object_id, shot_id):
        statement = select(AssetRow).where(*scoped(AssetRow, context), AssetRow.owner_project_id == project_id)
        if object_id:
            statement = statement.where(AssetRow.asset_id == object_id)
        for head in self._rows(session, statement):
            actors = {}
            for audit in self._rows(
                session,
                select(AssetAuditRow)
                .where(
                    *scoped(AssetAuditRow, context),
                    AssetAuditRow.asset_id == head.asset_id,
                    AssetAuditRow.result.in_(["succeeded", "success"]),
                )
                .order_by(AssetAuditRow.occurred_at, AssetAuditRow.event_id),
            ):
                after_id = audit.after_payload.get("current_version_id")
                if isinstance(after_id, str) and after_id != audit.before_payload.get("current_version_id"):
                    actors.setdefault(after_id, audit.actor_id)
            for row in self._rows(
                session,
                select(AssetVersionRow)
                .where(*scoped(AssetVersionRow, context), AssetVersionRow.asset_id == head.asset_id)
                .order_by(AssetVersionRow.sequence),
            ):
                content = row.content
                ref = {"kind": "asset", "objectId": head.asset_id, "versionId": row.version_id}
                owned = head.owner_project_id == project_id
                node = self._node(
                    context,
                    ref,
                    head.name,
                    row.sequence,
                    head.revision,
                    row.version_id == head.current_version_id,
                    False,
                    content,
                    "assets",
                    head.asset_id,
                    created_at=row.created_at,
                    actor_id=actors.get(row.version_id),
                    summary=row.change_note,
                    parent=row.derived_from_version_id,
                    restorable=owned,
                    lock_available=False,
                    reason="VERSION_REFERENCE_READ_ONLY" if not owned else None,
                )
                yield Entry(node, content)

    def _boards(self, session, context, project_id, kind, object_id, shot_id):
        statement = select(StoryboardRow).where(*scoped(StoryboardRow, context), StoryboardRow.project_id == project_id)
        if object_id:
            statement = statement.where(StoryboardRow.storyboard_id == object_id)
        for row in self._rows(session, statement):
            board = Storyboard.from_dict(row.payload)
            if (board.tenant_id, board.workspace_id, board.project_id, board.storyboard_id, board.version) != (
                context.tenant_id,
                context.workspace_id,
                project_id,
                row.storyboard_id,
                row.version,
            ):
                raise VersionFailure("VERSION_SOURCE_INVALID", 503)
            locked = board.frozen_snapshot is not None
            current_shots = {shot.shot_id for shot in board.shots}
            previous = {}
            for version in board.versions:
                items = (
                    [(None, {"shots": [shot.to_dict() for shot in version.shots]})]
                    if kind == "storyboard"
                    else [(shot, shot.to_dict()) for shot in version.shots if not shot_id or shot.shot_id == shot_id]
                )
                for shot, content in items:
                    ref = {"kind": kind, "objectId": board.storyboard_id, "versionId": str(version.number)}
                    if shot is not None:
                        ref["shotId"] = shot.shot_id
                    source_id = board.storyboard_id if shot is None else shot.shot_id
                    parent = str(previous[source_id]) if source_id in previous else None
                    reason = (
                        "QUALITY_POLICY_REQUIRED"
                        if kind == "storyboard" and not self.quality_policy_available and not locked
                        else None
                    )
                    if shot is not None and shot.shot_id not in current_shots:
                        reason = "VERSION_SHOT_REMOVED"
                    node = self._node(
                        context,
                        ref,
                        board.storyboard_id if shot is None else shot.shot_number,
                        version.number,
                        board.version,
                        version.number == board.version,
                        locked and version.number == board.version,
                        content,
                        "storyboard",
                        source_id,
                        created_at=version.occurred_at,
                        actor_id=version.actor_id,
                        summary=version.action,
                        episode_id=board.episode_id,
                        parent=parent,
                        mutable=not locked,
                        restorable=shot is None or shot.shot_id in current_shots,
                        lock_available=self.quality_policy_available,
                        reason=reason,
                    )
                    previous[source_id] = version.number
                    yield Entry(node, content)

    def _finals(self, session, context, project_id, kind, object_id, shot_id):
        statement = select(FinalVideoVersionRow).where(
            *scoped(FinalVideoVersionRow, context), FinalVideoVersionRow.project_id == project_id
        )
        if object_id:
            statement = statement.where(FinalVideoVersionRow.final_video_id == object_id)
        for row in self._rows(session, statement):
            timeline = session.scalar(
                select(TimelineVersionRow).where(
                    *scoped(TimelineVersionRow, context),
                    TimelineVersionRow.project_id == project_id,
                    TimelineVersionRow.timeline_id == row.timeline_id,
                    TimelineVersionRow.version_id == row.timeline_version_id,
                )
            )
            if timeline is None:
                raise VersionFailure("VERSION_SOURCE_INVALID", 503)
            selection = session.scalar(
                select(FinalVideoSelectionRow).where(
                    *scoped(FinalVideoSelectionRow, context),
                    FinalVideoSelectionRow.project_id == project_id,
                    FinalVideoSelectionRow.final_video_id == row.final_video_id,
                )
            )
            ref = {"kind": "final", "objectId": row.final_video_id, "versionId": row.version_id}
            node = self._node(
                context,
                ref,
                row.final_video_id,
                timeline.revision,
                selection.revision if selection else 0,
                selection is not None and selection.selected_version_id == row.version_id,
                False,
                row.snapshot,
                "editing",
                row.timeline_version_id,
                created_at=row.created_at,
                summary=row.render_task_id,
                episode_id=str(timeline.snapshot.get("episode_id"))
                if timeline.snapshot.get("episode_id") is not None
                else None,
                lock_available=False,
                restorable=self.final_selection_available,
                reason=None if self.final_selection_available else "VERSION_FINAL_SELECTION_UNAVAILABLE",
            )
            yield Entry(node, row.snapshot)


def compare_entries(baseline: Entry, candidate: Entry) -> dict[str, Any]:
    left, right = baseline.node["ref"], candidate.node["ref"]
    if any(left.get(key) != right.get(key) for key in ("kind", "objectId", "shotId")):
        raise VersionFailure("VERSION_COMPARE_OBJECT_MISMATCH", 422)
    changes = []

    def walk(before, after, path):
        if len(changes) >= MAX_CHANGES:
            raise VersionFailure("VERSION_COMPARISON_LIMIT", 422)
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before.keys() | after.keys()):
                location = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in before:
                    changes.append({"path": location, "kind": "added", "after": after[key]})
                elif key not in after:
                    changes.append({"path": location, "kind": "removed", "before": before[key]})
                else:
                    walk(before[key], after[key], location)
        elif before != after:
            changes.append({"path": path or "/", "kind": "changed", "before": before, "after": after})
        if len(changes) > MAX_CHANGES:
            raise VersionFailure("VERSION_COMPARISON_LIMIT", 422)

    walk(baseline.content, candidate.content, "")
    if not changes and baseline.node["contentDigest"] != candidate.node["contentDigest"]:
        changes.append(
            {
                "path": "/$sourceDigest",
                "kind": "changed",
                "before": baseline.node["contentDigest"],
                "after": candidate.node["contentDigest"],
            }
        )
    result = {
        "baseline": baseline.node,
        "candidate": candidate.node,
        "changes": changes,
        "counts": {kind: sum(item["kind"] == kind for item in changes) for kind in ("added", "removed", "changed")},
        "totalChanges": len(changes),
        "contentRedacted": True,
    }
    result["digest"] = digest(result)
    if len(canonical(result)) > MAX_CONTENT_BYTES:
        raise VersionFailure("VERSION_REPORT_SIZE_LIMIT", 413)
    return result
