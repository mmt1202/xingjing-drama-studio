"""Signed adapter evidence and administrator commands use separate contracts."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
TraceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")]
Minor = Annotated[StrictInt, Field(ge=0, le=9_007_199_254_740_991)]


class RecoveryError(ValueError):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ReceiptOutput(Contract):
    source_path: str = Field(alias="sourcePath", min_length=1, max_length=1024)
    sha256: Digest
    size_bytes: Annotated[StrictInt, Field(gt=0, le=512 * 1024 * 1024)] = Field(alias="sizeBytes")
    mime_type: Literal["image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm", "video/quicktime"] = Field(
        alias="mimeType"
    )


class ProviderReceipt(Contract):
    version: Literal[1] = 1
    receipt_id: UUID = Field(alias="receiptId")
    workspace_id: Identifier = Field(alias="workspaceId")
    project_id: Identifier = Field(alias="projectId")
    task_id: Identifier = Field(alias="taskId")
    execution_id: Identifier = Field(alias="executionId")
    kind: Literal["gateway", "native_image"]
    request_fingerprint: Digest = Field(alias="requestFingerprint")
    provider_id: str = Field(alias="providerId", min_length=1, max_length=64)
    model_id: str = Field(alias="modelId", min_length=1, max_length=255)
    provider_job_id: Identifier | None = Field(default=None, alias="providerJobId")
    upstream_request_id: TraceId = Field(alias="upstreamRequestId")
    provider_receipt_ref: TraceId = Field(alias="providerReceiptRef")
    evidence_kind: Literal["provider_terminal", "provider_not_accepted"] = Field(alias="evidenceKind")
    attempt: Annotated[StrictInt, Field(ge=1, le=100)]
    status: Literal["succeeded", "failed"]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    estimated_minor: Annotated[StrictInt, Field(gt=0, le=9_007_199_254_740_991)] = Field(alias="estimatedMinor")
    actual_minor: Minor = Field(alias="actualMinor")
    pricing_version: str = Field(alias="pricingVersion", min_length=1, max_length=128)
    completed_at: datetime = Field(alias="completedAt")
    observed_at: datetime = Field(alias="observedAt")
    outputs: tuple[ReceiptOutput, ...] = Field(default=(), max_length=8)

    @field_validator("completed_at", "observed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("RECOVERY_RECEIPT_INVALID")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if bool(self.outputs) != (self.status == "succeeded") or self.actual_minor > self.estimated_minor:
            raise ValueError("RECOVERY_RECEIPT_INVALID")
        if self.evidence_kind == "provider_not_accepted" and (
            self.status != "failed" or self.actual_minor or self.provider_job_id
        ):
            raise ValueError("RECOVERY_RECEIPT_INVALID")
        if self.kind == "gateway" and self.evidence_kind == "provider_terminal" and not self.provider_job_id:
            raise ValueError("RECOVERY_RECEIPT_INVALID")
        return self


class PreviewCommand(Contract):
    receipt_id: UUID = Field(alias="receiptId")
    expected_version: Annotated[StrictInt, Field(ge=1)] = Field(alias="expectedVersion")


class ResolveCommand(Contract):
    preview_id: UUID = Field(alias="previewId")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1000)]
