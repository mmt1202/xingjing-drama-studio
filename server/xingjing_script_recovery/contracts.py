from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from server.xingjing_source_planning.contracts import Contract

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class Change(Contract):
    action: Literal["replace", "insert", "remove"]
    part: Literal["scene", "paragraph", "heading"]
    source_id: Identifier | None = Field(default=None, alias="sourceId")
    target_id: Identifier = Field(alias="targetId")


class Selection(Contract):
    revision: int = Field(ge=1)
    base_version: int = Field(alias="baseVersion", ge=1)
    source_version: int = Field(alias="sourceVersion", ge=1)
    changes: list[Change] = Field(min_length=1, max_length=100)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class Confirmation(Contract):
    selection: Selection
    preview_digest: str = Field(alias="previewDigest", pattern=r"^[a-f0-9]{64}$")
