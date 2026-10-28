"""Bounded JSON commands for image candidates and manually evaluated variants."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from server.xingjing_generation.contracts import GenerationAssetReference

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Target = Literal["douyin", "kuaishou", "wechat", "xiaohongshu"]
AspectRatio = Literal["9:16", "3:4", "1:1", "16:9"]


class CoverFailure(RuntimeError):
    def __init__(self, code: str, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class SafeArea(StrictModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def inside_frame(self) -> SafeArea:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("COVER_SAFE_AREA_OUTSIDE_FRAME")
        return self


class PlanFields(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=12000)
    aspectRatio: AspectRatio
    titleSafeArea: SafeArea


class CreatePlan(PlanFields):
    finalVersionId: Identifier
    target: Target


class Command(StrictModel):
    action: Literal[
        "update",
        "generate",
        "attachCandidate",
        "saveVariant",
        "evaluateVariant",
        "recommendVariant",
        "selectVariant",
        "apply",
    ]
    version: int = Field(ge=1, strict=True)
    payload: dict[str, object] = Field(default_factory=dict)


class AttachCandidate(StrictModel):
    assetId: Identifier


class Variant(StrictModel):
    id: Identifier | None = None
    label: str = Field(min_length=1, max_length=80)
    coverAssetId: Identifier
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=3000)
    tags: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]] = Field(
        max_length=20
    )


class Evaluate(StrictModel):
    variantId: Identifier
    score: float | None = Field(ge=0, le=100, allow_inf_nan=False)
    note: str = Field(min_length=1, max_length=3000)


class Choose(StrictModel):
    variantId: Identifier


class Generate(StrictModel):
    providerId: Identifier | None = None
    modelId: Identifier | None = None
    capability: Literal["image_generation"] = "image_generation"
    prompt: str | None = Field(default=None, min_length=1, max_length=12000)
    parameters: dict[str, object] = Field(default_factory=dict)
    inputAssetIds: list[Identifier] = Field(default_factory=list, max_length=12)
    inputAssetReferences: list[GenerationAssetReference] = Field(default_factory=list, max_length=12)


ACTION_MODELS: dict[str, type[BaseModel]] = {
    "update": PlanFields,
    "generate": Generate,
    "attachCandidate": AttachCandidate,
    "saveVariant": Variant,
    "evaluateVariant": Evaluate,
    "recommendVariant": Choose,
    "selectVariant": Choose,
    "apply": StrictModel,
}
