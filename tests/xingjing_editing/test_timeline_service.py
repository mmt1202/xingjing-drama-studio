from __future__ import annotations

from datetime import UTC, datetime

import pytest

from server.xingjing_editing import (
    AccessContext,
    ClipInput,
    CreateTimelineCommand,
    Permission,
    PermissionDenied,
    PreviewSummary,
    ReplaceClipCommand,
    SourceMediaVersion,
    TimelineNotFound,
    TimelineService,
    TimelineTrackInput,
    TimelineVersion,
    TrackKind,
)

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequenceIds:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def new_id(self, kind: str) -> str:
        del kind
        return next(self._values)


class MediaCatalogStub:
    async def get_version(self, *, tenant_id: str, workspace_id: str, version_id: str) -> SourceMediaVersion | None:
        versions = {"video-version-7": "a", "video-version-8": "b"}
        if (tenant_id, workspace_id) != ("tenant-1", "workspace-1") or version_id not in versions:
            return None
        return SourceMediaVersion(
            asset_id="video-asset-3",
            version_id=version_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_project_id="project-1",
            allowed_project_ids=(),
            content_sha256=versions[version_id] * 64,
            duration_ms=5_000,
        )


class TimelineRepositoryStub:
    def __init__(self) -> None:
        self.current: TimelineVersion | None = None

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
        self, *, tenant_id: str, workspace_id: str, timeline_id: str
    ) -> TimelineVersion | None:
        if self.current is None:
            return None
        if (self.current.tenant_id, self.current.workspace_id, self.current.timeline_id) != (
            tenant_id,
            workspace_id,
            timeline_id,
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
        del idempotency_key, request_fingerprint
        assert self.current is not None
        assert self.current.revision == expected_revision
        self.current = timeline
        return timeline, True


class AuditRecorderStub:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record(self, event: object) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_create_timeline_binds_immutable_source_and_serializes_audited_snapshot() -> None:
    repository = TimelineRepositoryStub()
    audit = AuditRecorderStub()
    service = TimelineService(
        repository=repository,
        media_catalog=MediaCatalogStub(),
        audit=audit,
        ids=SequenceIds("timeline-1", "timeline-version-1", "final-video-1", "audit-1"),
        clock=FixedClock(),
    )
    context = AccessContext(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        actor_id="user-9",
        request_id="request-22",
        permissions=frozenset({Permission.FINAL_MANAGE}),
    )

    timeline = await service.create_timeline(
        context,
        CreateTimelineCommand(
            project_id="project-1",
            episode_id="episode-2",
            tracks=(
                TimelineTrackInput(
                    track_id="track-video-main",
                    kind=TrackKind.VIDEO,
                    clips=(
                        ClipInput(
                            clip_id="clip-shot-10",
                            shot_id="shot-10",
                            source_version_id="video-version-7",
                            source_in_ms=0,
                            source_out_ms=5_000,
                            timeline_start_ms=0,
                            timeline_end_ms=5_000,
                            volume_milli=1_000,
                            effects={"transition_out": "cut"},
                        ),
                    ),
                ),
            ),
        ),
        idempotency_key="create-final-timeline-1",
    )

    assert timeline.timeline_id == "timeline-1"
    assert timeline.version_id == "timeline-version-1"
    assert timeline.final_video_id == "final-video-1"
    assert timeline.revision == 1
    assert timeline.tracks[0].clips[0].source_version_id == "video-version-7"
    assert timeline.tracks[0].clips[0].source_sha256 == "a" * 64
    assert len(timeline.content_sha256) == 64
    assert TimelineVersion.model_validate_json(timeline.model_dump_json()) == timeline
    assert repository.current == timeline
    assert len(audit.events) == 1


@pytest.mark.asyncio
async def test_replace_clip_appends_version_without_changing_stable_timeline_or_shot_ids() -> None:
    repository = TimelineRepositoryStub()
    audit = AuditRecorderStub()
    service = TimelineService(
        repository=repository,
        media_catalog=MediaCatalogStub(),
        audit=audit,
        ids=SequenceIds(
            "timeline-1",
            "timeline-version-1",
            "final-video-1",
            "audit-1",
            "timeline-version-2",
            "audit-2",
        ),
        clock=FixedClock(),
    )
    context = AccessContext(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        actor_id="user-9",
        request_id="request-22",
        permissions=frozenset({Permission.FINAL_MANAGE}),
    )
    original = await service.create_timeline(
        context,
        CreateTimelineCommand(
            project_id="project-1",
            episode_id="episode-2",
            tracks=(
                TimelineTrackInput(
                    track_id="track-video-main",
                    kind=TrackKind.VIDEO,
                    clips=(
                        ClipInput(
                            clip_id="clip-shot-10",
                            shot_id="shot-10",
                            source_version_id="video-version-7",
                            source_in_ms=0,
                            source_out_ms=5_000,
                            timeline_start_ms=0,
                            timeline_end_ms=5_000,
                        ),
                    ),
                ),
            ),
        ),
        idempotency_key="create-final-timeline-1",
    )

    replaced = await service.replace_clip(
        context,
        ReplaceClipCommand(
            project_id="project-1",
            timeline_id=original.timeline_id,
            track_id="track-video-main",
            clip_id="clip-shot-10",
            source_version_id="video-version-8",
            source_in_ms=0,
            source_out_ms=5_000,
            expected_revision=1,
        ),
        idempotency_key="replace-shot-10-v2",
    )

    old_clip = original.tracks[0].clips[0]
    new_clip = replaced.tracks[0].clips[0]
    assert (replaced.timeline_id, replaced.tracks[0].track_id, new_clip.clip_id, new_clip.shot_id) == (
        original.timeline_id,
        original.tracks[0].track_id,
        old_clip.clip_id,
        old_clip.shot_id,
    )
    assert replaced.version_id == "timeline-version-2"
    assert replaced.parent_version_id == original.version_id
    assert replaced.revision == 2
    assert new_clip.source_version_id == "video-version-8"
    assert new_clip.source_sha256 == "b" * 64
    assert old_clip.source_version_id == "video-version-7"
    assert replaced.content_sha256 != original.content_sha256
    assert len(audit.events) == 2


@pytest.mark.asyncio
async def test_preview_is_tenant_scoped_permission_checked_and_bound_to_immutable_inputs() -> None:
    repository = TimelineRepositoryStub()
    audit = AuditRecorderStub()
    service = TimelineService(
        repository=repository,
        media_catalog=MediaCatalogStub(),
        audit=audit,
        ids=SequenceIds("timeline-1", "timeline-version-1", "final-video-1", "audit-created", "audit-denied"),
        clock=FixedClock(),
    )
    manager = AccessContext(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        actor_id="editor-1",
        request_id="request-create",
        permissions=frozenset({Permission.FINAL_MANAGE}),
    )
    timeline = await service.create_timeline(
        manager,
        CreateTimelineCommand(
            project_id="project-1",
            episode_id="episode-2",
            tracks=(
                TimelineTrackInput(
                    track_id="track-video-main",
                    kind=TrackKind.VIDEO,
                    clips=(
                        ClipInput(
                            clip_id="clip-shot-10",
                            shot_id="shot-10",
                            source_version_id="video-version-7",
                            source_in_ms=0,
                            source_out_ms=5_000,
                            timeline_start_ms=250,
                            timeline_end_ms=5_250,
                        ),
                    ),
                ),
            ),
        ),
        idempotency_key="create-preview-source",
    )
    viewer = manager.model_copy(
        update={"actor_id": "viewer-2", "request_id": "request-view", "permissions": frozenset({Permission.FINAL_VIEW})}
    )

    summary = await service.get_preview(
        viewer,
        project_id="project-1",
        timeline_id=timeline.timeline_id,
    )

    assert summary.timeline_version_id == timeline.version_id
    assert summary.composition_sha256 == timeline.content_sha256
    assert summary.duration_ms == 5_250
    assert summary.track_count == 1
    assert summary.clip_count == 1
    assert summary.source_version_ids == ("video-version-7",)
    assert len(summary.input_snapshot_sha256) == 64
    assert PreviewSummary.model_validate_json(summary.model_dump_json()) == summary

    cross_tenant = viewer.model_copy(update={"tenant_id": "tenant-2", "workspace_id": "workspace-2"})
    with pytest.raises(TimelineNotFound):
        await service.get_preview(cross_tenant, project_id="project-1", timeline_id=timeline.timeline_id)

    denied = viewer.model_copy(update={"permissions": frozenset(), "request_id": "request-denied"})
    with pytest.raises(PermissionDenied):
        await service.get_preview(denied, project_id="project-1", timeline_id=timeline.timeline_id)
    assert len(audit.events) == 2
