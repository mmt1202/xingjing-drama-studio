"""Strict operator input shared by the control API and routing decisions."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FAILURE_CEILING = 3
SUCCESS_SAMPLE_SIZE = 20
MIN_SUCCESS_SAMPLES = 5


class OperatorInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class RoutingOptions(OperatorInput):
    mode: Literal["priority", "weighted"] = "priority"
    weights: dict[str, int] = Field(default_factory=dict)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    maxLatencyMs: int | None = Field(default=None, ge=1, le=3_600_000)
    maxConsecutiveFailures: int | None = Field(default=None, ge=1, le=FAILURE_CEILING)
    minSuccessRate: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class ModelInput(OperatorInput):
    providerId: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    providerModelName: str = Field(min_length=1, max_length=255)
    displayName: str = Field(min_length=1, max_length=255)
    modelVersion: str = Field(min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=64)
    mediaTypes: list[Literal["image", "video", "audio", "text"]] = Field(min_length=1, max_length=4)
    capabilities: list[str] = Field(min_length=1, max_length=40)
    parameterSchema: dict[str, dict[str, object]] = Field(default_factory=dict)
    credentialRef: str | None = Field(
        default=None, max_length=128, pattern=r"^(env|secret)://[A-Za-z0-9][A-Za-z0-9._/-]*$"
    )
    maxConcurrency: int = Field(ge=1, le=10000)
    active: bool = False

    @model_validator(mode="after")
    def validate_parameters(self):
        if len(self.mediaTypes) != len(set(self.mediaTypes)) or len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("DUPLICATE_CAPABILITY")
        if any(not value.strip() or value != value.strip() or len(value) > 128 for value in self.capabilities):
            raise ValueError("INVALID_CAPABILITY")
        if any(
            not value.strip() for value in (self.displayName, self.providerModelName, self.modelVersion, self.region)
        ):
            raise ValueError("EMPTY_MODEL_FIELD")
        for key, rule in self.parameterSchema.items():
            if not key.strip() or len(key) > 128 or set(rule) - {"type", "enum"}:
                raise ValueError("INVALID_PARAMETER_SCHEMA")
            kind = rule.get("type")
            if kind not in {"string", "integer", "number", "boolean"}:
                raise ValueError("INVALID_PARAMETER_TYPE")
            values = rule.get("enum")
            if values is not None and (
                not isinstance(values, list)
                or not values
                or len(values) > 200
                or any(not parameter_matches(value, str(kind)) for value in values)
            ):
                raise ValueError("INVALID_PARAMETER_ENUM")
        return self


class PriceInput(OperatorInput):
    pricingVersion: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    estimatedMinor: int = Field(ge=1, le=2_147_483_647)


class ActiveInput(OperatorInput):
    active: bool


class PolicyInput(OperatorInput):
    capability: str = Field(min_length=1, max_length=128)
    mediaType: Literal["image", "video", "audio", "text"]
    allowedRegistryKeys: list[str] = Field(min_length=1, max_length=100)
    preferredRegistryKeys: list[str] = Field(default_factory=list, max_length=100)
    maxEstimatedMinor: int | None = Field(default=None, ge=1, le=2_147_483_647)
    allowFallback: bool = False
    routingOptions: RoutingOptions = Field(default_factory=RoutingOptions)

    @model_validator(mode="after")
    def validate_references(self):
        allowed, preferred = self.allowedRegistryKeys, self.preferredRegistryKeys
        if (
            len(allowed) != len(set(allowed))
            or len(preferred) != len(set(preferred))
            or not set(preferred) <= set(allowed)
        ):
            raise ValueError("INVALID_POLICY_REFERENCES")
        if self.maxEstimatedMinor is not None and self.routingOptions.currency is None:
            raise ValueError("POLICY_CURRENCY_REQUIRED")
        weights = self.routingOptions.weights
        if self.routingOptions.mode == "weighted" and (
            set(weights) != set(preferred or allowed)
            or sum(weights.values()) != 100
            or any(weight < 1 or weight > 100 for weight in weights.values())
        ):
            raise ValueError("POLICY_WEIGHTS_INVALID")
        return self


class ModelCommand(OperatorInput):
    action: Literal["upsert_model", "set_model_active", "publish_pricing", "upsert_policy"]
    registryKey: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)
    payload: dict[str, object]


def parameter_matches(value: object, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return False
