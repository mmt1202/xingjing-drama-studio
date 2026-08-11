from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_compliance.models import ExportManifestContext
from server.xingjing_compliance_runtime.contracts import ExportArtifact, ExportAuthorityEvidence
from server.xingjing_editing import FinalVideoVersion
from server.xingjing_editing.contracts import ClipReference, TimelineVersion
from server.xingjing_editing_persistence.models import FinalVideoVersionRow, TimelineVersionRow

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHUNK_SIZE = 1024 * 1024
_FORMAT_EXTENSIONS = {
    "mp4": "mp4",
    "subtitle_srt": "srt",
    "storyboard_csv": "csv",
    "davinci_edl": "edl",
    "premiere_xml": "xml",
    "compliance_report": "json",
    "cost_report": "json",
    "material_package": "zip",
    "project_archive": "zip",
    "jianying_draft": "zip",
    "publish_package": "zip",
}


class FormalExportArtifactError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LocalFormalExportExecutor:
    """Copies a verified M08 immutable final video into a scoped formal-delivery area."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        render_root: Path,
        export_root: Path,
    ) -> None:
        self._session_factory = session_factory
        self._render_root = render_root.resolve()
        export_root.mkdir(parents=True, exist_ok=True)
        self._export_root = export_root.resolve()
        if not self._render_root.is_dir() or not self._export_root.is_dir():
            raise FormalExportArtifactError("FORMAL_EXPORT_STORAGE_ROOT_INVALID")

    async def execute(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        project_version: str,
        output_format: str,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
    ) -> ExportArtifact:
        if output_format not in _FORMAT_EXTENSIONS:
            raise FormalExportArtifactError("FORMAL_EXPORT_FORMAT_UNSUPPORTED")
        version = await self._load_version(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=project_version,
        )
        if output_format == "mp4" and version.profile.container != "mp4":
            raise FormalExportArtifactError("FORMAL_EXPORT_SOURCE_CONTAINER_MISMATCH")
        timeline = await self._load_timeline(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            timeline_id=version.timeline_id,
            version_id=version.timeline_version_id,
        )
        return await asyncio.to_thread(
            self._generate_verified,
            tenant_id,
            workspace_id,
            project_id,
            version,
            timeline,
            output_format,
            manifest,
            evidence,
        )

    async def load_source_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        version_id: str,
    ) -> FinalVideoVersion:
        return await self._load_version(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=version_id,
        )

    async def _load_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        version_id: str,
    ) -> FinalVideoVersion:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(FinalVideoVersionRow).where(
                    FinalVideoVersionRow.tenant_id == tenant_id,
                    FinalVideoVersionRow.workspace_id == workspace_id,
                    FinalVideoVersionRow.project_id == project_id,
                    FinalVideoVersionRow.version_id == version_id,
                )
            )
        if row is None:
            raise FormalExportArtifactError("FINAL_VIDEO_VERSION_NOT_FOUND")
        return FinalVideoVersion.model_validate(row.snapshot)

    async def _load_timeline(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        timeline_id: str,
        version_id: str,
    ) -> TimelineVersion:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(TimelineVersionRow).where(
                    TimelineVersionRow.tenant_id == tenant_id,
                    TimelineVersionRow.workspace_id == workspace_id,
                    TimelineVersionRow.project_id == project_id,
                    TimelineVersionRow.timeline_id == timeline_id,
                    TimelineVersionRow.version_id == version_id,
                )
            )
        if row is None:
            raise FormalExportArtifactError("FORMAL_EXPORT_TIMELINE_VERSION_NOT_FOUND")
        return TimelineVersion.model_validate(row.snapshot)

    def _generate_verified(
        self,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        version: FinalVideoVersion,
        timeline: TimelineVersion,
        output_format: str,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
    ) -> ExportArtifact:
        source = self._resolve_scoped(self._render_root, tenant_id, workspace_id, version.output.object_key)
        if not source.is_file():
            raise FormalExportArtifactError("FINAL_VIDEO_OBJECT_NOT_FOUND")
        source_digest, source_size = _file_digest(source)
        if source_digest != version.output.content_sha256:
            raise FormalExportArtifactError("FINAL_VIDEO_DIGEST_MISMATCH")
        destination_dir = self._export_root / tenant_id / workspace_id / "exports" / project_id / manifest.request_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        extension = _FORMAT_EXTENSIONS[output_format]
        destination = destination_dir / f"formal-delivery.{extension}"
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            self._write_artifact(
                output_format=output_format,
                destination=temporary,
                source=source,
                version=version,
                timeline=timeline,
                manifest=manifest,
                evidence=evidence,
            )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        output_digest, output_size = _file_digest(destination)
        if output_format == "mp4" and (output_digest != source_digest or output_size != source_size):
            destination.unlink(missing_ok=True)
            raise FormalExportArtifactError("FORMAL_EXPORT_COPY_VERIFICATION_FAILED")
        manifest_payload = {
            "manifest": asdict(manifest),
            "policy": {"id": evidence.policy_id, "version": evidence.policy_version},
            "authorizations": list(evidence.authorization_ids),
            "aigc": {"generated": True, "declaration": "本交付包含人工智能生成或辅助生成内容"},
            "source": {
                "final_video_version": version.version_id,
                "timeline_version": timeline.version_id,
                "sha256": source_digest,
                "size_bytes": source_size,
            },
            "artifact": {
                "format": output_format,
                "filename": destination.name,
                "sha256": output_digest,
                "size_bytes": output_size,
            },
        }
        manifest_path = destination_dir / "manifest.json"
        manifest_temporary = destination_dir / f".manifest.{uuid4().hex}.tmp"
        try:
            manifest_temporary.write_text(
                json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, default=str, indent=2),
                encoding="utf-8",
            )
            os.replace(manifest_temporary, manifest_path)
        finally:
            manifest_temporary.unlink(missing_ok=True)
        object_key = destination.relative_to(self._export_root).as_posix()
        return ExportArtifact(
            format=output_format,
            object_key=object_key,
            content_sha256=output_digest,
            size_bytes=output_size,
            download_path=f"/api/v1/projects/{project_id}/exports/{manifest.request_id}/download",
        )

    def _write_artifact(
        self,
        *,
        output_format: str,
        destination: Path,
        source: Path,
        version: FinalVideoVersion,
        timeline: TimelineVersion,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
    ) -> None:
        if output_format == "mp4":
            shutil.copyfile(source, destination)
            return
        if output_format == "subtitle_srt":
            destination.write_text(_subtitle_srt(timeline), encoding="utf-8")
            return
        if output_format == "storyboard_csv":
            destination.write_text(_storyboard_csv(timeline), encoding="utf-8-sig")
            return
        if output_format == "davinci_edl":
            destination.write_text(_davinci_edl(timeline), encoding="utf-8")
            return
        if output_format == "premiere_xml":
            destination.write_bytes(_premiere_xml(timeline))
            return
        report = _compliance_report(version, timeline, manifest, evidence)
        if output_format == "compliance_report":
            destination.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            return
        if output_format == "cost_report":
            if not evidence.billing_summary:
                raise FormalExportArtifactError("FORMAL_EXPORT_COST_SUMMARY_MISSING")
            destination.write_text(
                json.dumps(
                    {
                        "schema": "xingjing.cost-report/v1",
                        "project": {"id": manifest.project_id, "version": manifest.project_version},
                        "settled": evidence.state.billing_settled,
                        "breakdown": list(evidence.billing_summary),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return
        _write_zip_package(
            destination=destination,
            package_kind=output_format,
            source=source,
            version=version,
            timeline=timeline,
            compliance_report=report,
        )

    def resolve_download(self, *, tenant_id: str, workspace_id: str, object_key: str) -> Path:
        path = self._resolve_scoped(self._export_root, tenant_id, workspace_id, object_key)
        if not path.is_file():
            raise FormalExportArtifactError("FORMAL_EXPORT_OBJECT_NOT_FOUND")
        return path

    @staticmethod
    def _resolve_scoped(root: Path, tenant_id: str, workspace_id: str, object_key: str) -> Path:
        if not _SAFE_COMPONENT.fullmatch(tenant_id) or not _SAFE_COMPONENT.fullmatch(workspace_id):
            raise FormalExportArtifactError("FORMAL_EXPORT_SCOPE_INVALID")
        if not object_key or "\\" in object_key or Path(object_key).is_absolute():
            raise FormalExportArtifactError("FORMAL_EXPORT_OBJECT_KEY_INVALID")
        parts = object_key.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise FormalExportArtifactError("FORMAL_EXPORT_OBJECT_KEY_INVALID")
        scoped_root = (root / tenant_id / workspace_id).resolve()
        path = (root.joinpath(*parts)).resolve()
        if not path.is_relative_to(scoped_root):
            raise FormalExportArtifactError("FORMAL_EXPORT_OBJECT_SCOPE_DENIED")
        return path


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _all_clips(timeline: TimelineVersion) -> list[tuple[str, ClipReference]]:
    return [(track.kind.value, clip) for track in timeline.tracks for clip in track.clips]


def _milliseconds_timecode(value: int, *, separator: str = ":") -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"


def _frame_timecode(value: int, fps: int = 25) -> str:
    total_frames = round(value * fps / 1_000)
    hours, remainder = divmod(total_frames, fps * 3_600)
    minutes, remainder = divmod(remainder, fps * 60)
    seconds, frames = divmod(remainder, fps)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _subtitle_text(effects: object) -> str | None:
    if not isinstance(effects, dict):
        return None
    for key in ("subtitle", "text", "caption", "dialogue"):
        value = effects.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _subtitle_srt(timeline: TimelineVersion) -> str:
    cues: list[tuple[int, int, str]] = []
    for kind, clip in _all_clips(timeline):
        text = _subtitle_text(clip.effects)
        if kind == "subtitle" and text is not None:
            cues.append((clip.timeline_start_ms, clip.timeline_end_ms, text))
    if not cues:
        raise FormalExportArtifactError("FORMAL_EXPORT_SUBTITLE_TEXT_MISSING")
    cues.sort(key=lambda item: (item[0], item[1], item[2]))
    return "\n\n".join(
        f"{index}\n{_milliseconds_timecode(start, separator=',')} --> "
        f"{_milliseconds_timecode(end, separator=',')}\n{text}"
        for index, (start, end, text) in enumerate(cues, start=1)
    ) + "\n"


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace('"', '""') + '"'


def _storyboard_csv(timeline: TimelineVersion) -> str:
    rows: list[list[object]] = [[
        "track_kind", "track_id", "clip_id", "shot_id", "source_asset_id", "source_version_id",
        "timeline_start_ms", "timeline_end_ms", "source_in_ms", "source_out_ms", "source_sha256",
    ]]
    for track in timeline.tracks:
        for clip in track.clips:
            rows.append([
                track.kind.value, track.track_id, clip.clip_id, clip.shot_id, clip.source_asset_id,
                clip.source_version_id, clip.timeline_start_ms, clip.timeline_end_ms, clip.source_in_ms,
                clip.source_out_ms, clip.source_sha256,
            ])
    return "\r\n".join(",".join(_csv_cell(cell) for cell in row) for row in rows) + "\r\n"


def _davinci_edl(timeline: TimelineVersion) -> str:
    lines = [f"TITLE: XINGJING_{timeline.project_id}_{timeline.version_id}", "FCM: NON-DROP FRAME", ""]
    index = 1
    for track in timeline.tracks:
        if track.kind.value != "video":
            continue
        for clip in track.clips:
            reel = re.sub(r"[^A-Z0-9]", "", clip.source_asset_id.upper())[:8] or "XINGJING"
            lines.append(
                f"{index:03d}  {reel:<8} V     C        {_frame_timecode(clip.source_in_ms)} "
                f"{_frame_timecode(clip.source_out_ms)} {_frame_timecode(clip.timeline_start_ms)} "
                f"{_frame_timecode(clip.timeline_end_ms)}"
            )
            lines.append(f"* FROM CLIP NAME: {clip.source_version_id}")
            lines.append("")
            index += 1
    if index == 1:
        raise FormalExportArtifactError("FORMAL_EXPORT_VIDEO_TRACK_MISSING")
    return "\n".join(lines)


def _premiere_xml(timeline: TimelineVersion) -> bytes:
    root = ElementTree.Element("xmeml", version="5")
    sequence = ElementTree.SubElement(root, "sequence", id=timeline.timeline_id)
    ElementTree.SubElement(sequence, "name").text = f"Xingjing {timeline.project_id}"
    media = ElementTree.SubElement(sequence, "media")
    video = ElementTree.SubElement(media, "video")
    for track in timeline.tracks:
        if track.kind.value != "video":
            continue
        track_node = ElementTree.SubElement(video, "track")
        for clip in track.clips:
            item = ElementTree.SubElement(track_node, "clipitem", id=clip.clip_id)
            ElementTree.SubElement(item, "name").text = clip.source_version_id
            ElementTree.SubElement(item, "start").text = str(round(clip.timeline_start_ms * 25 / 1_000))
            ElementTree.SubElement(item, "end").text = str(round(clip.timeline_end_ms * 25 / 1_000))
            ElementTree.SubElement(item, "in").text = str(round(clip.source_in_ms * 25 / 1_000))
            ElementTree.SubElement(item, "out").text = str(round(clip.source_out_ms * 25 / 1_000))
            file_node = ElementTree.SubElement(item, "file", id=clip.source_asset_id)
            ElementTree.SubElement(file_node, "pathurl").text = clip.source_object_key or clip.source_version_id
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _compliance_report(
    version: FinalVideoVersion,
    timeline: TimelineVersion,
    manifest: ExportManifestContext,
    evidence: ExportAuthorityEvidence,
) -> dict[str, object]:
    return {
        "schema": "xingjing.compliance-report/v1",
        "project": {"id": manifest.project_id, "version": manifest.project_version},
        "policy": {"id": evidence.policy_id, "version": evidence.policy_version},
        "review": {"version": evidence.review_version, "reviewedAt": evidence.reviewed_at.isoformat()},
        "authorizations": list(evidence.authorization_ids),
        "aigc": {"declared": True, "label": "AI generated or assisted content"},
        "finalVideo": version.model_dump(mode="json"),
        "timeline": timeline.model_dump(mode="json"),
        "manifest": asdict(manifest),
    }


def _write_zip_package(
    *,
    destination: Path,
    package_kind: str,
    source: Path,
    version: FinalVideoVersion,
    timeline: TimelineVersion,
    compliance_report: dict[str, object],
) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(
            "package.json",
            json.dumps(
                {
                    "schema": "xingjing.delivery-package/v1",
                    "kind": package_kind,
                    "projectId": timeline.project_id,
                    "projectVersion": version.version_id,
                    "timelineVersion": timeline.version_id,
                    "aigc": True,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
        )
        archive.writestr("timeline.json", timeline.model_dump_json(indent=2))
        archive.writestr(
            "compliance-report.json",
            json.dumps(compliance_report, ensure_ascii=False, sort_keys=True, default=str, indent=2),
        )
        archive.writestr("storyboard.csv", _storyboard_csv(timeline))
        archive.writestr("timeline.edl", _davinci_edl(timeline))
        archive.writestr("premiere.xml", _premiere_xml(timeline))
        if package_kind == "jianying_draft":
            archive.writestr(
                "draft_content.json",
                json.dumps(
                    {
                        "duration": max(
                            (clip.timeline_end_ms for _, clip in _all_clips(timeline)),
                            default=0,
                        ) * 1_000,
                        "tracks": [track.model_dump(mode="json") for track in timeline.tracks],
                        "source": "xingjing",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
            )
        if package_kind in {"publish_package", "project_archive"}:
            archive.write(source, "media/final-delivery.mp4")
        try:
            archive.writestr("subtitles.srt", _subtitle_srt(timeline))
        except FormalExportArtifactError:
            pass
