from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
