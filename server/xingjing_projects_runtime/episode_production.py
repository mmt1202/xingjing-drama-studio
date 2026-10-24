"""Resolve episode production links against scoped, authoritative module records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.episode_editor import EpisodeEdit, EpisodeEditError, EpisodeProduction
from server.xingjing_assets_persistence.repository import AssetRow
from server.xingjing_compliance_persistence.repository import DeliveryRow, ReviewRow
from server.xingjing_content_persistence.repository import ScriptRow, ScriptVersionRow
from server.xingjing_editing_persistence.models import FinalVideoVersionRow, TimelineHeadRow
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_storyboard.errors import ContractViolation
from server.xingjing_storyboard.models import FrozenStoryboard
from server.xingjing_storyboard_persistence.repository import StoryboardRow

from .episode_sources_models import EpisodeWorkingScriptRow


def _scope(model: Any, context: TrustedWorkspaceContext, project_id: str):
    return (
        model.tenant_id == context.tenant_id,
        model.workspace_id == context.workspace_id,
        model.project_id == project_id,
    )


async def production_catalog(
    session: AsyncSession, context: TrustedWorkspaceContext, project_id: str, *, enforce_visibility: bool = True
) -> dict[str, Any]:
    """Return references and counts only; source text and media keys stay in their modules."""
    scripts, snapshots, finals, boards, reviews, deliveries = [], {}, [], [], [], []
    if not enforce_visibility or "script.view" in context.permissions:
        rows = await session.execute(
            select(ScriptRow, ScriptVersionRow)
            .join(
                ScriptVersionRow,
                and_(
                    ScriptRow.workspace_id == ScriptVersionRow.workspace_id,
                    ScriptRow.script_id == ScriptVersionRow.script_id,
                    ScriptRow.locked_version_number == ScriptVersionRow.number,
                ),
            )
            .where(ScriptRow.workspace_id == context.workspace_id, ScriptRow.project_id == project_id)
            .order_by(ScriptRow.title, ScriptRow.script_id)
        )
        scripts = [
            {
                "id": f"{script.script_id}@{version.number}",
                "title": script.title,
                "version": version.number,
                "digest": hashlib.sha256(version.source_content.encode("utf-8")).hexdigest(),
            }
            for script, version in rows
        ]
    if not enforce_visibility or "asset.view" in context.permissions:
        assets = await session.scalars(
            select(AssetRow)
            .where(
                AssetRow.tenant_id == context.tenant_id,
                AssetRow.workspace_id == context.workspace_id,
                AssetRow.owner_project_id == project_id,
            )
            .order_by(AssetRow.asset_id)
        )
        for asset in assets:
            frozen = asset.frozen_snapshot
            identity = str(frozen.get("snapshot_id", ""))
            if not identity:
                continue
            item = snapshots.setdefault(
                identity,
                {
                    "id": identity,
                    "scriptVersionId": frozen.get("script_version_id"),
                    "digest": frozen.get("content_sha256"),
                    "assets": [],
                    "valid": True,
                },
            )
            item["valid"] = item["valid"] and (
                frozen.get("workspace_id") == context.workspace_id
                and frozen.get("project_id") == project_id
                and frozen.get("script_version_id") == item["scriptVersionId"]
                and frozen.get("content_sha256") == item["digest"]
            )
            item["assets"].append({"id": asset.asset_id, "versionId": asset.current_version_id})
    if not enforce_visibility or "final.view" in context.permissions:
        final_rows = await session.execute(
            select(FinalVideoVersionRow, TimelineHeadRow.episode_id)
            .join(
                TimelineHeadRow,
                and_(
                    FinalVideoVersionRow.tenant_id == TimelineHeadRow.tenant_id,
                    FinalVideoVersionRow.workspace_id == TimelineHeadRow.workspace_id,
                    FinalVideoVersionRow.project_id == TimelineHeadRow.project_id,
                    FinalVideoVersionRow.timeline_id == TimelineHeadRow.timeline_id,
                    FinalVideoVersionRow.final_video_id == TimelineHeadRow.final_video_id,
                ),
            )
            .where(*_scope(FinalVideoVersionRow, context, project_id))
            .order_by(
                FinalVideoVersionRow.created_at.desc(),
                FinalVideoVersionRow.version_id,
            )
        )
        finals = [
            {
                "id": row.version_id,
                "finalVideoId": row.final_video_id,
                "episodeId": episode_id,
                "timelineId": row.timeline_id,
                "createdAt": row.created_at.isoformat(),
            }
            for row, episode_id in final_rows
        ]
    if not enforce_visibility or "shot.view" in context.permissions:
        board_rows = await session.scalars(select(StoryboardRow).where(*_scope(StoryboardRow, context, project_id)))
        boards = [
            {
                "id": row.storyboard_id,
                "episodeId": row.episode_id,
                "version": row.version,
                "frozen": _frozen_board_valid(row.payload),
                "shots": len(shots) if isinstance(shots := row.payload.get("shots"), list) else 0,
            }
            for row in board_rows
        ]
    if not enforce_visibility or "compliance.view" in context.permissions:
        review_rows = await session.scalars(select(ReviewRow).where(*_scope(ReviewRow, context, project_id)))
        reviews = [
            {"versionId": row.project_version, "status": row.manual_review_status, "conclusion": row.conclusion}
            for row in review_rows
        ]
    if not enforce_visibility or "export.view" in context.permissions:
        delivery_rows = await session.scalars(
            select(DeliveryRow)
            .where(*_scope(DeliveryRow, context, project_id))
            .order_by(DeliveryRow.created_at.desc(), DeliveryRow.request_id)
        )
        deliveries = [
            {
                "id": row.request_id,
                "versionId": row.project_version,
                "target": row.target,
                "format": row.format,
                "status": row.status,
                "createdAt": row.created_at.isoformat(),
            }
            for row in delivery_rows
        ]
    return {
        "scripts": scripts,
        "snapshots": list(snapshots.values()),
        "finals": finals,
        "boards": boards,
        "reviews": reviews,
        "deliveries": deliveries,
    }


def _frozen_board_valid(payload: dict[str, object]) -> bool:
    snapshot = payload.get("frozen_snapshot")
    if payload.get("status") != "frozen" or not isinstance(snapshot, dict):
        return False
    try:
        frozen = FrozenStoryboard.from_dict(snapshot)
        return bool(frozen.shots) and frozen.verify_digest()
    except (ContractViolation, KeyError, TypeError, ValueError):
        return False


def binding_issue(production: EpisodeProduction, episode: int, catalog: dict[str, Any]) -> str | None:
    script = next((s for s in catalog["scripts"] if s["id"] == production.scriptVersionId), None)
    if production.scriptVersionId and script is None:
        return "EPISODE_FROZEN_SCRIPT_UNAVAILABLE"
    if production.assetSnapshotId:
        snapshot = next((s for s in catalog["snapshots"] if s["id"] == production.assetSnapshotId), None)
        if (
            not snapshot
            or not snapshot["valid"]
            or not script
            or (snapshot["scriptVersionId"] != script["id"] or snapshot["digest"] != script["digest"])
        ):
            return "EPISODE_ASSET_SNAPSHOT_MISMATCH"
    if production.finalVideoVersionId and not any(
        f["id"] == production.finalVideoVersionId and f["episodeId"] == str(episode) for f in catalog["finals"]
    ):
        return "EPISODE_FINAL_VERSION_MISMATCH"
    return None


async def validate_production_edits(
    session: AsyncSession,
    context: TrustedWorkspaceContext,
    project_id: str,
    edits: list[EpisodeEdit],
    snapshot: dict[str, Any],
) -> None:
    old = {e["episode"]: e["production"] for e in snapshot["items"]}
    proposed = {
        e.episode: e.production.model_dump() if e.production is not None else old.get(e.episode, {}) for e in edits
    }
    changed = [
        e
        for e in edits
        if e.production is not None and proposed[e.episode] != old.get(e.episode, EpisodeProduction().model_dump())
    ]
    if not changed:
        return
    versions = [p["scriptVersionId"] for p in proposed.values() if p.get("scriptVersionId")]
    if len(versions) != len(set(versions)):
        raise EpisodeEditError("EPISODE_SCRIPT_ALREADY_BOUND", 409)
    for edit in changed:
        previous = old.get(edit.episode, {})
        for field, permission in (
            ("scriptVersionId", "script.view"),
            ("assetSnapshotId", "asset.view"),
            ("finalVideoVersionId", "final.view"),
        ):
            if previous.get(field, "") != proposed[edit.episode][field] and permission not in context.permissions:
                raise EpisodeEditError("FORBIDDEN", 403)
    catalog = await production_catalog(session, context, project_id, enforce_visibility=False)
    for edit in changed:
        assert edit.production is not None
        script_id = edit.production.scriptVersionId.rsplit("@", 1)[0]
        link = (
            await session.scalar(
                select(EpisodeWorkingScriptRow).where(
                    *_scope(EpisodeWorkingScriptRow, context, project_id),
                    EpisodeWorkingScriptRow.script_id == script_id,
                )
            )
            if script_id
            else None
        )
        if link is not None:
            selected = edit.sourceRange
            source = next(
                (s for s in snapshot.get("sources", []) if s["path"] == link.source_ref.get("sourceId")), None
            )
            source_digest = (
                (source.get("digest") or hashlib.sha256(source["text"].encode("utf-8")).hexdigest()) if source else None
            )
            if (
                link.episode != edit.episode
                or selected is None
                or selected.source_file != link.source_ref.get("sourceId")
                or selected.start != link.source_ref.get("start")
                or selected.end != link.source_ref.get("end")
                or source_digest != link.source_ref.get("digest")
            ):
                raise EpisodeEditError("EPISODE_WORKING_SCRIPT_SOURCE_MISMATCH", 409)
        previous = old.get(edit.episode, {})
        has_boards = await session.scalar(
            select(StoryboardRow.storyboard_id)
            .where(
                *_scope(StoryboardRow, context, project_id),
                StoryboardRow.episode_id == str(edit.episode),
            )
            .limit(1)
        )
        if has_boards and any(
            previous.get(field, "") != proposed[edit.episode][field] for field in ("scriptVersionId", "assetSnapshotId")
        ):
            raise EpisodeEditError("EPISODE_PRODUCTION_INPUTS_LOCKED", 409)
        issue = binding_issue(edit.production, edit.episode, catalog)
        if issue:
            raise EpisodeEditError(issue)


def project_production(snapshot: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    items = []
    for episode in snapshot["items"]:
        production = EpisodeProduction.model_validate(episode["production"])
        boards = [b for b in catalog["boards"] if b["episodeId"] == str(episode["episode"])]
        review = next((r for r in catalog["reviews"] if r["versionId"] == production.finalVideoVersionId), None)
        deliveries = [d for d in catalog["deliveries"] if d["versionId"] == production.finalVideoVersionId]
        issue = binding_issue(production, episode["episode"], catalog)
        state = "unbound"
        if production.scriptVersionId and not issue:
            state = "script_frozen"
        if not issue and any(b["shots"] for b in boards):
            state = "storyboard_ready"
        if production.finalVideoVersionId and not issue:
            state = "final_ready"
        if review and production.finalVideoVersionId and not issue:
            if review["status"] in {"pending", "appealed"}:
                state = "pending_review"
            elif review["conclusion"] == "blocked" or review["status"] == "rejected":
                state = "review_blocked"
        if state == "final_ready" and any(
            d["status"] == "succeeded" and d["format"] == "publish_package" for d in deliveries
        ):
            state = "package_ready"
        items.append(
            {
                "episode": episode["episode"],
                "state": state,
                "issue": issue,
                "boards": boards,
                "review": review,
                "deliveries": deliveries,
            }
        )
    return {"revision": snapshot["revision"], **catalog, "items": items}


def prepare_production_batch(
    context: TrustedWorkspaceContext,
    project_id: str,
    snapshot: dict[str, Any],
    catalog: dict[str, Any],
    numbers: list[int],
    action: str,
    target: str,
) -> list[dict[str, Any]]:
    """Build immutable requests for existing audited M05/M09 commands; no model calls here."""
    episodes = {e["episode"]: e for e in snapshot["items"]}
    if any(number not in episodes for number in numbers):
        raise EpisodeEditError("EPISODE_NOT_FOUND", 404)
    results = []
    for number in numbers:
        production = EpisodeProduction.model_validate(episodes[number]["production"])
        applicable = (
            production.model_copy(update={"finalVideoVersionId": ""})
            if action == "storyboards"
            else (production.model_copy(update={"scriptVersionId": "", "assetSnapshotId": ""}))
        )
        issue = binding_issue(applicable, number, catalog)
        existing = None
        if action == "storyboards":
            existing = next((b["id"] for b in catalog["boards"] if b["episodeId"] == str(number)), None)
            issue = issue or (
                None
                if production.scriptVersionId and production.assetSnapshotId
                else "EPISODE_STORYBOARD_INPUTS_REQUIRED"
            )
            payload = {
                "action": "generateShots",
                "payload": {
                    "episodeId": str(number),
                    "scriptVersionId": production.scriptVersionId,
                    "assetSnapshotId": production.assetSnapshotId,
                },
            }
            endpoint = "shots/actions"
        else:
            existing = next(
                (
                    d["id"]
                    for d in catalog["deliveries"]
                    if d["versionId"] == production.finalVideoVersionId
                    and d["target"] == target
                    and d["format"] == "publish_package"
                    and d["status"] == "succeeded"
                ),
                None,
            )
            issue = issue or (None if production.finalVideoVersionId else "EPISODE_FINAL_VERSION_REQUIRED")
            payload = {"projectVersion": production.finalVideoVersionId, "target": target, "format": "publish_package"}
            endpoint = "exports"
        snapshot_input = next((s for s in catalog["snapshots"] if s["id"] == production.assetSnapshotId), None)
        fingerprint = json.dumps(
            [
                "episode-batch-v1",
                context.tenant_id,
                context.workspace_id,
                project_id,
                number,
                action,
                payload,
                snapshot_input if action == "storyboards" else None,
            ],
            sort_keys=True,
            ensure_ascii=False,
        )
        results.append(
            {
                "episode": number,
                "title": episodes[number]["title"],
                "endpoint": endpoint,
                "payload": payload,
                "idempotencyKey": "ep-" + hashlib.sha256(fingerprint.encode()).hexdigest(),
                "issue": None if existing else issue,
                "existingId": existing,
            }
        )
    return results
