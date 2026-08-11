# pyright: reportMissingImports=false, reportPossiblyUnboundVariable=false
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.testclient import TestClient

from server.xingjing_storyboard.models import AssetKind, AssetReference
from server.xingjing_storyboard.ports import AssetReferenceFailure
from server.xingjing_storyboard.quality import QualityPolicy
from server.xingjing_storyboard.repository import AtomicFileStoryboardRepository
from server.xingjing_storyboard.service import AccessContext, ShotDraft, StoryboardService

if TYPE_CHECKING:
    from server.xingjing_storyboard_http import StoryboardHttpDependencies


class SequentialIds:
    def __init__(self) -> None:
        self._counts: defaultdict[str, int] = defaultdict(int)

    def new(self, kind: str) -> str:
        self._counts[kind] += 1
        return f"{kind}-{self._counts[kind]}"


class AcceptingAssets:
    def validate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        references: tuple[AssetReference, ...],
    ) -> tuple[AssetReferenceFailure, ...]:
        return ()


def _http_api():
    try:
        from server.xingjing_storyboard_http import (
            InMemoryStoryboardUploadStore,
            create_storyboard_http_dependencies,
            create_storyboard_router,
            router,
        )
    except ModuleNotFoundError:
        pytest.fail("M05 FastAPI HTTP contract module is missing")
    return (
        InMemoryStoryboardUploadStore,
        create_storyboard_http_dependencies,
        create_storyboard_router,
        router,
    )


def _service(root: Path) -> StoryboardService:
    return StoryboardService(
        AtomicFileStoryboardRepository(root),
        AcceptingAssets(),
        identifiers=SequentialIds(),
        quality_policy=QualityPolicy(
            minimum_duration_ms=500,
            maximum_duration_ms=10_000,
            max_adjacent_duration_delta_ms=5_000,
            max_dialogue_characters_per_second=20,
            require_scene_reference=True,
            require_character_for_dialogue=True,
            compliance_terms=frozenset({"blocked-term"}),
        ),
    )


def _context(*, permissions: frozenset[str] = frozenset({"shot.view", "shot.manage"})) -> AccessContext:
    return AccessContext(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        project_id="project-1",
        actor_id="actor-1",
        request_id="seed-request",
        permissions=permissions,
    )


def _seed(service: StoryboardService) -> str:
    references = (
        AssetReference("character-1", "character-version-1", AssetKind.CHARACTER),
        AssetReference("scene-1", "scene-version-1", AssetKind.SCENE),
    )
    result = service.create_storyboard(
        _context(),
        episode_id="episode-1",
        shots=(
            ShotDraft(
                shot_number="001",
                duration_ms=2_000,
                dialogue="hello",
                shot_size="wide",
                camera_movement="pan",
                prompt="first prompt",
                asset_references=references,
            ),
            ShotDraft(
                shot_number="002",
                duration_ms=2_200,
                dialogue="world",
                shot_size="medium",
                camera_movement="static",
                prompt="second prompt",
                asset_references=references,
            ),
        ),
        idempotency_key="seed-storyboard",
    )
    return result.storyboard.storyboard_id


def _client(
    service: StoryboardService,
    *,
    resolver=None,
) -> tuple[TestClient, StoryboardHttpDependencies]:
    (
        InMemoryStoryboardUploadStore,
        create_storyboard_http_dependencies,
        create_storyboard_router,
        router,
    ) = _http_api()
    assert isinstance(router, APIRouter)

    async def resolve_context(request: Request):
        from server.xingjing_identity_context import TrustedWorkspaceContext

        return TrustedWorkspaceContext(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            actor_id="actor-1",
            request_id=request.headers.get("X-Request-Id", "test-request"),
            permissions=frozenset({"shot.view", "shot.manage"}),
            role="OWNER",
        )

    dependencies = create_storyboard_http_dependencies(
        service=service,
        trusted_context_resolver=resolver or resolve_context,
        upload_store=InMemoryStoryboardUploadStore(),
    )
    app = FastAPI()
    app.include_router(create_storyboard_router(dependencies), prefix="/api/v1")
    return TestClient(app), dependencies


def _headers(*, request_id: str = "request-1") -> dict[str, str]:
    return {
        "X-Request-Id": request_id,
    }


def test_list_and_detail_are_scoped_permission_checked_frontend_contracts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _seed(service)
    client, _ = _client(service)

    response = client.get(
        "/api/v1/projects/project-1/shots?pageSize=1&sort=sequenceNo%3Aasc%2Cid%3Aasc",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "id": "shot-1",
                "storyboardId": "storyboard-1",
                "episodeId": "episode-1",
                "shotNo": "001",
                "sequenceNo": 1,
                "version": 1,
                "status": "draft",
                "shotSize": "wide",
                "cameraMove": "pan",
                "durationMs": 2000,
                "dialogue": "hello",
                "prompt": "first prompt",
                "assetReferences": [
                    {
                        "id": "character-1",
                        "versionId": "character-version-1",
                        "type": "character",
                        "name": "character-1",
                    },
                    {"id": "scene-1", "versionId": "scene-version-1", "type": "scene", "name": "scene-1"},
                ],
                "generationStatus": "not_started",
                "issueCount": 0,
                "qualityIssues": [],
                "candidates": [],
                "versions": [
                    {
                        "id": "storyboard-1-v1-shot-1",
                        "versionNo": 1,
                        "shotSize": "wide",
                        "cameraMove": "pan",
                        "durationMs": 2000,
                        "dialogue": "hello",
                        "prompt": "first prompt",
                        "status": "draft",
                        "createdAt": ANY,
                    }
                ],
                "updatedAt": ANY,
            }
        ],
        "meta": {
            "requestId": "request-1",
            "page": {"nextToken": "1"},
            "collectionVersion": 1,
        },
    }

    detail = client.get("/api/v1/projects/project-1/shots/shot-2", headers=_headers(request_id="detail-1"))
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == "shot-2"
    assert detail.json()["meta"] == {"requestId": "detail-1"}

    async def denied_context(request: Request):
        from server.xingjing_identity_context import TrustedWorkspaceContext

        return TrustedWorkspaceContext(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            actor_id="actor-1",
            request_id=request.headers.get("X-Request-Id", "test-request"),
            permissions=frozenset(),
            role="MEMBER",
        )

    denied_client, _ = _client(service, resolver=denied_context)
    denied = denied_client.get(
        "/api/v1/projects/project-1/shots/shot-1",
        headers={
            **_headers(request_id="denied-1"),
            "X-Workspace-Id": "workspace-1",
            "X-Actor-Id": "actor-attacker",
            "X-Permissions": "shot.manage",
        },
    )
    assert denied.status_code == 403
    assert denied.json() == {
        "error": {"code": "PERMISSION_DENIED", "message": "PERMISSION_DENIED", "retryable": False, "details": {}},
        "meta": {"requestId": "denied-1"},
    }
    assert "shot-1" not in denied.text
    assert "first prompt" not in denied.text

    other_workspace = client.get(
        "/api/v1/projects/project-1/shots/shot-1",
        headers={
            **_headers(request_id="hidden-1"),
            "X-Workspace-Id": "workspace-2",
            "X-Actor-Id": "actor-attacker",
            "X-Permissions": "shot.manage",
        },
    )
    assert other_workspace.status_code == 200
    assert other_workspace.json()["data"]["id"] == "shot-1"


def test_trusted_context_failures_are_closed_and_do_not_leak_shots(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _seed(service)

    async def unavailable(_: Request):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "IDENTITY_CONTEXT_UNAVAILABLE"}
        )

    async def unauthenticated(_: Request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "UNAUTHENTICATED"})

    async def missing_workspace(_: Request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "WORKSPACE_CONTEXT_REQUIRED"})

    for resolver, expected_status, expected_code in (
        (unavailable, 503, "IDENTITY_CONTEXT_UNAVAILABLE"),
        (unauthenticated, 401, "UNAUTHENTICATED"),
        (missing_workspace, 403, "WORKSPACE_CONTEXT_REQUIRED"),
    ):
        client, _ = _client(service, resolver=resolver)
        response = client.get("/api/v1/projects/project-1/shots/shot-1", headers=_headers(request_id="closed-1"))

        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code
        assert "shot-1" not in response.text
        assert "first prompt" not in response.text


def test_missing_trusted_context_resolver_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _seed(service)
    InMemoryStoryboardUploadStore, create_dependencies, create_router, _ = _http_api()
    dependencies = create_dependencies(service=service, upload_store=InMemoryStoryboardUploadStore())
    app = FastAPI()
    app.include_router(create_router(dependencies), prefix="/api/v1")

    response = TestClient(app).get("/api/v1/projects/project-1/shots/shot-1", headers=_headers())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "IDENTITY_CONTEXT_UNAVAILABLE"
    assert "shot-1" not in response.text
    assert "first prompt" not in response.text


def test_batch_edit_is_idempotent_optimistically_locked_and_read_after_write(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _seed(service)
    client, _ = _client(service)
    request = {
        "action": "batchUpdate",
        "payload": {
            "itemIds": ["shot-1"],
            "changes": {
                "shotSize": "close-up",
                "durationMs": 1750,
                "modelPolicy": "quality",
                "costTier": "premium",
            },
        },
    }
    headers = {**_headers(request_id="batch-1"), "Idempotency-Key": "batch-key-1", "If-Match": '"1"'}

    response = client.post("/api/v1/projects/project-1/shots/actions", headers=headers, json=request)

    assert response.status_code == 200
    changed = response.json()["data"]["shots"][0]
    assert {key: changed[key] for key in ("id", "version", "shotSize", "durationMs")} == {
        "id": "shot-1",
        "version": 2,
        "shotSize": "close-up",
        "durationMs": 1750,
    }
    assert response.json()["data"]["batch"] == {
        "operationId": "batch-key-1",
        "items": [{"id": "shot-1", "status": "succeeded", "version": 2}],
        "succeeded": 1,
        "failed": 0,
        "requestId": "batch-1",
    }

    replay = client.post("/api/v1/projects/project-1/shots/actions", headers=headers, json=request)
    assert replay.status_code == 200
    assert replay.headers["X-Idempotency-Replayed"] == "true"
    assert replay.json()["data"]["shots"][0]["version"] == 2

    stale = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="stale-1"), "Idempotency-Key": "batch-key-2", "If-Match": '"1"'},
        json=request,
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "error": {
            "code": "VERSION_CONFLICT",
            "message": "VERSION_CONFLICT",
            "retryable": False,
            "details": {"current_version": 2},
        },
        "meta": {"requestId": "stale-1"},
    }

    missing_idempotency = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="missing-key"), "If-Match": '"2"'},
        json=request,
    )
    assert missing_idempotency.status_code == 400
    assert missing_idempotency.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    restarted_client, _ = _client(_service(tmp_path))
    persisted = restarted_client.get(
        "/api/v1/projects/project-1/shots/shot-1",
        headers=_headers(request_id="persisted-1"),
    )
    assert persisted.status_code == 200
    assert persisted.json()["data"]["version"] == 2
    assert persisted.json()["data"]["shotSize"] == "close-up"


def test_quality_replace_freeze_export_import_and_version_compare_http_contracts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    storyboard_id = _seed(service)
    client, _ = _client(service)

    quality = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="quality-1"), "Idempotency-Key": "quality-key", "If-Match": '"1"'},
        json={"action": "runQualityCheck", "payload": {"shotIds": ["shot-1", "shot-2"]}},
    )
    assert quality.status_code == 200
    assert quality.json()["data"]["issues"] == []
    assert quality.json()["data"]["shots"][0]["version"] == 2

    replacement = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="replace-1"), "Idempotency-Key": "replace-key", "If-Match": '"2"'},
        json={"action": "replaceShot", "targetId": "shot-1", "payload": {"candidateId": "media-repaired"}},
    )
    assert replacement.status_code == 200
    assert replacement.json()["data"]["shot"]["candidates"] == [
        {"id": "media-repaired", "version": 1, "selected": True, "status": "succeeded"}
    ]

    comparison = client.get(
        f"/api/v1/projects/project-1/storyboards/{storyboard_id}/versions/compare?baselineVersion=1&candidateVersion=3",
        headers=_headers(request_id="compare-1"),
    )
    assert comparison.status_code == 200
    assert comparison.json()["data"]["changedShotIds"] == ["shot-1"]

    frozen = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="freeze-1"), "Idempotency-Key": "freeze-key", "If-Match": '"3"'},
        json={"action": "confirmStoryboard", "payload": {"storyboardId": storyboard_id}},
    )
    assert frozen.status_code == 200
    assert frozen.json()["data"]["shots"][0]["status"] == "frozen"

    exported = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="export-1"), "Idempotency-Key": "export-key"},
        json={"action": "createExport", "payload": {"storyboardId": storyboard_id, "format": "json"}},
    )
    assert exported.status_code == 200
    download = client.get(exported.json()["data"]["task"]["downloadUrl"], headers=_headers())
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")
    assert b'"storyboard_id":"storyboard-1"' in download.content

    created = client.post(
        "/api/v1/uploads",
        headers={**_headers(request_id="upload-create"), "Idempotency-Key": "upload-key"},
        json={
            "action": "createUpload",
            "payload": {"fileName": "import.json", "size": 200, "mimeType": "application/json", "sha256": "ignored"},
        },
    )
    assert created.status_code == 200
    upload_id = created.json()["data"]["uploadId"]
    source = b'{"schema_version":1,"shots":[{"shot_id":"import-shot","position":1,"shot_number":"I01","shot_size":"wide","camera_movement":"static","dialogue":"","duration_ms":1000,"prompt":"imported","model_strategy":"","cost_tier":"","asset_references":[],"selected_media_id":"","generation_status":"not_started"}]}'
    assert client.put(f"/api/v1/storyboard-uploads/{upload_id}/content", content=source).status_code == 204
    assert (
        client.post(
            f"/api/v1/uploads/{upload_id}/complete",
            headers={**_headers(), "Idempotency-Key": "upload-complete"},
            json={"action": "completeUpload", "payload": {}},
        ).status_code
        == 200
    )
    preview = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="preview-1"), "Idempotency-Key": "preview-key"},
        json={"action": "preflightImport", "payload": {"uploadId": upload_id, "fileName": "import.json"}},
    )
    assert preview.status_code == 200
    assert preview.json()["data"]["batch"]["succeeded"] == 1
    confirmed = client.post(
        "/api/v1/projects/project-1/shots/actions",
        headers={**_headers(request_id="import-1"), "Idempotency-Key": "import-key"},
        json={"action": "commitImport", "payload": {"uploadId": upload_id, "episodeId": "episode-imported"}},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["shots"][0]["id"] == "import-shot"
