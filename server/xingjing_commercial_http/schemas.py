from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MilestoneRequest(_StrictRequest):
    title: str
    amount_minor: int
    acceptance_criteria: str
    due_at: datetime | None = None


class PublishOrderRequest(_StrictRequest):
    owner_workspace_id: str | None = None
    title: str
    requirements: str
    budget_minor: int
    currency: str
    milestones: list[MilestoneRequest] = Field(min_length=1)


class QuoteRequest(_StrictRequest):
    amount_minor: int
    currency: str
    proposal: str
    valid_until: datetime


class ContractRequest(_StrictRequest):
    content_ref: str
    content_digest: str
    amount_minor: int


class DeliveryRequest(_StrictRequest):
    artifact_version_id: str
    artifact_digest: str
    note: str | None = None


class ReturnDeliveryRequest(_StrictRequest):
    reason: str


class AcceptDeliveryRequest(_StrictRequest):
    evidence_ref: str


class OpenDisputeRequest(_StrictRequest):
    kind: str
    reason: str


class ResolveDisputeRequest(_StrictRequest):
    resolution: str


class SettlementConfirmationRequest(_StrictRequest):
    amount_minor: int
    currency: str
