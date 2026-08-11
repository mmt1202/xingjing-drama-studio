from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server.xingjing_editing import (
    AccessContext,
    Permission,
    TimelineService,
    TimelineTrack,
    TimelineVersion,
    TrackKind,
)
from server.xingjing_editing.contracts import timeline_content_sha256
from server.xingjing_editing_http import create_editing_dependencies, create_editing_router

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def new_id(self, kind: str) -> str:
        self._next += 1
        return f"{kind}-{self._next}"


class AuditRecorderStub:
    async def record(self, event: object) -> None:
        del event


class MediaCatalogStub:
    async def get_version(self, *, tenant_id: str, workspace_id: str, version_id: str) -> None:
        del tenant_id, workspace_id, version_id
        return None


class TimelineRepositoryStub:
    def __init__(self, current: TimelineVersion) -> None:
        self.current = current

    async def create_timeline(
        self,
        timeline: TimelineVersion,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]:
        del idempotency_key, request_fingerprint
        self.current = timeline
        return timeline, True

    async def get_current_timeline(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
    ) -> TimelineVersion | None:
        if (tenant_id, workspace_id, timeline_id) != (
            self.current.tenant_id,
            self.current.workspace_id,
            self.current.timeline_id,
        ):
            return None
        return self.current

    async def append_timeline(
        self,
        timeline: TimelineVersion,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]:
        del expected_revision, idempotency_key, request_fingerprint
        self.current = timeline
        return timeline, True


def _timeline() -> TimelineVersion:
    tracks = (TimelineTrack(track_id="track-video", kind=TrackKind.VIDEO, clips=()),)
    return TimelineVersion(
        timeline_id="timeline-1",
        version_id="timeline-version-4",
        final_video_id="final-video-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        project_id="project-1",
        episode_id="episode-1",
        revision=4,
        parent_version_id="timeline-version-3",
        tracks=tracks,
        content_sha256=timeline_content_sha256(project_id="project-1", episode_id="episode-1", tracks=tracks),
        created_by="editor-1",
        created_at=NOW,
    )


def _access_context(request: Request) -> AccessContext:
    del request
    return AccessContext(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        actor_id="editor-1",
        request_id="request-1",
        permissions=frozenset({Permission.FINAL_VIEW}),
    )


def test_read_timeline_returns_versioned_domain_preview() -> None:
    service = TimelineService(
        repository=TimelineRepositoryStub(_timeline()),
        media_catalog=MediaCatalogStub(),
        audit=AuditRecorderStub(),
        ids=SequenceIds(),
        clock=FixedClock(),
    )
    dependencies = create_editing_dependencies(
        timeline_service=service,
        access_context_resolver=_access_context,
    )
    app = FastAPI()
    app.include_router(create_editing_router(dependencies))

    response = TestClient(app).get("/api/v1/projects/project-1/timelines/timeline-1")

    assert response.status_code == 200
    assert response.json() == {
        "timeline_id": "timeline-1",
        "timeline_version_id": "timeline-version-4",
        "timeline_revision": 4,
        "composition_sha256": _timeline().content_sha256,
        "input_snapshot_sha256": response.json()["input_snapshot_sha256"],
        "duration_ms": 0,
        "track_count": 1,
        "clip_count": 0,
        "source_version_ids": [],
    }


def test_read_timeline_enforces_current_permission_and_workspace_scope() -> None:
    service = TimelineService(
        repository=TimelineRepositoryStub(_timeline()),
        media_catalog=MediaCatalogStub(),
        audit=AuditRecorderStub(),
        ids=SequenceIds(),
        clock=FixedClock(),
    )

    def resolve_context(request: Request) -> AccessContext:
        return AccessContext(
            tenant_id="tenant-1",
            workspace_id=request.headers.get("X-Workspace-ID", "workspace-1"),
            actor_id="editor-1",
            request_id="request-1",
            permissions=frozenset(
                {Permission.FINAL_VIEW} if request.headers.get("X-Permissions") == "final.view" else set()
            ),
        )

    dependencies = create_editing_dependencies(
        timeline_service=service,
        access_context_resolver=resolve_context,
    )
    app = FastAPI()
    app.include_router(create_editing_router(dependencies))
    client = TestClient(app)

    denied = client.get("/api/v1/projects/project-1/timelines/timeline-1")
    hidden = client.get(
        "/api/v1/projects/project-1/timelines/timeline-1",
        headers={"X-Permissions": "final.view", "X-Workspace-ID": "workspace-2"},
    )

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PERMISSION_DENIED"
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "TIMELINE_NOT_FOUND"
