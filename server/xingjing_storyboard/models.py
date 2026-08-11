from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self, cast

from .errors import ContractViolation


class AssetKind(StrEnum):
    CHARACTER = "character"
    SCENE = "scene"
    PROP = "prop"
    COSTUME = "costume"
    VOICE = "voice"


class GenerationStatus(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class QualitySeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class StoryboardStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"


@dataclass(frozen=True, slots=True)
class AssetReference:
    asset_id: str
    asset_version_id: str
    kind: AssetKind

    def __post_init__(self) -> None:
        _require_text(self.asset_id, "ASSET_ID_REQUIRED")
        _require_text(self.asset_version_id, "ASSET_VERSION_ID_REQUIRED")

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "asset_version_id": self.asset_version_id,
            "kind": self.kind.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        return cls(
            asset_id=_string(value, "asset_id"),
            asset_version_id=_string(value, "asset_version_id"),
            kind=_enum(AssetKind, value, "kind"),
        )


@dataclass(frozen=True, slots=True)
class QualityIssue:
    issue_id: str
    shot_id: str
    code: str
    severity: QualitySeverity
    field: str | None
    details: dict[str, object]

    def __post_init__(self) -> None:
        _require_text(self.issue_id, "QUALITY_ISSUE_ID_REQUIRED")
        _require_text(self.shot_id, "SHOT_ID_REQUIRED")
        _require_text(self.code, "QUALITY_ISSUE_CODE_REQUIRED")
        _canonical_json(self.details)

    def to_dict(self) -> dict[str, object]:
        return {
            "issue_id": self.issue_id,
            "shot_id": self.shot_id,
            "code": self.code,
            "severity": self.severity.value,
            "field": self.field,
            "details": json.loads(_canonical_json(self.details)),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        return cls(
            issue_id=_string(value, "issue_id"),
            shot_id=_string(value, "shot_id"),
            code=_string(value, "code"),
            severity=_enum(QualitySeverity, value, "severity"),
            field=_optional_string(value, "field"),
            details=_object_dict(value, "details"),
        )


@dataclass(frozen=True, slots=True)
class ShotReplacement:
    replacement_id: str
    previous_media_id: str | None
    new_media_id: str
    reason: str
    actor_id: str
    replaced_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.replacement_id, "REPLACEMENT_ID_REQUIRED")
        _require_text(self.new_media_id, "MEDIA_ID_REQUIRED")
        _require_text(self.reason, "REPLACEMENT_REASON_REQUIRED")
        _require_text(self.actor_id, "ACTOR_ID_REQUIRED")
        _require_aware(self.replaced_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "replacement_id": self.replacement_id,
            "previous_media_id": self.previous_media_id,
            "new_media_id": self.new_media_id,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "replaced_at": _datetime_text(self.replaced_at),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        return cls(
            replacement_id=_string(value, "replacement_id"),
            previous_media_id=_optional_string(value, "previous_media_id"),
            new_media_id=_string(value, "new_media_id"),
            reason=_string(value, "reason"),
            actor_id=_string(value, "actor_id"),
            replaced_at=_datetime(value, "replaced_at"),
        )


@dataclass(frozen=True, slots=True)
class Shot:
    shot_id: str
    position: int
    shot_number: str
    shot_size: str | None
    camera_movement: str | None
    dialogue: str
    duration_ms: int
    prompt: str
    model_strategy: str | None
    cost_tier: str | None
    asset_references: tuple[AssetReference, ...]
    selected_media_id: str | None
    generation_status: GenerationStatus
    quality_issues: tuple[QualityIssue, ...] = ()
    replacements: tuple[ShotReplacement, ...] = ()
    negative_prompt: str = ""
    prompt_template_id: str | None = None
    prompt_variables: dict[str, str] | None = None
    prompt_model_adapter_version: str | None = None
    generation_task_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.shot_id, "SHOT_ID_REQUIRED")
        _require_text(self.shot_number, "SHOT_NUMBER_REQUIRED")
        if self.position < 1:
            raise ContractViolation("INVALID_SHOT_POSITION")
        if self.duration_ms < 1:
            raise ContractViolation("INVALID_SHOT_DURATION")
        if len({(item.asset_id, item.asset_version_id) for item in self.asset_references}) != len(
            self.asset_references
        ):
            raise ContractViolation("DUPLICATE_ASSET_REFERENCE")
        if any(issue.shot_id != self.shot_id for issue in self.quality_issues):
            raise ContractViolation("QUALITY_ISSUE_SHOT_MISMATCH")

    def to_dict(self) -> dict[str, object]:
        return {
            "shot_id": self.shot_id,
            "position": self.position,
            "shot_number": self.shot_number,
            "shot_size": self.shot_size,
            "camera_movement": self.camera_movement,
            "dialogue": self.dialogue,
            "duration_ms": self.duration_ms,
            "prompt": self.prompt,
            "model_strategy": self.model_strategy,
            "cost_tier": self.cost_tier,
            "asset_references": [item.to_dict() for item in self.asset_references],
            "selected_media_id": self.selected_media_id,
            "generation_status": self.generation_status.value,
            "quality_issues": [item.to_dict() for item in self.quality_issues],
            "replacements": [item.to_dict() for item in self.replacements],
            "negative_prompt": self.negative_prompt,
            "prompt_template_id": self.prompt_template_id,
            "prompt_variables": (
                dict(self.prompt_variables)
                if self.prompt_variables is not None
                else None
            ),
            "prompt_model_adapter_version": self.prompt_model_adapter_version,
            "generation_task_id": self.generation_task_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        return cls(
            shot_id=_string(value, "shot_id"),
            position=_integer(value, "position"),
            shot_number=_string(value, "shot_number"),
            shot_size=_optional_string(value, "shot_size"),
            camera_movement=_optional_string(value, "camera_movement"),
            dialogue=_string(value, "dialogue"),
            duration_ms=_integer(value, "duration_ms"),
            prompt=_string(value, "prompt"),
            model_strategy=_optional_string(value, "model_strategy"),
            cost_tier=_optional_string(value, "cost_tier"),
            asset_references=tuple(AssetReference.from_dict(item) for item in _mapping_list(value, "asset_references")),
            selected_media_id=_optional_string(value, "selected_media_id"),
            generation_status=_enum(GenerationStatus, value, "generation_status"),
            quality_issues=tuple(QualityIssue.from_dict(item) for item in _mapping_list(value, "quality_issues")),
            replacements=tuple(ShotReplacement.from_dict(item) for item in _mapping_list(value, "replacements")),
            negative_prompt=str(value.get("negative_prompt") or ""),
            prompt_template_id=_optional_string(value, "prompt_template_id"),
            prompt_variables=(
                {
                    str(key): str(item)
                    for key, item in _object_dict(value, "prompt_variables").items()
                }
                if value.get("prompt_variables") is not None
                else None
            ),
            prompt_model_adapter_version=_optional_string(value, "prompt_model_adapter_version"),
            generation_task_id=_optional_string(value, "generation_task_id"),
        )


@dataclass(frozen=True, slots=True)
class StoryboardVersion:
    number: int
    action: str
    actor_id: str
    occurred_at: datetime
    shots: tuple[Shot, ...]
    digest: str

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ContractViolation("INVALID_STORYBOARD_VERSION")
        _require_text(self.action, "VERSION_ACTION_REQUIRED")
        _require_text(self.actor_id, "ACTOR_ID_REQUIRED")
        _require_aware(self.occurred_at)
        if self.digest != storyboard_version_digest(self.shots):
            raise ContractViolation("INVALID_VERSION_DIGEST")

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "action": self.action,
            "actor_id": self.actor_id,
            "occurred_at": _datetime_text(self.occurred_at),
            "shots": [shot.to_dict() for shot in self.shots],
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        return cls(
            number=_integer(value, "number"),
            action=_string(value, "action"),
            actor_id=_string(value, "actor_id"),
            occurred_at=_datetime(value, "occurred_at"),
            shots=tuple(Shot.from_dict(item) for item in _mapping_list(value, "shots")),
            digest=_string(value, "digest"),
        )


@dataclass(frozen=True, slots=True)
class FrozenStoryboard:
    snapshot_id: str
    storyboard_id: str
    tenant_id: str
    workspace_id: str
    project_id: str
    episode_id: str
    source_version: int
    shots: tuple[Shot, ...]
    frozen_at: datetime
    digest: str

    def __post_init__(self) -> None:
        for value, code in (
            (self.snapshot_id, "SNAPSHOT_ID_REQUIRED"),
            (self.storyboard_id, "STORYBOARD_ID_REQUIRED"),
            (self.tenant_id, "TENANT_ID_REQUIRED"),
            (self.workspace_id, "WORKSPACE_ID_REQUIRED"),
            (self.project_id, "PROJECT_ID_REQUIRED"),
            (self.episode_id, "EPISODE_ID_REQUIRED"),
        ):
            _require_text(value, code)
        if self.source_version < 1:
            raise ContractViolation("INVALID_STORYBOARD_VERSION")
        _require_aware(self.frozen_at)
        if self.digest and len(self.digest) != 64:
            raise ContractViolation("INVALID_FROZEN_DIGEST")

    def verify_digest(self) -> bool:
        return self.digest == frozen_storyboard_digest(self)

    def to_dict(self) -> dict[str, object]:
        return _frozen_payload(self) | {"digest": self.digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        return cls(
            snapshot_id=_string(value, "snapshot_id"),
            storyboard_id=_string(value, "storyboard_id"),
            tenant_id=_string(value, "tenant_id"),
            workspace_id=_string(value, "workspace_id"),
            project_id=_string(value, "project_id"),
            episode_id=_string(value, "episode_id"),
            source_version=_integer(value, "source_version"),
            shots=tuple(Shot.from_dict(item) for item in _mapping_list(value, "shots")),
            frozen_at=_datetime(value, "frozen_at"),
            digest=_string(value, "digest"),
        )


@dataclass(frozen=True, slots=True)
class Storyboard:
    storyboard_id: str
    tenant_id: str
    workspace_id: str
    project_id: str
    episode_id: str
    version: int
    status: StoryboardStatus
    shots: tuple[Shot, ...]
    versions: tuple[StoryboardVersion, ...]
    created_at: datetime
    updated_at: datetime
    frozen_snapshot: FrozenStoryboard | None = None

    def __post_init__(self) -> None:
        for value, code in (
            (self.storyboard_id, "STORYBOARD_ID_REQUIRED"),
            (self.tenant_id, "TENANT_ID_REQUIRED"),
            (self.workspace_id, "WORKSPACE_ID_REQUIRED"),
            (self.project_id, "PROJECT_ID_REQUIRED"),
            (self.episode_id, "EPISODE_ID_REQUIRED"),
        ):
            _require_text(value, code)
        shot_ids = [shot.shot_id for shot in self.shots]
        if len(set(shot_ids)) != len(shot_ids):
            raise ContractViolation("DUPLICATE_SHOT_ID")
        positions = [shot.position for shot in self.shots]
        if len(set(positions)) != len(positions):
            raise ContractViolation("DUPLICATE_SHOT_POSITION")
        if set(positions) != set(range(1, len(self.shots) + 1)):
            raise ContractViolation("NON_CONTIGUOUS_SHOT_POSITIONS")
        if self.version < 1:
            raise ContractViolation("INVALID_STORYBOARD_VERSION")
        if self.versions and self.versions[-1].number != self.version:
            raise ContractViolation("VERSION_HISTORY_MISMATCH")
        _require_aware(self.created_at)
        _require_aware(self.updated_at)
        if self.status is StoryboardStatus.FROZEN:
            if self.frozen_snapshot is None or not self.frozen_snapshot.verify_digest():
                raise ContractViolation("INVALID_FROZEN_SNAPSHOT")
        elif self.frozen_snapshot is not None:
            raise ContractViolation("DRAFT_HAS_FROZEN_SNAPSHOT")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "storyboard_id": self.storyboard_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "episode_id": self.episode_id,
            "version": self.version,
            "status": self.status.value,
            "shots": [shot.to_dict() for shot in self.shots],
            "versions": [version.to_dict() for version in self.versions],
            "created_at": _datetime_text(self.created_at),
            "updated_at": _datetime_text(self.updated_at),
            "frozen_snapshot": self.frozen_snapshot.to_dict() if self.frozen_snapshot else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        if value.get("schema_version", 1) != 1:
            raise ContractViolation("UNSUPPORTED_STORYBOARD_SCHEMA")
        raw_snapshot = value.get("frozen_snapshot")
        snapshot = None if raw_snapshot is None else FrozenStoryboard.from_dict(_mapping(raw_snapshot))
        return cls(
            storyboard_id=_string(value, "storyboard_id"),
            tenant_id=_string(value, "tenant_id"),
            workspace_id=_string(value, "workspace_id"),
            project_id=_string(value, "project_id"),
            episode_id=_string(value, "episode_id"),
            version=_integer(value, "version"),
            status=_enum(StoryboardStatus, value, "status"),
            shots=tuple(Shot.from_dict(item) for item in _mapping_list(value, "shots")),
            versions=tuple(StoryboardVersion.from_dict(item) for item in _mapping_list(value, "versions")),
            created_at=_datetime(value, "created_at"),
            updated_at=_datetime(value, "updated_at"),
            frozen_snapshot=snapshot,
        )


def storyboard_version_digest(shots: tuple[Shot, ...]) -> str:
    return hashlib.sha256(_canonical_json([shot.to_dict() for shot in shots]).encode()).hexdigest()


def frozen_storyboard_digest(snapshot: FrozenStoryboard) -> str:
    return hashlib.sha256(_canonical_json(_frozen_payload(snapshot)).encode()).hexdigest()


def _frozen_payload(snapshot: FrozenStoryboard) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": snapshot.snapshot_id,
        "storyboard_id": snapshot.storyboard_id,
        "tenant_id": snapshot.tenant_id,
        "workspace_id": snapshot.workspace_id,
        "project_id": snapshot.project_id,
        "episode_id": snapshot.episode_id,
        "source_version": snapshot.source_version,
        "shots": [shot.to_dict() for shot in snapshot.shots],
        "frozen_at": _datetime_text(snapshot.frozen_at),
    }


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ContractViolation("NON_SERIALIZABLE_CONTRACT") from error


def _require_text(value: str, code: str) -> None:
    if not value.strip():
        raise ContractViolation(code)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractViolation("TIMEZONE_REQUIRED")


def _datetime_text(value: datetime) -> str:
    _require_aware(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT")
    return cast(Mapping[str, object], value)


def _string(value: Mapping[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT", details={"field": key})
    return raw


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT", details={"field": key})
    return raw


def _integer(value: Mapping[str, object], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT", details={"field": key})
    return raw


def _datetime(value: Mapping[str, object], key: str) -> datetime:
    raw = _string(value, key)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT", details={"field": key}) from error
    _require_aware(parsed)
    return parsed


def _mapping_list(value: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    raw = value.get(key, [])
    if not isinstance(raw, list):
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT", details={"field": key})
    return [_mapping(item) for item in raw]


def _object_dict(value: Mapping[str, object], key: str) -> dict[str, object]:
    raw = _mapping(value.get(key))
    return {str(item_key): item_value for item_key, item_value in raw.items()}


def _enum[T: StrEnum](enum_type: type[T], value: Mapping[str, object], key: str) -> T:
    raw = _string(value, key)
    try:
        return enum_type(raw)
    except ValueError as error:
        raise ContractViolation("INVALID_SERIALIZED_CONTRACT", details={"field": key}) from error
