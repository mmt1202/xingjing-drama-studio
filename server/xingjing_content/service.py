from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from .models import (
    ContentAnalysis,
    DirectorConfirmation,
    DirectorProfile,
    DirectorVersion,
    ScriptDocument,
    SourceDocument,
    director_profile_digest,
)
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
    origin: Literal["manual", "ai"] = "manual"
    actor_id: str = "system"


@dataclass(frozen=True, slots=True)
class ConfirmDirectorProfileCommand:
    workspace_id: str
    script_id: str
    expected_revision: int
    idempotency_key: str
    candidate_id: str
    actor_id: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class ApplyAnalysisCommand:
    workspace_id: str
    script_id: str
    expected_revision: int
    idempotency_key: str
    analysis: ContentAnalysis
    content: str | None = None


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
            revised = replace(
                script,
                revision=script.revision + 1,
                versions=(*script.versions, version),
                director_profile=DirectorProfile(),
            )
            scripts[script.script_id] = revised
            return scripts, revised

        return self._transact(command.workspace_id, command.idempotency_key, fingerprint, revise)

    def set_director_profile(self, command: SetDirectorProfileCommand) -> ScriptDocument:
        # Actor/origin are trusted evidence; retain existing command fingerprints.
        request = asdict(command)
        request.pop("origin")
        request.pop("actor_id")
        fingerprint = _fingerprint(request)

        def update(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script = _owned_script(scripts, command.script_id, command.workspace_id)
            if script.revision != command.expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            _validate_director_profile(command.profile)
            if command.origin not in {"manual", "ai"} or not command.actor_id.strip():
                raise ValueError("INVALID_DIRECTOR_PROFILE")
            candidate = DirectorVersion(
                str(uuid.uuid4()),
                len(script.director_versions) + 1,
                f"{script.script_id}@{script.current_version.number}",
                hashlib.sha256(script.current_version.source_content.encode()).hexdigest(),
                command.profile,
                command.origin,
                command.actor_id,
                datetime.now(UTC).isoformat(),
            )
            directed = replace(
                script,
                revision=script.revision + 1,
                director_profile=command.profile,
                director_versions=(*script.director_versions, candidate),
            )
            scripts[script.script_id] = directed
            return scripts, directed

        return self._transact(command.workspace_id, command.idempotency_key, fingerprint, update)

    def confirm_director_profile(self, command: ConfirmDirectorProfileCommand) -> ScriptDocument:
        fingerprint = _fingerprint(command)

        def confirm(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script = _owned_script(scripts, command.script_id, command.workspace_id)
            if script.revision != command.expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            if script.locked_version_number is None:
                raise ValueError("SCRIPT_NOT_FROZEN")
            candidate = script.director_candidate
            if not any(item.id == command.candidate_id for item in script.director_versions):
                raise KeyError("DIRECTOR_CANDIDATE_NOT_FOUND")
            if (
                candidate is None
                or candidate.id != command.candidate_id
                or candidate.script_version_id != f"{script.script_id}@{script.locked_version_number}"
                or candidate.script_content_digest
                != hashlib.sha256(script.current_version.source_content.encode()).hexdigest()
            ):
                raise ContentConflict("DIRECTOR_CANDIDATE_STALE")
            _validate_director_profile(candidate.profile, complete=True)
            if len(command.note) > 1000 or not command.actor_id.strip():
                raise ValueError("INVALID_DIRECTOR_PROFILE")
            if script.confirmed_director is not None:
                return scripts, script
            confirmation = DirectorConfirmation(
                str(uuid.uuid4()),
                candidate.id,
                candidate.number,
                candidate.script_version_id,
                candidate.script_content_digest,
                director_profile_digest(candidate.profile),
                command.actor_id,
                datetime.now(UTC).isoformat(),
                command.note.strip(),
            )
            directed = replace(
                script,
                revision=script.revision + 1,
                director_confirmations=(*script.director_confirmations, confirmation),
            )
            scripts[script.script_id] = directed
            return scripts, directed

        return self._transact(command.workspace_id, command.idempotency_key, fingerprint, confirm)

    def restore_script_version(
        self,
        *,
        workspace_id: str,
        script_id: str,
        source_version_number: int,
        expected_revision: int,
        idempotency_key: str,
        reason: str,
    ) -> ScriptDocument:
        if type(source_version_number) is not int or source_version_number < 1:
            raise ValueError("INVALID_SOURCE_VERSION")
        if not reason.strip() or len(reason) > 1000:
            raise ValueError("RESTORE_REASON_REQUIRED")
        fingerprint = _fingerprint(
            (
                workspace_id,
                script_id,
                source_version_number,
                expected_revision,
                reason,
                "restore",
            )
        )

        def restore(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            current = _owned_script(scripts, script_id, workspace_id)
            if current.revision != expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            if current.locked_version_number is not None:
                raise ValueError("SCRIPT_FROZEN")
            source = next((item for item in current.versions if item.number == source_version_number), None)
            if source is None:
                raise KeyError("SCRIPT_VERSION_NOT_FOUND")
            restored_version = replace(
                source,
                number=current.current_version.number + 1,
                change_summary=f"restore:{source_version_number}; {reason.strip()}",
            )
            restored = replace(
                current,
                revision=current.revision + 1,
                versions=(*current.versions, restored_version),
                director_profile=DirectorProfile(),
            )
            scripts[script_id] = restored
            return scripts, restored

        return self._transact(workspace_id, idempotency_key, fingerprint, restore)

    def apply_analysis(self, command: ApplyAnalysisCommand) -> ScriptDocument:
        values = asdict(command)
        if command.content is None:
            values.pop("content")
        fingerprint = _fingerprint(values)

        def update(scripts: dict[str, ScriptDocument]) -> tuple[dict[str, ScriptDocument], ScriptDocument]:
            script = _owned_script(scripts, command.script_id, command.workspace_id)
            if script.revision != command.expected_revision:
                raise ContentConflict("VERSION_CONFLICT")
            if script.locked_version_number is not None:
                raise ValueError("SCRIPT_FROZEN")
            parsed = parse_script(
                command.content if command.content is not None else script.current_version.source_content,
                version_number=script.current_version.number + 1,
                change_summary="AI 结构化解析",
            )
            if parsed.validation_errors:
                raise ValueError("SCRIPT_AI_STRUCTURE_INVALID")
            analyzed = replace(parsed, analysis=command.analysis)
            revised = replace(
                script,
                revision=script.revision + 1,
                versions=(*script.versions, analyzed),
                director_profile=DirectorProfile(),
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
            and (
                not normalized_query
                or normalized_query in item.title.casefold()
                or normalized_query in item.script_id.casefold()
            )
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
            if script.locked_version_number is not None:
                return scripts, script
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


def _validate_director_profile(profile: DirectorProfile, *, complete: bool = False) -> None:
    values = (profile.audience, profile.pacing, profile.visual_style, profile.camera_language)
    if any(value is not None and not _bounded_text(value, 1000) for value in values):
        raise ValueError("INVALID_DIRECTOR_PROFILE")
    if len(profile.production_constraints) > 100 or any(
        not _bounded_text(value, 2000) or not value.strip() for value in profile.production_constraints
    ):
        raise ValueError("INVALID_DIRECTOR_PROFILE")
    if complete and any(value is None or not value.strip() for value in values):
        raise ValueError("DIRECTOR_PROFILE_INCOMPLETE")


def _bounded_text(value: object, maximum: int) -> bool:
    return isinstance(value, str) and len(value) <= maximum


def _fingerprint(value: object) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
