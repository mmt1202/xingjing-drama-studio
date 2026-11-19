"""Read the saved inputs for one episode without reserving a model call."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.episode_editor import EpisodeEditError
from server.xingjing_assets_persistence.repository import AssetRow
from server.xingjing_content.models import director_confirmation_from_dict, director_version_from_dict
from server.xingjing_content.parser import parse_script
from server.xingjing_content_persistence.repository import DirectorConfirmationRow, DirectorVersionRow, ScriptVersionRow
from server.xingjing_identity_context import TrustedWorkspaceContext

from .episode_production import prepare_production_batch, production_catalog


async def storyboard_inputs(
    session: AsyncSession,
    context: TrustedWorkspaceContext,
    project_id: str,
    snapshot: dict[str, Any],
    number: int | None,
) -> dict[str, Any]:
    permissions = context.permissions
    episodes = snapshot["items"]
    choices = [
        {
            "number": item["episode"],
            "title": item["title"],
            "bound": bool(item["production"]["scriptVersionId"]) and "script.view" in permissions,
        }
        for item in episodes
    ]
    result: dict[str, Any] = {"episodes": choices, "selected": None}
    if number is None:
        return result
    episode = next((item for item in episodes if item["episode"] == number), None)
    if episode is None:
        raise EpisodeEditError("EPISODE_NOT_FOUND", 404)
    catalog = await production_catalog(session, context, project_id, storyboard_only=True)
    binding = episode["production"]
    script = next((s for s in catalog["scripts"] if s["id"] == binding["scriptVersionId"]), None)
    working = episode.get("workingScript") or {}
    script_id = binding["scriptVersionId"].rpartition("@")[0] or working.get("scriptId")
    script_state = (
        "stale" if binding["scriptVersionId"] else "bind" if working.get("lockedVersionNumber") else "missing"
    )
    director_state = "waiting"
    candidate_number = None
    scene_count = 0
    confirmation_id = None
    if script:
        version = await session.get(ScriptVersionRow, (context.workspace_id, script_id, script["version"]))
        if version:
            parsed = parse_script(
                version.source_content, version_number=script["version"], change_summary="storyboard.inputs"
            )
            scene_count = len(parsed.scenes)
            script_state = "ready" if scene_count and not parsed.validation_errors else "invalid"
        candidate_row = await session.scalar(
            select(DirectorVersionRow)
            .where(
                DirectorVersionRow.workspace_id == context.workspace_id,
                DirectorVersionRow.script_id == script_id,
            )
            .order_by(DirectorVersionRow.number.desc())
            .limit(1)
        )
        director_state = "missing"
        if candidate_row:
            candidate = director_version_from_dict(candidate_row.payload)
            receipt = await session.scalar(
                select(DirectorConfirmationRow)
                .where(
                    DirectorConfirmationRow.workspace_id == context.workspace_id,
                    DirectorConfirmationRow.script_id == script_id,
                    DirectorConfirmationRow.candidate_number == candidate.number,
                )
                .order_by(DirectorConfirmationRow.sequence.desc())
                .limit(1)
            )
            if candidate.script_version_id == script["id"] and candidate.script_content_digest == script["digest"]:
                candidate_number = candidate.number
                director_state = "unconfirmed"
                if receipt and director_confirmation_from_dict(receipt.payload).matches(
                    candidate, script["id"], script["digest"]
                ):
                    confirmation_id = receipt.confirmation_id
                    director_state = "ready"
    matching = [
        item
        for item in catalog["snapshots"]
        if script and item["valid"] and item["scriptVersionId"] == script["id"] and item["digest"] == script["digest"]
    ]
    bound = next((s for s in matching if s["id"] == binding["assetSnapshotId"]), None)
    asset_state = "missing" if script else "waiting"
    if matching:
        asset_state = "bind"
    if binding["assetSnapshotId"]:
        asset_state = "ready" if bound else "stale"
    asset_names: dict[str, str] = {}
    if bound:
        names = await session.execute(
            select(AssetRow.asset_id, AssetRow.name).where(
                AssetRow.tenant_id == context.tenant_id,
                AssetRow.workspace_id == context.workspace_id,
                AssetRow.owner_project_id == project_id,
                AssetRow.asset_id.in_([item["id"] for item in bound["assets"]]),
            )
        )
        asset_names = dict(names.tuples().all())
    boards = sorted(
        (item for item in catalog["boards"] if item["episodeId"] == str(number)), key=lambda item: item["id"]
    )
    can_manage = "shot.manage" in permissions
    command = None
    if can_manage and script_state == director_state == asset_state == "ready":
        prepared = prepare_production_batch(context, project_id, snapshot, catalog, [number], "storyboards", "")[0]
        if not prepared["issue"]:
            key = prepared["idempotencyKey"]
            if boards:
                # Concurrent requests for another draft share a key until the new group exists.
                identity = json.dumps([key, confirmation_id, [board["id"] for board in boards]], separators=(",", ":"))
                key = "shot-draft-" + hashlib.sha256(identity.encode()).hexdigest()
            command = {"key": key, "payload": prepared["payload"]["payload"]}
    result["selected"] = {
        "number": number,
        "title": episode["title"],
        "canManage": can_manage,
        "scriptId": script_id if "script.view" in permissions else None,
        "scriptState": script_state if "script.view" in permissions else "denied",
        "scriptTitle": script["title"] if script else None,
        "version": script["version"] if script else None,
        "scriptVersionId": binding["scriptVersionId"] if "script.view" in permissions else None,
        "sceneCount": scene_count,
        "directorState": director_state if "script.view" in permissions else "denied",
        "candidateNumber": candidate_number,
        "confirmationId": confirmation_id,
        "assetState": asset_state if "asset.view" in permissions else "denied",
        "assetSnapshotId": binding["assetSnapshotId"] if "asset.view" in permissions else None,
        "assetCount": len(bound["assets"]) if bound else 0,
        "assetNames": asset_names,
        "boards": boards,
        "command": command,
    }
    return result
