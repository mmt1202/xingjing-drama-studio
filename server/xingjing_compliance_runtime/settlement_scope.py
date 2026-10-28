"""Resolve billing from immutable delivery inputs, never from a project's task list."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.xingjing_audio_persistence.models import (
    audio_billing_holds,
    audio_track_versions,
    audit_events,
    generation_tasks,
    idempotency_records,
    lip_sync_versions,
    subtitle_track_versions,
)
from server.xingjing_editing import FinalVideoVersion
from server.xingjing_editing.contracts import TimelineVersion, build_preview_summary
from server.xingjing_editing.rendering import RenderTask
from server.xingjing_editing_persistence.models import (
    EditingRenderBillingHoldRow,
    FinalVideoVersionRow,
    RenderTaskRow,
    TimelineVersionRow,
)
from server.xingjing_generation.contracts import GenerationTask
from server.xingjing_generation_persistence.repository import GeneratedAssetRow, GenerationBillingHoldRow, TaskRow
from server.xingjing_platform_persistence.persistence import ProjectRow

from .formal_export import FormalExportArtifactError

Hold = tuple[str, int, int, int, str, datetime]


@dataclass(frozen=True)
class ExportSettlementScope:
    sources: tuple[tuple[str, list[Hold]], ...]
    references: tuple[tuple[str, str, str], ...]


async def resolve_export_settlement(
    session: AsyncSession, *, tenant_id: str, workspace_id: str, project_id: str, version_id: str
) -> ExportSettlementScope:
    resolver = _Resolver(session, tenant_id, workspace_id)
    await resolver.final(version_id, required_project=project_id)
    return ExportSettlementScope(
        tuple((source, rows) for source, rows in sorted(resolver.holds.items())), tuple(sorted(resolver.references))
    )


async def verify_release_settlement(
    session: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    creative: dict[str, object],
) -> None:
    """A package's optional cover has its own costs, outside the final-video receipt."""
    resolver = _Resolver(session, tenant_id, workspace_id)
    cover = creative.get("cover")
    if not isinstance(cover, dict):
        resolver.reject()
    row = await session.scalar(
        select(GeneratedAssetRow).where(
            GeneratedAssetRow.workspace_id == workspace_id,
            GeneratedAssetRow.project_id == project_id,
            GeneratedAssetRow.asset_id == cover.get("assetId"),
            GeneratedAssetRow.task_id == cover.get("taskId"),
        )
    )
    if row is None:
        resolver.reject()
    resolver.match((row.object_key, row.content_sha256), (cover.get("objectKey"), cover.get("contentSha256")))
    await resolver.generated(row)


class _Resolver:
    def __init__(self, session: AsyncSession, tenant_id: str, workspace_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.holds: dict[str, list[Hold]] = {"generation": [], "audio": [], "editing": []}
        self.references: set[tuple[str, str, str]] = set()
        self.active: set[tuple[str, str]] = set()
        self.done: set[tuple[str, str]] = set()
        self.projects: set[str] = set()
        self.audio_tasks: dict[str, list[Mapping[str, Any]]] = {}

    def scope(self, table: Any, project_id: str | None = None) -> tuple[Any, ...]:
        columns = table.c if hasattr(table, "c") else table
        result = (columns.tenant_id == self.tenant_id, columns.workspace_id == self.workspace_id)
        return result if project_id is None else (*result, columns.project_id == project_id)

    async def project(self, project_id: str) -> None:
        if project_id not in self.projects:
            found = await self.session.scalar(
                select(ProjectRow.id).where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == self.tenant_id,
                    ProjectRow.workspace_id == self.workspace_id,
                )
            )
            if found is None:
                self.reject()
            self.projects.add(project_id)

    def enter(self, kind: str, identity: str) -> bool:
        key = (kind, identity)
        if key in self.active:
            raise FormalExportArtifactError("EXPORT_BILLING_SOURCE_CYCLE")
        if key in self.done:
            return False
        if len(self.active) >= 64 or len(self.done) >= 10000:
            self.reject()
        self.active.add(key)
        return True

    def leave(self, kind: str, identity: str) -> None:
        self.active.remove((kind, identity))
        self.done.add((kind, identity))

    @staticmethod
    def reject(code: str = "EXPORT_BILLING_SOURCE_UNRESOLVED") -> Never:
        raise FormalExportArtifactError(code)

    @staticmethod
    def match(actual: tuple[object, ...], expected: tuple[object, ...]) -> None:
        if actual != expected:
            _Resolver.reject("EXPORT_BILLING_SOURCE_MISMATCH")

    def hold(self, source: str, project_id: str, task_id: str, row: Any) -> None:
        reference = (source, project_id, task_id)
        if reference in self.references:
            return
        if row is None:
            self.reject("EXPORT_BILLING_EVIDENCE_MISSING")
        values = row if isinstance(row, Mapping) else vars(row)
        if values["status"] == "active":
            raise ValueError("PROJECT_BILLING_NOT_SETTLED")
        # A released reservation is not evidence that a successful output was paid.
        if values["status"] != "settled" or not values["terminal_event_id"]:
            self.reject("EXPORT_BILLING_EVIDENCE_MISSING")
        estimated, actual, released = (
            int(values[name]) for name in ("estimated_minor", "actual_minor", "released_minor")
        )
        if min(actual, released) < 0 or estimated <= 0 or actual + released != estimated:
            self.reject("EXPORT_BILLING_EVIDENCE_MISSING")
        updated = values["updated_at"]
        self.holds[source].append(
            (
                values["currency"],
                estimated,
                actual,
                released,
                "settled",
                updated.replace(tzinfo=UTC) if updated.tzinfo is None else updated,
            )
        )
        self.references.add(reference)

    async def final(self, version_id: str, *, required_project: str | None = None) -> FinalVideoVersion:
        row = await self.session.scalar(
            select(FinalVideoVersionRow).where(
                *self.scope(FinalVideoVersionRow), FinalVideoVersionRow.version_id == version_id
            )
        )
        if row is None:
            self.reject()
        assert row is not None
        try:
            final = FinalVideoVersion.model_validate(row.snapshot)
        except ValueError as error:
            raise FormalExportArtifactError("EXPORT_BILLING_SOURCE_MISMATCH") from error
        self.match(
            (
                final.tenant_id,
                final.workspace_id,
                final.project_id,
                final.version_id,
                final.timeline_id,
                final.timeline_version_id,
                final.render_task_id,
            ),
            (
                self.tenant_id,
                self.workspace_id,
                row.project_id,
                row.version_id,
                row.timeline_id,
                row.timeline_version_id,
                row.render_task_id,
            ),
        )
        if required_project is not None:
            self.match((final.project_id,), (required_project,))
        await self.project(final.project_id)
        if not self.enter("final", version_id):
            return final
        render = await self.session.scalar(
            select(RenderTaskRow).where(
                *self.scope(RenderTaskRow, final.project_id), RenderTaskRow.task_id == final.render_task_id
            )
        )
        if render is None:
            self.reject()
        assert render is not None
        try:
            render_task = RenderTask.model_validate(render.snapshot)
        except ValueError as error:
            raise FormalExportArtifactError("EXPORT_BILLING_SOURCE_MISMATCH") from error
        self.match(
            (render.status, render.timeline_id, render.timeline_version_id, render.output_version_id),
            ("succeeded", final.timeline_id, final.timeline_version_id, final.version_id),
        )
        self.match(
            (
                render_task.tenant_id,
                render_task.workspace_id,
                render_task.project_id,
                render_task.task_id,
                render_task.timeline_id,
                render_task.timeline_version_id,
                render_task.output_version_id,
                render_task.status.value,
                render_task.preview,
                render_task.profile,
            ),
            (
                self.tenant_id,
                self.workspace_id,
                final.project_id,
                final.render_task_id,
                final.timeline_id,
                final.timeline_version_id,
                final.version_id,
                "succeeded",
                final.preview,
                final.profile,
            ),
        )
        hold = await self.session.scalar(
            select(EditingRenderBillingHoldRow).where(
                *self.scope(EditingRenderBillingHoldRow, final.project_id),
                EditingRenderBillingHoldRow.task_id == final.render_task_id,
            )
        )
        self.hold("editing", final.project_id, final.render_task_id, hold)
        timeline_row = await self.session.scalar(
            select(TimelineVersionRow).where(
                *self.scope(TimelineVersionRow, final.project_id),
                TimelineVersionRow.timeline_id == final.timeline_id,
                TimelineVersionRow.version_id == final.timeline_version_id,
            )
        )
        if timeline_row is None:
            self.reject()
        assert timeline_row is not None
        try:
            timeline = TimelineVersion.model_validate(timeline_row.snapshot)
        except ValueError as error:
            raise FormalExportArtifactError("EXPORT_BILLING_SOURCE_MISMATCH") from error
        self.match(
            (
                timeline.tenant_id,
                timeline.workspace_id,
                timeline.project_id,
                timeline.timeline_id,
                timeline.version_id,
                timeline.content_sha256,
            ),
            (
                self.tenant_id,
                self.workspace_id,
                final.project_id,
                final.timeline_id,
                final.timeline_version_id,
                final.preview.composition_sha256,
            ),
        )
        self.match((build_preview_summary(timeline),), (final.preview,))
        clips = [clip for track in timeline.tracks for clip in track.clips]
        if not clips:
            self.reject()
        for clip in clips:
            if not clip.source_object_key:
                self.reject()
            await self.media(clip.source_version_id, clip.source_asset_id, clip.source_object_key, clip.source_sha256)
        self.leave("final", version_id)
        return final

    async def media(
        self, version_id: str, asset_id: str | None = None, object_key: str | None = None, digest: str | None = None
    ) -> None:
        generated = await self.session.scalar(
            select(GeneratedAssetRow).where(
                GeneratedAssetRow.workspace_id == self.workspace_id, GeneratedAssetRow.asset_id == version_id
            )
        )
        if generated is not None:
            self._match_media(
                generated.asset_id, generated.object_key, generated.content_sha256, asset_id, object_key, digest
            )
            await self.generated(generated)
            return
        for table, kind in (
            (audio_track_versions, "audio"),
            (subtitle_track_versions, "subtitle"),
            (lip_sync_versions, "lip"),
        ):
            row = (
                (await self.session.execute(select(table).where(*self.scope(table), table.c.id == version_id)))
                .mappings()
                .one_or_none()
            )
            if row is not None:
                source_key = row["output_video_key"] if kind == "lip" else row["object_key"]
                source_digest = row["output_sha256"] if kind == "lip" else row["object_sha256"]
                self._match_media(
                    row["id"] if kind == "lip" else row["track_id"],
                    source_key,
                    source_digest,
                    asset_id,
                    object_key,
                    digest,
                )
                await self.audio(dict(row), kind)
                return
        final = await self.final(version_id)
        self._match_media(
            final.final_video_id, final.output.object_key, final.output.content_sha256, asset_id, object_key, digest
        )

    def _match_media(
        self,
        actual_asset: object,
        actual_key: object,
        actual_digest: object,
        asset: str | None,
        key: str | None,
        digest: str | None,
    ) -> None:
        if not actual_key or not actual_digest:
            self.reject()
        for actual, expected in ((actual_asset, asset), (actual_key, key), (actual_digest, digest)):
            if expected is not None:
                self.match((actual,), (expected,))

    async def generated(self, asset: GeneratedAssetRow) -> None:
        await self.project(asset.project_id)
        if not self.enter("generated", asset.asset_id):
            return
        task = await self.session.scalar(
            select(TaskRow).where(
                TaskRow.workspace_id == self.workspace_id,
                TaskRow.project_id == asset.project_id,
                TaskRow.task_id == asset.task_id,
            )
        )
        if task is None or task.status != "succeeded":
            self.reject()
        assert task is not None
        try:
            generation_task = GenerationTask.model_validate(task.snapshot)
        except ValueError as error:
            raise FormalExportArtifactError("EXPORT_BILLING_SOURCE_MISMATCH") from error
        request = generation_task.request.model_dump(mode="json")
        self.match((generation_task.task_id, generation_task.status.value), (asset.task_id, "succeeded"))
        self.match((request.get("workspace_id"), request.get("project_id")), (self.workspace_id, asset.project_id))
        if asset.asset_id not in task.snapshot.get("output_asset_ids", []):
            self.reject("EXPORT_BILLING_SOURCE_MISMATCH")
        hold = await self.session.scalar(
            select(GenerationBillingHoldRow).where(
                GenerationBillingHoldRow.workspace_id == self.workspace_id,
                GenerationBillingHoldRow.project_id == asset.project_id,
                GenerationBillingHoldRow.task_id == asset.task_id,
            )
        )
        self.hold("generation", asset.project_id, asset.task_id, hold)
        resolved = request.get("resolved_input_assets")
        if resolved:
            if not isinstance(resolved, list):
                self.reject()
            for source in resolved:
                if not isinstance(source, dict) or not source.get("generated_asset_id"):
                    self.reject()
                dependency = await self.session.scalar(
                    select(GeneratedAssetRow).where(
                        GeneratedAssetRow.workspace_id == self.workspace_id,
                        GeneratedAssetRow.asset_id == source["generated_asset_id"],
                    )
                )
                if dependency is None:
                    self.reject()
                assert dependency is not None
                self.match(
                    (dependency.project_id, dependency.task_id, dependency.object_key, dependency.content_sha256),
                    (
                        source.get("source_project_id"),
                        source.get("source_task_id"),
                        source.get("object_key"),
                        source.get("content_sha256"),
                    ),
                )
                await self.generated(dependency)
        else:
            for input_id in request.get("input_asset_ids", []):
                # Legacy input IDs are usable only when they already identify an immutable output.
                dependency = await self.session.scalar(
                    select(GeneratedAssetRow).where(
                        GeneratedAssetRow.workspace_id == self.workspace_id,
                        GeneratedAssetRow.asset_id == input_id,
                    )
                )
                if dependency is None:
                    self.reject()
                assert dependency is not None
                await self.generated(dependency)
        self.leave("generated", asset.asset_id)

    async def _tasks(self, project_id: str) -> list[Mapping[str, Any]]:
        if project_id not in self.audio_tasks:
            self.audio_tasks[project_id] = [
                dict(row)
                for row in (
                    (
                        await self.session.execute(
                            select(generation_tasks).where(*self.scope(generation_tasks, project_id))
                        )
                    ).mappings()
                )
            ]
        return self.audio_tasks[project_id]

    async def audio(self, row: Mapping[str, Any], kind: str) -> None:
        project_id, version_id = str(row["project_id"]), str(row["id"])
        await self.project(project_id)
        if not self.enter(kind, version_id):
            return
        key = row["output_video_key"] if kind == "lip" else row["object_key"]
        digest = row["output_sha256"] if kind == "lip" else row["object_sha256"]
        task_id = row["source_task_id"]
        tasks = await self._tasks(project_id)
        producers = [task for task in tasks if self._produces(task, key, digest)]
        if task_id:
            producer = next((task for task in producers if task["id"] == task_id), None)
            if producer is None:
                self.reject()
            await self.audio_hold(project_id, producer)
        elif producers:
            # A stored callback can recover omitted linkage without selecting a newer track revision.
            for producer in producers:
                await self.audio_hold(project_id, producer)
        elif kind == "lip":
            self.reject()
        else:
            await self.manual_audio(row, kind, tasks)
        if kind == "lip":
            if row["state"] != "ready":
                self.reject()
            await self.media(str(row["audio_version_id"]))
            if row["subtitle_version_id"]:
                await self.media(str(row["subtitle_version_id"]))
            await self.object_media(str(row["input_video_key"]))
        elif kind == "audio":
            payload = row["cue_payload"]
            if not isinstance(payload, dict):
                self.reject()
            segments = payload.get("segments", [])
            if not isinstance(segments, list):
                self.reject()
            for segment in segments:
                source_key = segment.get("objectKey") if isinstance(segment, dict) else None
                if not isinstance(source_key, str) or not source_key:
                    self.reject()
                if source_key != key:
                    await self.object_media(source_key)
        self.leave(kind, version_id)

    @staticmethod
    def _produces(task: Mapping[str, Any], key: object, digest: object) -> bool:
        metadata = task.get("result_metadata")
        outputs = metadata.get("outputs") if isinstance(metadata, dict) else None
        return isinstance(outputs, list) and any(
            isinstance(output, dict) and output.get("objectKey") == key and output.get("sha256") == digest
            for output in outputs
        )

    async def audio_hold(self, project_id: str, task: Mapping[str, Any]) -> None:
        if task["status"] != "succeeded":
            self.reject()
        row = (
            (
                await self.session.execute(
                    select(audio_billing_holds).where(
                        *self.scope(audio_billing_holds, project_id),
                        audio_billing_holds.c.task_id == task["id"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        self.hold("audio", project_id, str(task["id"]), row)

    async def manual_audio(self, row: Mapping[str, Any], kind: str, tasks: list[Mapping[str, Any]]) -> None:
        key = str(row["object_key"])
        prefix = f"{self.tenant_id}/{self.workspace_id}/{row['project_id']}/"
        if not key.startswith(prefix) or any(key.startswith(f"{prefix}{task['id']}/") for task in tasks):
            self.reject()
        operation = "append_audio_version" if kind == "audio" else "append_subtitle_version"
        receipt = await self.session.scalar(
            select(idempotency_records.c.resource_id)
            .where(
                *self.scope(idempotency_records, row["project_id"]),
                idempotency_records.c.operation == operation,
                idempotency_records.c.resource_id == row["id"],
            )
            .limit(1)
        )
        audit = await self.session.scalar(
            select(audit_events.c.id)
            .where(
                *self.scope(audit_events, row["project_id"]),
                audit_events.c.operation == operation,
                audit_events.c.object_id == row["id"],
                audit_events.c.actor_id == row["created_by"],
                audit_events.c.result == "succeeded",
            )
            .limit(1)
        )
        if receipt is None or audit is None:
            self.reject()
        self.references.add(("manual", str(row["project_id"]), str(row["id"])))

    async def object_media(self, object_key: str) -> None:
        identities: set[str] = set(
            await self.session.scalars(
                select(GeneratedAssetRow.asset_id).where(
                    GeneratedAssetRow.workspace_id == self.workspace_id,
                    GeneratedAssetRow.object_key == object_key,
                )
            )
        )
        for table, column in (
            (audio_track_versions, "object_key"),
            (subtitle_track_versions, "object_key"),
            (lip_sync_versions, "output_video_key"),
        ):
            identities.update(
                await self.session.scalars(select(table.c.id).where(*self.scope(table), table.c[column] == object_key))
            )
        final_rows = await self.session.scalars(select(FinalVideoVersionRow).where(*self.scope(FinalVideoVersionRow)))
        identities.update(row.version_id for row in final_rows if self._uses_object(row.snapshot, object_key))
        if not identities:
            self.reject()
        # Reused bytes may have several immutable version records. Include every proven producer.
        for identity in sorted(identities):
            await self.media(identity, object_key=object_key)

    @staticmethod
    def _uses_object(snapshot: object, object_key: str) -> bool:
        output = snapshot.get("output") if isinstance(snapshot, dict) else None
        return isinstance(output, dict) and output.get("object_key") == object_key
