"""Version references identify authoritative domain records, never disk files."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Kind = Literal["script", "asset", "shot", "storyboard", "final"]
KINDS = ("script", "asset", "shot", "storyboard", "final")
VIEW_PERMISSIONS = {
    "script": "script.view",
    "asset": "asset.view",
    "shot": "shot.view",
    "storyboard": "shot.view",
    "final": "final.view",
}
MANAGE_PERMISSIONS = {kind: permission.replace(".view", ".manage") for kind, permission in VIEW_PERMISSIONS.items()}
SOURCE_DATABASES = {
    "script": "XINGJING_CONTENT_DATABASE_URL",
    "asset": "XINGJING_ASSET_DATABASE_URL",
    "shot": "XINGJING_STORYBOARD_DATABASE_URL",
    "storyboard": "XINGJING_STORYBOARD_DATABASE_URL",
    "final": "XINGJING_EDITING_DATABASE_URL",
}
SCAN_LIMIT = 5000
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_CHANGES = 2000


class VersionFailure(RuntimeError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class Ref(StrictModel):
    kind: Kind
    objectId: Identifier
    versionId: Identifier
    shotId: Identifier | None = None

    @model_validator(mode="after")
    def canonical(self) -> Ref:
        if self.kind in {"script", "shot", "storyboard"}:
            if (
                not self.versionId.isascii()
                or not self.versionId.isdigit()
                or int(self.versionId) < 1
                or str(int(self.versionId)) != self.versionId
            ):
                raise ValueError("VERSION_REFERENCE_INVALID")
        if (self.kind == "shot") != bool(self.shotId):
            raise ValueError("VERSION_SHOT_REFERENCE_INVALID")
        return self

    def dto(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


class VersionAction(StrictModel):
    action: Literal["snapshot", "lock", "restore"]
    ref: Ref
    expectedRevision: int = Field(ge=0, strict=True)
    sourceDigest: Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]
    note: str = Field(min_length=1, max_length=1000)
    label: str | None = Field(default=None, min_length=1, max_length=160)


class ReportRequest(StrictModel):
    baseline: Ref
    candidate: Ref


def require_kind(context, kind: str, *, manage: bool = False) -> None:
    permission = (MANAGE_PERMISSIONS if manage else VIEW_PERMISSIONS)[kind]
    if permission not in context.permissions:
        raise VersionFailure("VERSION_PERMISSION_DENIED", 403)
