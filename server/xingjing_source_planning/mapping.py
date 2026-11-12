"""Version-bound, explicitly confirmed scene and paragraph provenance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from lib.episode_editor import EpisodeEditError
from lib.episode_ledger import normalize_source_text
from lib.i18n import _ as translate
from lib.source_structure import chapters
from server.xingjing_content.models import DirectorProfile, ScriptDocument, ScriptVersion, SourceAttribution
from server.xingjing_content_persistence.repository import AuditContext, ScriptRow, SqlAlchemyContentRepository
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_projects_runtime.episode_sources import scoped
from server.xingjing_projects_runtime.episode_sources_models import EpisodeWorkingScriptRow

from .contracts import Contract
from .mapping_models import MappingReceiptRow
from .models import SourceSnapshotRow
from .service import SourcePlanningService, fingerprint, require_key, text_digest


class MappingInput(Contract):
    target_id: str = Field(alias="targetId", min_length=1, max_length=128)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    kind: Literal["exact_quote", "manual_adaptation"]
    note: str = Field(default="", max_length=1000)


class ConfirmMappings(Contract):
    revision: int = Field(ge=1)
    version: int = Field(ge=1)
    source_digest: str = Field(alias="sourceDigest", pattern=r"^[a-f0-9]{64}$")
    mappings: list[MappingInput] = Field(max_length=2000)


@dataclass(frozen=True)
class MappingSource:
    source_id: str
    digest: str
    snapshot_id: str | None
    label: str
    content: str
    start: int
    end: int
    structure: list[dict]


def targets(version: ScriptVersion) -> dict[str, dict]:
    result = {}
    for scene in version.scenes:
        result[scene.scene_id] = {"digest": fingerprint(asdict(scene)), "text": "", "kind": "scene"}
        for paragraph in scene.paragraphs:
            result[paragraph.paragraph_id] = {
                "digest": fingerprint([asdict(scene.heading), asdict(paragraph)]),
                "text": normalize_source_text(paragraph.text),
                "kind": paragraph.kind,
            }
    return result


class ScriptMappingService:
    def __init__(self, planning: SourcePlanningService):
        self.planning = planning

    async def document(
        self,
        session: AsyncSession,
        context: TrustedWorkspaceContext,
        project_id: str,
        script_id: str,
        *,
        write: bool = False,
    ) -> ScriptDocument:
        statement = select(ScriptRow).where(
            ScriptRow.workspace_id == context.workspace_id,
            ScriptRow.project_id == project_id,
            ScriptRow.script_id == script_id,
        )
        row = await session.scalar(statement.with_for_update() if write else statement)
        if row is None:
            raise EpisodeEditError("SOURCE_MAPPING_NOT_FOUND", 404)

        def read(sync: Session):
            return self.repository(sync, context).read_document(sync, row)

        return await session.run_sync(read)

    @staticmethod
    def repository(session: Session, context: TrustedWorkspaceContext):
        return SqlAlchemyContentRepository(
            lambda: session, audit_context=AuditContext(context.request_id, context.actor_id, datetime.now(UTC))
        )

    async def source(
        self, session: AsyncSession, context: TrustedWorkspaceContext, project_id: str, document: ScriptDocument
    ) -> MappingSource:
        link = await session.scalar(
            select(EpisodeWorkingScriptRow).where(
                *scoped(EpisodeWorkingScriptRow, context, project_id),
                EpisodeWorkingScriptRow.script_id == document.script_id,
            )
        )
        reference = link.source_ref if link else {}
        if reference.get("snapshotId"):
            row = await session.scalar(
                select(SourceSnapshotRow).where(
                    *scoped(SourceSnapshotRow, context, project_id),
                    SourceSnapshotRow.id == reference["snapshotId"],
                )
            )
            if row is None or row.source_digest != reference["digest"] or text_digest(row.content) != row.source_digest:
                raise EpisodeEditError("SOURCE_MAPPING_SOURCE_UNAVAILABLE", 409)
            start, end = reference["start"], reference["end"]
            if not 0 <= start < end <= len(row.content):
                raise EpisodeEditError("SOURCE_MAPPING_SOURCE_UNAVAILABLE", 409)
            return MappingSource(
                row.source_id,
                row.source_digest,
                row.id,
                row.option.get("label", row.source_id),
                row.content,
                start,
                end,
                row.chapters,
            )
        content = normalize_source_text(document.source_document.content)
        digest = text_digest(content)
        source_id = f"script:{document.script_id}:original"
        return MappingSource(
            source_id,
            digest,
            None,
            document.source_document.filename,
            content,
            0,
            len(content),
            [asdict(item) for item in chapters(content, fingerprint([source_id, digest]))],
        )

    async def read(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        script_id: str,
        *,
        version_number: int,
        offset: int | None = None,
        find: str = "",
    ) -> dict:
        if len(find) > 500 or (offset is not None and offset < 0):
            raise EpisodeEditError("SOURCE_MAPPING_INVALID", 422)
        async with self.planning.sessions() as session:
            context = await self.planning.project(session, context, project_id)
            document = await self.document(session, context, project_id, script_id)
            version = next((item for item in document.versions if item.number == version_number), None)
            if version is None:
                raise EpisodeEditError("SOURCE_MAPPING_NOT_FOUND", 404)
            source = await self.source(session, context, project_id, document)
            start = max(
                source.start, min(offset if offset is not None else source.start, max(source.start, source.end - 1))
            )
            end = min(source.end, start + 4000)
            matches, cursor = [], source.start
            query = normalize_source_text(find)
            while query and len(matches) < 51:
                found = source.content.find(query, cursor, source.end)
                if found < 0:
                    break
                matches.append({"start": found, "end": found + len(query)})
                cursor = found + 1
            return {
                "scriptId": script_id,
                "revision": document.revision,
                "version": version.number,
                "canManage": "script.manage" in context.permissions
                and document.locked_version_number is None
                and version.number == document.current_version.number,
                "source": {
                    "id": source.source_id,
                    "digest": source.digest,
                    "snapshotId": source.snapshot_id,
                    "label": source.label,
                    "start": source.start,
                    "end": source.end,
                },
                "chapters": [
                    item for item in source.structure if item["start"] < source.end and item["end"] > source.start
                ],
                "excerpt": {"start": start, "end": end, "text": source.content[start:end]},
                "matches": matches[:50],
                "moreMatches": len(matches) > 50,
                "coordinateSystem": "nfc_lf_codepoints",
                "mappings": [asdict(item) for item in version.source_attributions],
            }

    async def confirm(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        script_id: str,
        command: ConfirmMappings,
        *,
        key: str,
        locale: str,
    ) -> dict:
        require_key(key)
        request_digest = fingerprint([script_id, command.model_dump(mode="json", by_alias=True)])
        async with self.planning.sessions() as session, session.begin():
            context = await self.planning.project(session, context, project_id)
            if "script.manage" not in context.permissions:
                raise EpisodeEditError("SOURCE_MAPPING_PERMISSION_DENIED", 403)
            document = await self.document(session, context, project_id, script_id, write=True)
            previous = await session.get(
                MappingReceiptRow, (context.tenant_id, context.workspace_id, project_id, context.actor_id, key)
            )
            if previous:
                if previous.fingerprint != request_digest:
                    raise EpisodeEditError("SOURCE_MAPPING_KEY_REUSED", 409)
                return previous.result
            if document.revision != command.revision or document.current_version.number != command.version:
                raise EpisodeEditError("SOURCE_MAPPING_VERSION_CHANGED", 409)
            if document.locked_version_number is not None:
                raise EpisodeEditError("SOURCE_MAPPING_FROZEN", 409)
            source = await self.source(session, context, project_id, document)
            if source.digest != command.source_digest:
                raise EpisodeEditError("SOURCE_MAPPING_SOURCE_UNAVAILABLE", 409)
            known = targets(document.current_version)
            chosen = {}
            at = datetime.now(UTC).isoformat()
            for item in command.mappings:
                target = known.get(item.target_id)
                if (
                    target is None
                    or item.target_id in chosen
                    or not source.start <= item.start < item.end <= source.end
                ):
                    raise EpisodeEditError("SOURCE_MAPPING_INVALID", 422)
                if item.kind == "exact_quote" and (
                    target["kind"] == "scene" or target["text"] != source.content[item.start : item.end]
                ):
                    raise EpisodeEditError("SOURCE_MAPPING_QUOTE_MISMATCH", 422)
                chosen[item.target_id] = SourceAttribution(
                    item.target_id,
                    target["digest"],
                    source.source_id,
                    source.digest,
                    source.snapshot_id,
                    item.start,
                    item.end,
                    tuple(
                        chapter["id"]
                        for chapter in source.structure
                        if chapter["start"] < item.end and chapter["end"] > item.start
                    ),
                    item.kind,
                    item.note.strip(),
                    context.actor_id,
                    at,
                )
            mappings = tuple(chosen[identity] for identity in known if identity in chosen)
            version = replace(
                document.current_version,
                number=document.current_version.number + 1,
                source_attributions=mappings,
                change_summary=translate("source_mapping_version_summary", locale=locale),
            )
            updated = replace(
                document,
                revision=document.revision + 1,
                versions=(*document.versions, version),
                director_profile=DirectorProfile(),
            )

            def persist(sync: Session):
                self.repository(sync, context).append_document_version(
                    sync, updated, document, idempotency_key=f"mapping:{fingerprint([context.actor_id, key])}"
                )

            await session.run_sync(persist)
            result = {
                "scriptId": script_id,
                "revision": updated.revision,
                "version": version.number,
                "confirmedAt": at,
                "count": len(mappings),
            }
            session.add(
                MappingReceiptRow(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    actor_id=context.actor_id,
                    request_key=key,
                    script_id=script_id,
                    fingerprint=request_digest,
                    result=result,
                )
            )
            return result
