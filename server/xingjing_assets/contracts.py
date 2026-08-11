from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self


class AssetKind(StrEnum):
    CHARACTER = "character"
    SCENE = "scene"
    PROP = "prop"
    COSTUME = "costume"
    VOICE = "voice"


class RightsStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReuseScope(StrEnum):
    OWNER_PROJECT_ONLY = "owner_project_only"
    PROJECT_ALLOWLIST = "project_allowlist"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class AssetSource:
    source_type: str
    source_id: str
    evidence_object_key: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        _validate_sha256(self.evidence_sha256)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class RightsEvidence:
    rights_id: str
    status: RightsStatus
    reuse_scope: ReuseScope
    allowed_project_ids: tuple[str, ...]
    evidence_object_key: str
    evidence_sha256: str
    holder: str | None = None
    valid_until: str | None = None

    def __post_init__(self) -> None:
        _validate_sha256(self.evidence_sha256)

    def permits(self, project_id: str) -> bool:
        if self.status is not RightsStatus.VERIFIED:
            return False
        if self.valid_until is not None:
            expires_at = datetime.fromisoformat(self.valid_until.replace("Z", "+00:00"))
            if expires_at <= datetime.now(UTC):
                return False
        return self.reuse_scope is ReuseScope.WORKSPACE or (
            self.reuse_scope is ReuseScope.PROJECT_ALLOWLIST and project_id in self.allowed_project_ids
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["reuse_scope"] = self.reuse_scope.value
        value["allowed_project_ids"] = list(self.allowed_project_ids)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            rights_id=value["rights_id"],
            status=RightsStatus(value["status"]),
            reuse_scope=ReuseScope(value["reuse_scope"]),
            allowed_project_ids=tuple(value.get("allowed_project_ids", ())),
            evidence_object_key=value["evidence_object_key"],
            evidence_sha256=value["evidence_sha256"],
            holder=value.get("holder"),
            valid_until=value.get("valid_until"),
        )


@dataclass(frozen=True, slots=True)
class FrozenScriptSnapshotRef:
    """M03 冻结结果的最小只读交接契约。"""

    snapshot_id: str
    workspace_id: str
    project_id: str
    script_version_id: str
    content_sha256: str
    frozen_at: str

    def __post_init__(self) -> None:
        _validate_sha256(self.content_sha256)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class AssetVersion:
    version_id: str
    sequence: int
    content: dict[str, Any]
    content_sha256: str
    change_note: str
    created_at: str
    derived_from_version_id: str | None = None

    def __post_init__(self) -> None:
        _validate_sha256(self.content_sha256)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class AssetReference:
    reference_id: str
    workspace_id: str
    project_id: str
    asset_id: str
    asset_version_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Asset:
    asset_id: str
    workspace_id: str
    owner_project_id: str
    kind: AssetKind
    name: str
    source: AssetSource
    frozen_snapshot: FrozenScriptSnapshotRef
    current_version_id: str
    revision: int
    versions: tuple[AssetVersion, ...]
    rights: tuple[RightsEvidence, ...] = ()
    references: tuple[AssetReference, ...] = ()

    @property
    def current_version(self) -> AssetVersion:
        return next(version for version in self.versions if version.version_id == self.current_version_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "workspace_id": self.workspace_id,
            "owner_project_id": self.owner_project_id,
            "kind": self.kind.value,
            "name": self.name,
            "source": self.source.to_dict(),
            "frozen_snapshot": self.frozen_snapshot.to_dict(),
            "current_version_id": self.current_version_id,
            "revision": self.revision,
            "versions": [version.to_dict() for version in self.versions],
            "rights": [item.to_dict() for item in self.rights],
            "references": [item.to_dict() for item in self.references],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        return cls(
            asset_id=value["asset_id"],
            workspace_id=value["workspace_id"],
            owner_project_id=value["owner_project_id"],
            kind=AssetKind(value["kind"]),
            name=value["name"],
            source=AssetSource.from_dict(value["source"]),
            frozen_snapshot=FrozenScriptSnapshotRef.from_dict(value["frozen_snapshot"]),
            current_version_id=value["current_version_id"],
            revision=value["revision"],
            versions=tuple(AssetVersion.from_dict(item) for item in value["versions"]),
            rights=tuple(RightsEvidence.from_dict(item) for item in value.get("rights", ())),
            references=tuple(AssetReference.from_dict(item) for item in value.get("references", ())),
        )


def _validate_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise ValueError("digest must be a 64-character SHA-256 hex value")
