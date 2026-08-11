from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass, replace

from .models import ContentAnalysis, DirectorProfile, ScriptDocument, SourceDocument
from .parser import parse_script
from .repository import FileContentRepository


class ContentConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ImportScriptCommand:
    workspace_id: str
    project_id: str
    title: str
    filename: str
    media_type: str
    content: str
    idempotency_key: str

    def with_content(self, content: str) -> ImportScriptCommand:
        return replace(self, content=content)


@dataclass(frozen=True, slots=True)
class ReviseScriptCommand:
    workspace_id: str
    script_id: str
    expected_revision: int
    content: str
    idempotency_key: str
    change_summary: str


@dataclass(frozen=True, slots=True)
class SetDirectorProfileCommand:
    workspace_id: str
    script_id: str
    expected_revision: int
    idempotency_key: str
    profile: DirectorProfile


@dataclass(frozen=True, slots=True)
class ApplyAnalysisCommand:
    workspace_id: str
    script_id: str
    expected_revision: int
    idempotency_key: str
    analysis: ContentAnalysis


@dataclass(frozen=True, slots=True)
class ScriptPage:
    items: tuple[ScriptDocument, ...]
    total: int
    offset: int
    limit: int


class ContentService:
    def __init__(self, repository: FileContentRepository) -> None:
        self._repository = repository

    def import_script(self, command: ImportScriptCommand) -> ScriptDocument:
        fingerprint = _fingerprint(command)

        def create(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script_id = str(uuid.uuid4())
            version = parse_script(command.content, version_number=1, change_summary="导入原文")
            script = ScriptDocument(
                script_id=script_id,
                workspace_id=command.workspace_id,
                project_id=command.project_id,
                title=command.title,
                revision=1,
                source_document=SourceDocument(command.filename, command.media_type, command.content),
                versions=(version,),
            )
            scripts[script_id] = script
            return scripts, script

        return self._transact(command.workspace_id, command.idempotency_key, fingerprint, create)

    def revise_script(self, command: ReviseScriptCommand) -> ScriptDocument:
        fingerprint = _fingerprint(command)

        def revise(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script = _owned_script(scripts, command.script_id, command.workspace_id)
            if script.revision != command.expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            if script.locked_version_number is not None:
                raise ValueError("SCRIPT_FROZEN")
            version = parse_script(
                command.content,
                version_number=script.current_version.number + 1,
                change_summary=command.change_summary,
            )
            revised = replace(script, revision=script.revision + 1, versions=(*script.versions, version))
            scripts[script.script_id] = revised
            return scripts, revised

        return self._transact(command.workspace_id, command.idempotency_key, fingerprint, revise)

    def set_director_profile(self, command: SetDirectorProfileCommand) -> ScriptDocument:
        fingerprint = _fingerprint(command)

        def update(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script = _owned_script(scripts, command.script_id, command.workspace_id)
            if script.revision != command.expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            directed = replace(script, revision=script.revision + 1, director_profile=command.profile)
            scripts[script.script_id] = directed
            return scripts, directed

        return self._transact(command.workspace_id, command.idempotency_key, fingerprint, update)

    def apply_analysis(self, command: ApplyAnalysisCommand) -> ScriptDocument:
        fingerprint = _fingerprint(command)

        def update(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script = _owned_script(scripts, command.script_id, command.workspace_id)
            if script.revision != command.expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            if script.locked_version_number is not None:
                raise ValueError("SCRIPT_FROZEN")
            parsed = parse_script(
                script.current_version.source_content,
                version_number=script.current_version.number + 1,
                change_summary="AI 结构化解析",
            )
            analyzed = replace(parsed, analysis=command.analysis)
            revised = replace(
                script,
                revision=script.revision + 1,
                versions=(*script.versions, analyzed),
            )
            scripts[script.script_id] = revised
            return scripts, revised

        return self._transact(command.workspace_id, command.idempotency_key, fingerprint, update)

    def get_script(self, *, workspace_id: str, script_id: str) -> ScriptDocument:
        return _owned_script(self._repository.read_all(workspace_id), script_id, workspace_id)

    def list_scripts(
        self,
        *,
        workspace_id: str,
        project_id: str | None = None,
        query: str | None = None,
        locked: bool | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> ScriptPage:
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("INVALID_PAGINATION")
        scripts = self._repository.read_all(workspace_id).values()
        normalized_query = (query or "").strip().casefold()
        filtered = [
            item
            for item in scripts
            if (project_id is None or item.project_id == project_id)
            and (not normalized_query or normalized_query in item.title.casefold() or normalized_query in item.script_id.casefold())
            and (locked is None or (item.locked_version_number is not None) == locked)
        ]
        filtered.sort(key=lambda item: (item.title, item.script_id))
        return ScriptPage(
            items=tuple(filtered[offset : offset + limit]), total=len(filtered), offset=offset, limit=limit
        )

    def list_audit(
        self,
        *,
        workspace_id: str,
        project_id: str,
        script_id: str,
        request_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        self.get_script(workspace_id=workspace_id, script_id=script_id)
        return self._repository.list_audit(
            workspace_id,
            project_id=project_id,
            script_id=script_id,
            request_id=request_id,
            actor_id=actor_id,
            limit=limit,
        )

    def freeze_script(
        self,
        *,
        workspace_id: str,
        script_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ScriptDocument:
        fingerprint = _fingerprint((workspace_id, script_id, expected_revision, "freeze"))

        def freeze(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script = _owned_script(scripts, script_id, workspace_id)
            if script.revision != expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            if script.current_version.validation_errors:
                raise ValueError("SCRIPT_VALIDATION_FAILED")
            frozen = replace(
                script,
                revision=script.revision + 1,
                locked_version_number=script.current_version.number,
            )
            scripts[script.script_id] = frozen
            return scripts, frozen

        return self._transact(workspace_id, idempotency_key, fingerprint, freeze)

    def _transact(self, workspace_id: str, key: str, fingerprint: str, operation):
        if not key.strip():
            raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
        try:
            return self._repository.transact(workspace_id, key, fingerprint, operation)
        except ValueError as exc:
            if str(exc) == "IDEMPOTENCY_KEY_REUSED":
                raise ContentConflict("IDEMPOTENCY_KEY_REUSED") from exc
            raise


def _owned_script(scripts: dict[str, ScriptDocument], script_id: str, workspace_id: str) -> ScriptDocument:
    script = scripts.get(script_id)
    if script is None or script.workspace_id != workspace_id:
        raise KeyError("SCRIPT_NOT_FOUND")
    return script


def _fingerprint(value: object) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
