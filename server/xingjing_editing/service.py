from __future__ import annotations

from .contracts import (
    AccessContext,
    AddTrackCommand,
    AuditEvent,
    AuditOutcome,
    ClipInput,
    ClipReference,
    CreateTimelineCommand,
    OutputPolicy,
    Permission,
    PreviewSummary,
    ReplaceClipCommand,
    SourceMediaVersion,
    TimelineTrack,
    TimelineVersion,
    UpdateClipCommand,
    UpdateOutputPolicyCommand,
    build_preview_summary,
    canonical_sha256,
    timeline_content_sha256,
    validate_idempotency_key,
)
from .errors import (
    ClipNotFound,
    CrossScopeReference,
    PermissionDenied,
    SourceVersionNotFound,
    TimelineNotFound,
    TrackNotFound,
    VersionConflict,
)
from .ports import AuditRecorder, Clock, MediaVersionCatalog, StableIdGenerator, TimelineRepository


class TimelineService:
    def __init__(
        self,
        *,
        repository: TimelineRepository,
        media_catalog: MediaVersionCatalog,
        audit: AuditRecorder,
        ids: StableIdGenerator,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._media_catalog = media_catalog
        self._audit = audit
        self._ids = ids
        self._clock = clock

    async def _require_manage(self, context: AccessContext, *, action: str, object_id: str) -> None:
        if Permission.FINAL_MANAGE in context.permissions:
            return
        await self._audit.record(
            AuditEvent(
                event_id=self._ids.new_id("audit"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                action=action,
                object_type="timeline",
                object_id=object_id,
                outcome=AuditOutcome.DENIED,
                occurred_at=self._clock.now(),
            )
        )
        raise PermissionDenied()

    async def _require_view(self, context: AccessContext, *, action: str, object_id: str) -> None:
        if context.permissions.intersection({Permission.FINAL_VIEW, Permission.FINAL_MANAGE}):
            return
        await self._audit.record(
            AuditEvent(
                event_id=self._ids.new_id("audit"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                action=action,
                object_type="timeline",
                object_id=object_id,
                outcome=AuditOutcome.DENIED,
                occurred_at=self._clock.now(),
            )
        )
        raise PermissionDenied()

    @staticmethod
    def _source_is_authorized(source: SourceMediaVersion, *, context: AccessContext, project_id: str) -> bool:
        return (
            source.tenant_id == context.tenant_id
            and source.workspace_id == context.workspace_id
            and (source.owner_project_id == project_id or project_id in source.allowed_project_ids)
        )

    async def _resolve_clip(
        self,
        context: AccessContext,
        *,
        project_id: str,
        clip: ClipInput,
    ) -> ClipReference:
        source = await self._media_catalog.get_version(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            version_id=clip.source_version_id,
        )
        if source is None:
            raise SourceVersionNotFound()
        if not self._source_is_authorized(source, context=context, project_id=project_id):
            raise CrossScopeReference()
        if clip.source_out_ms > source.duration_ms:
            raise ValueError("片段源范围超出不可变媒体版本时长")
        return ClipReference(
            clip_id=clip.clip_id,
            shot_id=clip.shot_id,
            source_asset_id=source.asset_id,
            source_version_id=source.version_id,
            source_sha256=source.content_sha256,
            source_object_key=source.object_key,
            source_in_ms=clip.source_in_ms,
            source_out_ms=clip.source_out_ms,
            timeline_start_ms=clip.timeline_start_ms,
            timeline_end_ms=clip.timeline_end_ms,
            volume_milli=clip.volume_milli,
            effects=clip.effects,
        )

    async def create_timeline(
        self,
        context: AccessContext,
        command: CreateTimelineCommand,
        *,
        idempotency_key: str,
    ) -> TimelineVersion:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="timeline.create", object_id=command.project_id)
        tracks: list[TimelineTrack] = []
        for track_input in command.tracks:
            for clip in track_input.clips:
                source = await self._media_catalog.get_version(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    version_id=clip.source_version_id,
                )
                if source is None or source.media_kind is not track_input.kind:
                    raise CrossScopeReference()
            clips = tuple(
                [
                    await self._resolve_clip(context, project_id=command.project_id, clip=clip)
                    for clip in track_input.clips
                ]
            )
            tracks.append(TimelineTrack(track_id=track_input.track_id, kind=track_input.kind, clips=clips))
        frozen_tracks = tuple(tracks)
        timeline = TimelineVersion(
            timeline_id=self._ids.new_id("timeline"),
            version_id=self._ids.new_id("timeline_version"),
            final_video_id=self._ids.new_id("final_video"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=command.project_id,
            episode_id=command.episode_id,
            revision=1,
            tracks=frozen_tracks,
            output_policy=OutputPolicy(),
            content_sha256=timeline_content_sha256(
                project_id=command.project_id,
                episode_id=command.episode_id,
                tracks=frozen_tracks,
                output_policy=OutputPolicy(),
            ),
            created_by=context.actor_id,
            created_at=self._clock.now(),
        )
        request_fingerprint = canonical_sha256(command.model_dump(mode="json"))
        stored, created = await self._repository.create_timeline(
            timeline,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if created:
            await self._audit.record(
                AuditEvent(
                    event_id=self._ids.new_id("audit"),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=stored.project_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action="timeline.create",
                    object_type="timeline",
                    object_id=stored.timeline_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    occurred_at=self._clock.now(),
                    after_sha256=stored.content_sha256,
                    details={"version_id": stored.version_id, "revision": stored.revision},
                )
            )
        return stored

    async def replace_clip(
        self,
        context: AccessContext,
        command: ReplaceClipCommand,
        *,
        idempotency_key: str,
    ) -> TimelineVersion:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="timeline.clip.replace", object_id=command.timeline_id)
        current = await self._repository.get_current_timeline(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=command.timeline_id,
        )
        if current is None or current.project_id != command.project_id:
            raise TimelineNotFound()
        if current.revision != command.expected_revision:
            raise VersionConflict(expected_version=command.expected_revision, current_version=current.revision)

        selected_track = next((track for track in current.tracks if track.track_id == command.track_id), None)
        if selected_track is None:
            raise TrackNotFound()
        selected_clip = next((clip for clip in selected_track.clips if clip.clip_id == command.clip_id), None)
        if selected_clip is None:
            raise ClipNotFound()
        replacement_source = await self._media_catalog.get_version(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            version_id=command.source_version_id,
        )
        if replacement_source is None or replacement_source.media_kind is not selected_track.kind:
            raise CrossScopeReference()
        replacement = await self._resolve_clip(
            context,
            project_id=command.project_id,
            clip=ClipInput(
                clip_id=selected_clip.clip_id,
                shot_id=selected_clip.shot_id,
                source_version_id=command.source_version_id,
                source_in_ms=command.source_in_ms,
                source_out_ms=command.source_out_ms,
                timeline_start_ms=selected_clip.timeline_start_ms,
                timeline_end_ms=selected_clip.timeline_end_ms,
                volume_milli=selected_clip.volume_milli,
                effects=selected_clip.effects,
            ),
        )
        successor_tracks = tuple(
            TimelineTrack(
                track_id=track.track_id,
                kind=track.kind,
                clips=tuple(replacement if clip.clip_id == command.clip_id else clip for clip in track.clips),
            )
            if track.track_id == command.track_id
            else track
            for track in current.tracks
        )
        successor = TimelineVersion(
            timeline_id=current.timeline_id,
            version_id=self._ids.new_id("timeline_version"),
            final_video_id=current.final_video_id,
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            project_id=current.project_id,
            episode_id=current.episode_id,
            revision=current.revision + 1,
            parent_version_id=current.version_id,
            tracks=successor_tracks,
            output_policy=current.output_policy,
            content_sha256=timeline_content_sha256(
                project_id=current.project_id,
                episode_id=current.episode_id,
                tracks=successor_tracks,
                output_policy=current.output_policy,
            ),
            created_by=context.actor_id,
            created_at=self._clock.now(),
        )
        request_fingerprint = canonical_sha256(command.model_dump(mode="json"))
        stored, created = await self._repository.append_timeline(
            successor,
            expected_revision=command.expected_revision,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if created:
            await self._audit.record(
                AuditEvent(
                    event_id=self._ids.new_id("audit"),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=stored.project_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action="timeline.clip.replace",
                    object_type="timeline",
                    object_id=stored.timeline_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    occurred_at=self._clock.now(),
                    before_sha256=current.content_sha256,
                    after_sha256=stored.content_sha256,
                    details={
                        "clip_id": command.clip_id,
                        "from_version_id": current.version_id,
                        "to_version_id": stored.version_id,
                    },
                )
            )
        return stored

    async def update_clip(
        self,
        context: AccessContext,
        command: UpdateClipCommand,
        *,
        idempotency_key: str,
    ) -> TimelineVersion:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="timeline.clip.update", object_id=command.timeline_id)
        current = await self._repository.get_current_timeline(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=command.timeline_id,
        )
        if current is None or current.project_id != command.project_id:
            raise TimelineNotFound()
        if current.revision != command.expected_revision:
            raise VersionConflict(expected_version=command.expected_revision, current_version=current.revision)
        selected_track = next((track for track in current.tracks if track.track_id == command.track_id), None)
        if selected_track is None:
            raise TrackNotFound()
        selected_clip = next((clip for clip in selected_track.clips if clip.clip_id == command.clip_id), None)
        if selected_clip is None:
            raise ClipNotFound()
        source = await self._media_catalog.get_version(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            version_id=selected_clip.source_version_id,
        )
        if source is None:
            raise SourceVersionNotFound()
        if command.source_out_ms > source.duration_ms:
            raise ValueError("片段源范围超出不可变媒体版本时长")
        updated = selected_clip.model_copy(
            update={
                "source_in_ms": command.source_in_ms,
                "source_out_ms": command.source_out_ms,
                "timeline_start_ms": command.timeline_start_ms,
                "timeline_end_ms": command.timeline_end_ms,
                "volume_milli": command.volume_milli,
                "effects": command.effects,
            }
        )
        successor_tracks = tuple(
            TimelineTrack(
                track_id=track.track_id,
                kind=track.kind,
                clips=tuple(
                    sorted(
                        (updated if clip.clip_id == command.clip_id else clip for clip in track.clips),
                        key=lambda clip: (clip.timeline_start_ms, clip.timeline_end_ms, clip.clip_id),
                    )
                ),
            )
            if track.track_id == command.track_id
            else track
            for track in current.tracks
        )
        successor = TimelineVersion(
            timeline_id=current.timeline_id,
            version_id=self._ids.new_id("timeline_version"),
            final_video_id=current.final_video_id,
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            project_id=current.project_id,
            episode_id=current.episode_id,
            revision=current.revision + 1,
            parent_version_id=current.version_id,
            tracks=successor_tracks,
            output_policy=current.output_policy,
            content_sha256=timeline_content_sha256(
                project_id=current.project_id,
                episode_id=current.episode_id,
                tracks=successor_tracks,
                output_policy=current.output_policy,
            ),
            created_by=context.actor_id,
            created_at=self._clock.now(),
        )
        request_fingerprint = canonical_sha256(command.model_dump(mode="json"))
        stored, created = await self._repository.append_timeline(
            successor,
            expected_revision=command.expected_revision,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if created:
            await self._audit.record(
                AuditEvent(
                    event_id=self._ids.new_id("audit"),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=stored.project_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action="timeline.clip.update",
                    object_type="timeline",
                    object_id=stored.timeline_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    occurred_at=self._clock.now(),
                    before_sha256=current.content_sha256,
                    after_sha256=stored.content_sha256,
                    details={
                        "clip_id": command.clip_id,
                        "from_version_id": current.version_id,
                        "to_version_id": stored.version_id,
                    },
                )
            )
        return stored

    async def update_output_policy(
        self,
        context: AccessContext,
        command: UpdateOutputPolicyCommand,
        *,
        idempotency_key: str,
    ) -> TimelineVersion:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="timeline.output_policy.update", object_id=command.timeline_id)
        current = await self._repository.get_current_timeline(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=command.timeline_id,
        )
        if current is None or current.project_id != command.project_id:
            raise TimelineNotFound()
        if current.revision != command.expected_revision:
            raise VersionConflict(expected_version=command.expected_revision, current_version=current.revision)
        successor = TimelineVersion(
            timeline_id=current.timeline_id,
            version_id=self._ids.new_id("timeline_version"),
            final_video_id=current.final_video_id,
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            project_id=current.project_id,
            episode_id=current.episode_id,
            revision=current.revision + 1,
            parent_version_id=current.version_id,
            tracks=current.tracks,
            output_policy=command.policy,
            content_sha256=timeline_content_sha256(
                project_id=current.project_id,
                episode_id=current.episode_id,
                tracks=current.tracks,
                output_policy=command.policy,
            ),
            created_by=context.actor_id,
            created_at=self._clock.now(),
        )
        request_fingerprint = canonical_sha256(command.model_dump(mode="json"))
        stored, created = await self._repository.append_timeline(
            successor,
            expected_revision=command.expected_revision,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if created:
            await self._audit.record(
                AuditEvent(
                    event_id=self._ids.new_id("audit"),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=stored.project_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action="timeline.output_policy.update",
                    object_type="timeline",
                    object_id=stored.timeline_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    occurred_at=self._clock.now(),
                    before_sha256=current.content_sha256,
                    after_sha256=stored.content_sha256,
                    details={
                        "from_version_id": current.version_id,
                        "to_version_id": stored.version_id,
                        "export_template": command.policy.export_template,
                        "aigc_label_enabled": command.policy.aigc_label_enabled,
                    },
                )
            )
        return stored

    async def add_track(
        self,
        context: AccessContext,
        command: AddTrackCommand,
        *,
        idempotency_key: str,
    ) -> TimelineVersion:
        validate_idempotency_key(idempotency_key)
        await self._require_manage(context, action="timeline.track.add", object_id=command.timeline_id)
        current = await self._repository.get_current_timeline(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=command.timeline_id,
        )
        if current is None or current.project_id != command.project_id:
            raise TimelineNotFound()
        if current.revision != command.expected_revision:
            raise VersionConflict(expected_version=command.expected_revision, current_version=current.revision)
        if any(track.track_id == command.track.track_id for track in current.tracks):
            raise VersionConflict(expected_version=command.expected_revision, current_version=current.revision)
        clips = tuple(
            [
                await self._resolve_clip(context, project_id=command.project_id, clip=clip)
                for clip in command.track.clips
            ]
        )
        for clip in command.track.clips:
            source = await self._media_catalog.get_version(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                version_id=clip.source_version_id,
            )
            if source is None or source.media_kind is not command.track.kind:
                raise CrossScopeReference()
        successor_tracks = (
            *current.tracks,
            TimelineTrack(track_id=command.track.track_id, kind=command.track.kind, clips=clips),
        )
        successor = TimelineVersion(
            timeline_id=current.timeline_id,
            version_id=self._ids.new_id("timeline_version"),
            final_video_id=current.final_video_id,
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            project_id=current.project_id,
            episode_id=current.episode_id,
            revision=current.revision + 1,
            parent_version_id=current.version_id,
            tracks=successor_tracks,
            output_policy=current.output_policy,
            content_sha256=timeline_content_sha256(
                project_id=current.project_id,
                episode_id=current.episode_id,
                tracks=successor_tracks,
                output_policy=current.output_policy,
            ),
            created_by=context.actor_id,
            created_at=self._clock.now(),
        )
        request_fingerprint = canonical_sha256(command.model_dump(mode="json"))
        stored, created = await self._repository.append_timeline(
            successor,
            expected_revision=command.expected_revision,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if created:
            await self._audit.record(
                AuditEvent(
                    event_id=self._ids.new_id("audit"),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=stored.project_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action="timeline.track.add",
                    object_type="timeline",
                    object_id=stored.timeline_id,
                    outcome=AuditOutcome.SUCCEEDED,
                    occurred_at=self._clock.now(),
                    before_sha256=current.content_sha256,
                    after_sha256=stored.content_sha256,
                    details={"track_id": command.track.track_id, "kind": command.track.kind.value},
                )
            )
        return stored

    async def get_preview(
        self,
        context: AccessContext,
        *,
        project_id: str,
        timeline_id: str,
    ) -> PreviewSummary:
        await self._require_view(context, action="timeline.preview.view", object_id=timeline_id)
        timeline = await self._repository.get_current_timeline(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=timeline_id,
        )
        if timeline is None or timeline.project_id != project_id:
            raise TimelineNotFound()
        return build_preview_summary(timeline)

    async def get_timeline(
        self,
        context: AccessContext,
        *,
        project_id: str,
        timeline_id: str,
    ) -> TimelineVersion:
        await self._require_view(context, action="timeline.read", object_id=timeline_id)
        timeline = await self._repository.get_current_timeline(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            timeline_id=timeline_id,
        )
        if timeline is None or timeline.project_id != project_id:
            raise TimelineNotFound()
        return timeline

    async def list_previews(
        self,
        context: AccessContext,
        *,
        project_id: str,
        episode_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[PreviewSummary, ...]:
        await self._require_view(context, action="timeline.list", object_id=project_id)
        timelines = await self._repository.list_current_timelines(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=project_id,
            episode_id=episode_id,
            offset=offset,
            limit=limit,
        )
        return tuple(build_preview_summary(timeline) for timeline in timelines)

    async def list_source_versions(
        self,
        context: AccessContext,
        *,
        project_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[SourceMediaVersion, ...]:
        await self._require_view(context, action="timeline.source.list", object_id=project_id)
        return await self._media_catalog.list_versions(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=project_id,
            offset=offset,
            limit=limit,
        )
