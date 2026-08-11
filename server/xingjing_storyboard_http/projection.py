from __future__ import annotations

from datetime import UTC, datetime

from server.xingjing_storyboard.models import QualitySeverity, Shot, Storyboard


def project_shot(storyboard: Storyboard, shot: Shot) -> dict[str, object]:
    versions: list[dict[str, object]] = []
    for version in storyboard.versions:
        versioned_shot = next((item for item in version.shots if item.shot_id == shot.shot_id), None)
        if versioned_shot is None:
            continue
        versions.append(
            {
                "id": f"{storyboard.storyboard_id}-v{version.number}-{shot.shot_id}",
                "versionNo": version.number,
                "shotSize": versioned_shot.shot_size or "",
                "cameraMove": versioned_shot.camera_movement or "",
                "durationMs": versioned_shot.duration_ms,
                "dialogue": versioned_shot.dialogue,
                "prompt": versioned_shot.prompt,
                "status": storyboard.status.value if version.number == storyboard.version else "draft",
                "createdAt": _datetime_text(version.occurred_at),
            }
        )

    candidate_ids = [replacement.new_media_id for replacement in shot.replacements]
    generated_candidate_ids = {
        replacement.new_media_id
        for replacement in shot.replacements
        if replacement.reason == "generation_output_adopted"
    }
    if shot.selected_media_id and shot.selected_media_id not in candidate_ids:
        candidate_ids.append(shot.selected_media_id)
    candidates = [
        {
            "id": candidate_id,
            "version": index,
            "selected": candidate_id == shot.selected_media_id,
            "status": shot.generation_status.value,
            **({"mediaUrl": (
                f"/api/v1/generation-assets/{candidate_id}/content"
                f"?projectId={storyboard.project_id}"
            ),
            "thumbnailUrl": (
                f"/api/v1/generation-assets/{candidate_id}/content"
                f"?projectId={storyboard.project_id}"
            )} if candidate_id in generated_candidate_ids else {}),
        }
        for index, candidate_id in enumerate(candidate_ids, 1)
    ]
    issues = [
        {
            "id": issue.issue_id,
            "code": issue.code,
            "severity": "blocking" if issue.severity is QualitySeverity.ERROR else "warning",
            "message": issue.code,
            "retryable": issue.severity is QualitySeverity.WARNING,
            "recoveryAction": issue.field,
        }
        for issue in shot.quality_issues
    ]
    return {
        "id": shot.shot_id,
        "storyboardId": storyboard.storyboard_id,
        "episodeId": storyboard.episode_id,
        "shotNo": shot.shot_number,
        "sequenceNo": shot.position,
        "version": storyboard.version,
        "status": storyboard.status.value,
        "shotSize": shot.shot_size or "",
        "cameraMove": shot.camera_movement or "",
        "durationMs": shot.duration_ms,
        "dialogue": shot.dialogue,
        "prompt": shot.prompt,
        "negativePrompt": shot.negative_prompt,
        "promptTemplateId": shot.prompt_template_id,
        "promptVariables": shot.prompt_variables or {},
        "promptModelAdapterVersion": shot.prompt_model_adapter_version,
        "assetReferences": [
            {
                "id": reference.asset_id,
                "versionId": reference.asset_version_id,
                "type": reference.kind.value,
                "name": reference.asset_id,
            }
            for reference in shot.asset_references
        ],
        "generationStatus": shot.generation_status.value,
        "generationTaskId": shot.generation_task_id,
        "issueCount": len(issues),
        "qualityIssues": issues,
        "candidates": candidates,
        "versions": versions,
        "updatedAt": _datetime_text(storyboard.updated_at),
    }


def _datetime_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
