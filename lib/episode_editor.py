"""Episode planning edits over the production ledger, with stable file identities."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lib.episode_ledger import (
    SourceRange,
    discover_product_episode_nums,
    discover_sources,
    has_downstream_products,
    ordered_episode_entries,
)
from lib.episode_paths import REFERENCE_VIDEO_STEP1_FILENAME, STEP1_FILENAMES, episode_script_relpath
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.json_io import atomic_write_json
from lib.path_safety import safe_join
from lib.project_change_hints import emit_project_change_hint
from lib.project_manager import ProjectManager
from lib.status_calculator import StatusCalculator


class EpisodeEditError(ValueError):
    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.status = status


class EpisodeProduction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scriptVersionId: str = Field(default="", max_length=160)
    assetSnapshotId: str = Field(default="", max_length=160)
    finalVideoVersionId: str = Field(default="", max_length=128)


class EpisodeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    episode: int = Field(ge=1, le=1_000_000)
    title: str = Field(min_length=1, max_length=200)
    hook: str = Field(default="", max_length=4000)
    storyBeats: list[str] = Field(default_factory=list, max_length=100)
    nextEpisodeTeaser: str = Field(default="", max_length=4000)
    targetDuration: int = Field(default=60, ge=1, le=3600)
    budgetMinor: int | None = Field(default=None, ge=0, le=1_000_000_000)
    sourceRange: SourceRange | None = None
    production: EpisodeProduction | None = None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _sources(root: Path, source_texts: dict[str, str] | None = None) -> dict[str, str]:
    source_dir = root / "source"
    if source_dir.is_symlink() or (source_dir.exists() and not source_dir.resolve().is_relative_to(root.resolve())):
        raise EpisodeEditError("EPISODE_SOURCE_UNSAFE")
    if source_dir.exists() and any(path.is_symlink() for path in source_dir.iterdir()):
        raise EpisodeEditError("EPISODE_SOURCE_UNSAFE")
    sources = discover_sources(root)
    if sum(len(doc.text) for doc in sources) > 2_000_000:
        raise EpisodeEditError("EPISODE_SOURCE_TOO_LARGE")
    return {**{doc.rel_path: doc.text for doc in sources}, **(source_texts or {})}


def _editable(entry: dict, default_duration: int = 60) -> dict[str, Any]:
    outline = entry.get("outline") or {}
    return {
        "episode": entry["episode"],
        "title": entry.get("title", ""),
        "hook": entry.get("hook", ""),
        "storyBeats": outline.get("story_beats", []),
        "nextEpisodeTeaser": outline.get("next_episode_teaser", ""),
        "targetDuration": entry.get("target_duration_seconds", default_duration),
        "budgetMinor": entry.get("budget_minor"),
        "sourceRange": entry.get("source_range"),
        "production": EpisodeProduction.model_validate(entry.get("production", {})).model_dump(),
    }


def _revision(project: dict, sources: dict[str, str], locked: list[int]) -> str:
    return _digest(
        {
            "episodes": ordered_episode_entries(project),
            "sources": sources,
            "locked": locked,
            "revision": project.get("episode_edit_revision", 0),
        }
    )


def _locked(root: Path, entries: list[dict]) -> list[int]:
    return [
        entry["episode"]
        for entry in entries
        if entry.get("production", {}).get("scriptVersionId") or has_downstream_products(root, entry["episode"], entry)
    ]


def read_episodes(
    manager: ProjectManager, project_id: str, *, source_texts: dict[str, str] | None = None
) -> dict[str, Any]:
    project = manager.load_project(project_id)
    root = manager.get_project_path(project_id)
    sources = _sources(root, source_texts)
    entries = ordered_episode_entries(project)
    locked = _locked(root, entries)
    revision = _revision(project, sources, locked)
    enriched = StatusCalculator(manager).enrich_project(project_id, copy.deepcopy(project))
    statistics = {entry["episode"]: entry for entry in enriched.get("episodes", [])}
    result = []
    for entry in entries:
        number = entry["episode"]
        stats = statistics.get(number, {})
        editable = _editable(entry, project.get("planned_episode_duration_seconds", 60))
        issues = []
        if not editable["hook"].strip():
            issues.append("hook_missing")
        source_range = editable["sourceRange"]
        if not source_range:
            issues.append("source_missing")
        elif source_range.get("source_file") not in sources:
            issues.append("source_changed")
        else:
            rel = source_range["source_file"]
            recorded = project.get("source_fingerprints", {}).get(rel)
            current = hashlib.sha256(sources[rel].encode("utf-8")).hexdigest()
            if (recorded and recorded != current) or source_range.get("end", 0) > len(sources[rel]):
                issues.append("source_changed")
        if stats.get("script_status") != "generated":
            issues.append("script_missing")
        result.append(
            {
                **editable,
                "locked": number in locked,
                "status": stats.get("status", "draft"),
                "scriptStatus": stats.get("script_status", "none"),
                "shots": stats.get("scenes_count", 0),
                "storyboards": stats.get("storyboards", {"completed": 0, "total": 0}),
                "videos": stats.get("videos", {"completed": 0, "total": 0}),
                "issues": issues,
            }
        )
    return {
        "revision": revision,
        "items": result,
        "sources": [{"path": path, "text": text} for path, text in sources.items()],
        "contentMode": project.get("content_mode"),
        "generationMode": project.get("generation_mode"),
        "plannedCount": project.get("planned_episode_count", 1),
        "defaultDuration": project.get("planned_episode_duration_seconds", 60),
        "nextEpisode": max([*discover_product_episode_nums(root), *(e["episode"] for e in entries)], default=0) + 1,
    }


def _preview_episode_title(fragment: str, number: int, locale: str) -> str:
    meaningful_lines = [
        " ".join(line.split()) for line in fragment.splitlines() if any(character.isalnum() for character in line)
    ]
    if not meaningful_lines:
        return translate("draft_event_label", locale=locale, episode=number, label_prefix="")
    first_line = meaningful_lines[0]
    heading = re.sub(r"^#{1,6}\s+", "", first_line)
    short_heading = len(heading) <= 48 and not re.search(r"[。！？!?]", heading)
    explicit_chapter = re.fullmatch(
        r"(?:第[零〇一二三四五六七八九十百千万两\d]{1,16}[章节回集话幕卷](?:\s+.{1,30}|[：:、.．\-—].{1,30})?"
        r"|(?:chapter|episode|part|act|scene|book)\s+(?:\d+|[ivxlcdm]+)(?:[\s:：.\-—].{0,30})?"
        r"|(?:chương|tập|phần|hồi|cảnh)\s+\d+(?:[\s:：.\-—].{0,30})?)",
        heading,
        re.IGNORECASE,
    )
    if short_heading and (heading != first_line or explicit_chapter is not None):
        return heading
    return first_line if len(first_line) <= 36 else first_line[:35].rstrip() + "…"


def split_remaining(
    manager: ProjectManager,
    project_id: str,
    source: str,
    count: int,
    revision: str,
    *,
    source_texts: dict[str, str] | None = None,
    locale: str = DEFAULT_LOCALE,
) -> list[dict]:
    """Preview a deterministic paragraph split; never call a paid model or write files."""
    snapshot = read_episodes(manager, project_id, source_texts=source_texts)
    if snapshot["revision"] != revision:
        raise EpisodeEditError("VERSION_CONFLICT", 409)
    if snapshot["contentMode"] == "ad":
        raise EpisodeEditError("EPISODE_SINGLE_CONTENT")
    text = next((doc["text"] for doc in snapshot["sources"] if doc["path"] == source), None)
    if text is None or type(count) is not int or not 1 <= count <= 200:
        raise EpisodeEditError("EPISODE_SPLIT_INVALID")
    start = max(
        (
            e["sourceRange"]["end"]
            for e in snapshot["items"]
            if e["sourceRange"] and e["sourceRange"]["source_file"] == source
        ),
        default=0,
    )
    tail = text[start:]
    paragraphs = [
        (start + m.start(), start + m.end()) for m in re.finditer(r"[^\n]+(?:\n+|$)", tail) if m.group().strip()
    ]
    if count > len(paragraphs):
        raise EpisodeEditError("EPISODE_NOT_ENOUGH_PARAGRAPHS")
    boundaries = [start]
    for i in range(1, count):
        boundaries.append(paragraphs[len(paragraphs) * i // count][0])
    boundaries.append(len(text))
    result = []
    for i in range(count):
        begin, end = boundaries[i : i + 2]
        number = snapshot["nextEpisode"] + i
        title = _preview_episode_title(text[begin:end], number, locale)
        result.append(
            EpisodeEdit(
                episode=number,
                title=title,
                targetDuration=snapshot["defaultDuration"],
                sourceRange=SourceRange(source_file=source, start=begin, end=end),
            ).model_dump()
        )
    return result


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".episode-edit-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_episodes(
    manager: ProjectManager,
    project_id: str,
    edits: list[EpisodeEdit],
    *,
    revision: str,
    key: str,
    actor: str,
    replay_only: bool = False,
    source_texts: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = manager.get_project_path(project_id)
    snapshot = manager.load_project(project_id)
    paths = {safe_join(root, episode_script_relpath(edit.episode)) for edit in edits}
    for entry in ordered_episode_entries(snapshot):
        if entry.get("script_file"):
            filename = manager.normalize_script_filename(entry["script_file"])
            paths.add(safe_join(root / "scripts", filename))
    for edit in edits:
        for filename in {*STEP1_FILENAMES.values(), REFERENCE_VIDEO_STEP1_FILENAME}:
            paths.add(safe_join(root, f"drafts/episode_{edit.episode}/{filename}"))
    # Production writers acquire script/draft locks before the project lock.
    with ExitStack() as locks:
        for path in sorted(paths):
            locks.enter_context(manager.file_lock(path))
        return _save_episodes_locked(
            manager,
            project_id,
            edits,
            revision=revision,
            key=key,
            actor=actor,
            replay_only=replay_only,
            source_texts=source_texts,
        )


def _save_episodes_locked(
    manager: ProjectManager,
    project_id: str,
    edits: list[EpisodeEdit],
    *,
    revision: str,
    key: str,
    actor: str,
    replay_only: bool,
    source_texts: dict[str, str] | None,
) -> dict[str, Any]:
    """Commit the ledger and derived text under its existing project lock.

    A durable receipt allows the scoped HTTP adapter to retry its SQL audit/outbox
    transaction after a transport or database failure without applying the edit twice.
    """
    root = manager.get_project_path(project_id)
    project_file = root / manager.PROJECT_FILE
    digest = _digest({"revision": revision, "items": [e.model_dump() for e in edits], "actor": actor})
    with manager.file_lock(project_file):
        project = json.loads(project_file.read_text(encoding="utf-8"))
        receipts = project.get("episode_edit_receipts", [])
        previous = next((r for r in receipts if r["key"] == key), None)
        if previous:
            if previous["digest"] != digest:
                raise EpisodeEditError("IDEMPOTENCY_CONFLICT", 409)
            return previous
        if replay_only:
            raise EpisodeEditError("IDEMPOTENCY_RECEIPT_EXPIRED", 409)
        sources = _sources(root, source_texts)
        entries = ordered_episode_entries(project)
        locked = _locked(root, entries)
        before = _revision(project, sources, locked)
        if before != revision:
            raise EpisodeEditError("VERSION_CONFLICT", 409)
        existing = {entry["episode"]: entry for entry in entries}
        numbers = [edit.episode for edit in edits]
        if len(numbers) != len(set(numbers)) or not set(existing).issubset(numbers) or len(numbers) > 500:
            raise EpisodeEditError("EPISODE_SET_INVALID")
        if project.get("content_mode") == "ad" and numbers != [1]:
            raise EpisodeEditError("EPISODE_SINGLE_CONTENT")
        reserved = discover_product_episode_nums(root)
        updated = []
        ranges: dict[str, list[tuple[int, int]]] = {}
        writes: dict[Path, str] = {}
        for edit in edits:
            old = existing.get(edit.episode, {})
            if not edit.title.strip() or any(not beat.strip() or len(beat) > 4000 for beat in edit.storyBeats):
                raise EpisodeEditError("EPISODE_FIELDS_INVALID")
            source_range = edit.sourceRange.model_dump() if edit.sourceRange else None
            if edit.episode in locked and (edit.title != old.get("title") or source_range != old.get("source_range")):
                raise EpisodeEditError("EPISODE_PRODUCTION_LOCKED", 409)
            if not old and (edit.episode in reserved or edit.episode <= max(existing, default=0)):
                raise EpisodeEditError("EPISODE_ID_RESERVED", 409)
            entry = {
                **old,
                "episode": edit.episode,
                "title": edit.title.strip(),
                "hook": edit.hook,
                "target_duration_seconds": edit.targetDuration,
                "budget_minor": edit.budgetMinor,
                "outline": {
                    **(old.get("outline") or {}),
                    "story_beats": edit.storyBeats,
                    "next_episode_teaser": edit.nextEpisodeTeaser,
                },
            }
            entry.setdefault("script_file", episode_script_relpath(edit.episode))
            entry.setdefault("ledger_status", "planned")
            if edit.production is not None:
                entry["production"] = edit.production.model_dump()
            if edit.sourceRange:
                rel, start, end = edit.sourceRange.source_file, edit.sourceRange.start, edit.sourceRange.end
                text = sources.get(rel)
                if text is None or not 0 <= start < end <= len(text) or not text[start:end].strip():
                    raise EpisodeEditError("EPISODE_SOURCE_RANGE_INVALID")
                recorded = project.get("source_fingerprints", {}).get(rel)
                if recorded and recorded != hashlib.sha256(text.encode("utf-8")).hexdigest():
                    raise EpisodeEditError("EPISODE_SOURCE_CHANGED", 409)
                ranges.setdefault(rel, []).append((start, end))
                entry["source_range"] = source_range
                if edit.episode not in locked:
                    writes[root / "source" / f"episode_{edit.episode}.txt"] = text[start:end]
            else:
                entry.pop("source_range", None)
                if old.get("source_range"):
                    raise EpisodeEditError("EPISODE_SOURCE_RANGE_REQUIRED")
            updated.append(entry)
        for segments in ranges.values():
            segments.sort()
            if any(right[0] < left[1] for left, right in zip(segments, segments[1:])):
                raise EpisodeEditError("EPISODE_SOURCE_OVERLAP")
        project["episodes"] = updated
        project["episode_order"] = numbers
        project["episode_edit_revision"] = project.get("episode_edit_revision", 0) + 1
        project["source_fingerprints"] = {
            rel: hashlib.sha256(text.encode("utf-8")).hexdigest() for rel, text in sources.items()
        }
        # Cursor follows source coordinates, independently of presentation order.
        covered = [(rel, max(end for _, end in segments)) for rel, segments in ranges.items()]
        if covered:
            rel, end = max(covered)
            project["planning_cursor"] = {"source_file": rel, "offset": end}
        receipt = {
            "key": key,
            "digest": digest,
            "actor": actor,
            "before": before,
            "after": _revision(project, sources, _locked(root, updated)),
            "episodes": numbers,
            "at": datetime.now(UTC).isoformat(),
        }
        project["episode_edit_receipts"] = [*receipts[-49:], receipt]
        project.setdefault("metadata", {})["updated_at"] = receipt["at"]
        backups: dict[Path, str | None] = {}
        try:
            for path, text in writes.items():
                backups[path] = path.read_text(encoding="utf-8") if path.exists() else None
                _write_text(path, text)
            atomic_write_json(project_file, project)
        except Exception:
            for path, old_text in backups.items():
                if old_text is None:
                    path.unlink(missing_ok=True)
                else:
                    _write_text(path, old_text)
            raise
    emit_project_change_hint(project_id, changed_paths=[manager.PROJECT_FILE, "source"])
    return receipt
