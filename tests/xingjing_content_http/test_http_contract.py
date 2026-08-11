# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.xingjing_content import ContentService, FileContentRepository
from server.xingjing_content_http import ContentHttpDependencies, configure_content_http, create_content_router, router
from server.xingjing_identity_context import TrustedWorkspaceContext

SOURCE = """场景：雨夜街头\n动作：雨水打湿了招牌。\n阿明：我们得走了。"""


def _client(tmp_path: Path, *, permissions: frozenset[str] = frozenset({"script.view", "script.manage"})) -> TestClient:
    service = ContentService(FileContentRepository(tmp_path))

    async def resolve_context(_request):
        return TrustedWorkspaceContext("tenant-1", "workspace-1", "actor-1", "request-1", permissions, "OWNER")

    async def authorize_project(context: TrustedWorkspaceContext, project_id: str) -> bool:
        return context.workspace_id == "workspace-1" and project_id == "project-1"

    app = FastAPI()
    app.include_router(create_content_router(ContentHttpDependencies(service, resolve_context, authorize_project)))
    return TestClient(app)


def _headers(**extra: str) -> dict[str, str]:
    return {"X-Request-Id": "request-1", **extra}


def test_scripts_http_import_list_detail_revise_profile_freeze_and_compare(tmp_path: Path) -> None:
    client = _client(tmp_path)
    imported = client.post(
        "/projects/project-1/scripts/actions",
        headers=_headers(**{"Idempotency-Key": "import-1"}),
        json={
            "action": "import",
            "payload": {"title": "雨夜", "filename": "rain.md", "mediaType": "text/markdown", "content": SOURCE},
        },
    )
    assert imported.status_code == 200
    script = imported.json()["data"]["script"]
    assert script["revision"] == 1
    script_id = script["id"]

    listing = client.get("/projects/project-1/scripts", headers=_headers())
    assert listing.status_code == 200
    assert listing.json()["meta"]["total"] == 1

    detail = client.get(f"/projects/project-1/scripts/{script_id}", headers=_headers())
    assert detail.status_code == 200
    assert detail.json()["data"]["script"]["id"] == script_id

    revised = client.post(
        "/projects/project-1/scripts/actions",
        headers=_headers(**{"Idempotency-Key": "revise-1", "If-Match": '"1"'}),
        json={
            "action": "revise",
            "targetId": script_id,
            "payload": {"content": SOURCE + "\n动作：车灯远去。", "changeSummary": "补充结尾"},
        },
    )
    assert revised.status_code == 200
    assert revised.json()["data"]["script"]["revision"] == 2

    profile = client.post(
        "/projects/project-1/scripts/actions",
        headers=_headers(**{"Idempotency-Key": "profile-1", "If-Match": "2"}),
        json={"action": "setDirectorProfile", "targetId": script_id, "payload": {"pacing": "快节奏"}},
    )
    assert profile.status_code == 200
    assert profile.json()["data"]["script"]["directorProfile"]["pacing"] == "快节奏"

    comparison = client.get(
        f"/projects/project-1/scripts/{script_id}?baselineVersion=1&candidateVersion=2",
        headers=_headers(),
    )
    assert comparison.status_code == 200
    assert comparison.json()["data"]["comparison"]["changedFields"] == [
        "sourceContent",
        "scenes",
        "sourceMappings",
        "changeSummary",
    ]

    frozen = client.post(
        "/projects/project-1/scripts/actions",
        headers=_headers(**{"Idempotency-Key": "freeze-1", "If-Match": "3"}),
        json={"action": "freeze", "targetId": script_id, "payload": {}},
    )
    assert frozen.status_code == 200
    assert frozen.json()["data"]["script"]["lockedVersionNumber"] == 2


def test_scripts_http_fails_closed_for_scope_permission_and_missing_dependencies(tmp_path: Path) -> None:
    forbidden = _client(tmp_path, permissions=frozenset())
    response = forbidden.get("/projects/project-1/scripts", headers=_headers())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    client = _client(tmp_path)
    out_of_scope = client.get("/projects/project-2/scripts", headers=_headers())
    assert out_of_scope.status_code == 404
    assert out_of_scope.json()["error"]["code"] == "PROJECT_NOT_FOUND"

    app = FastAPI()
    app.include_router(router)
    unavailable = TestClient(app).get("/projects/project-1/scripts", headers=_headers())
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "CONTENT_HTTP_DEPENDENCIES_UNAVAILABLE"


def test_configured_router_uses_explicit_dependencies(tmp_path: Path) -> None:
    service = ContentService(FileContentRepository(tmp_path))

    async def resolve_context(_request):
        return TrustedWorkspaceContext(
            "tenant-1", "workspace-1", "actor-1", "request-1", frozenset({"script.view"}), "OWNER"
        )

    async def authorize_project(_context: TrustedWorkspaceContext, project_id: str) -> bool:
        return project_id == "project-1"

    app = FastAPI()
    app.include_router(configure_content_http(ContentHttpDependencies(service, resolve_context, authorize_project)))
    response = TestClient(app).get("/projects/project-1/scripts", headers=_headers())
    assert response.status_code == 200


def test_scripts_http_has_stable_input_idempotency_and_conflict_errors(tmp_path: Path) -> None:
    client = _client(tmp_path)
    missing_key = client.post(
        "/projects/project-1/scripts/actions",
        headers=_headers(),
        json={
            "action": "import",
            "payload": {"title": "雨夜", "filename": "rain.md", "mediaType": "text/markdown", "content": SOURCE},
        },
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    imported = client.post(
        "/projects/project-1/scripts/actions",
        headers=_headers(**{"Idempotency-Key": "import-1"}),
        json={
            "action": "import",
            "payload": {"title": "雨夜", "filename": "rain.md", "mediaType": "text/markdown", "content": SOURCE},
        },
    )
    script_id = imported.json()["data"]["script"]["id"]
    conflict = client.post(
        "/projects/project-1/scripts/actions",
        headers=_headers(**{"Idempotency-Key": "revise-1", "If-Match": "999"}),
        json={"action": "revise", "targetId": script_id, "payload": {"content": SOURCE, "changeSummary": "过期"}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"
