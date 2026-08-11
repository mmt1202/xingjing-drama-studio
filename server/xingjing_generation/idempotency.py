from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .contracts import GenerationRequest, GenerationTask, compute_request_fingerprint


class IdempotencyDisposition(StrEnum):
    REPLAY = "replay"
    CONFLICT = "conflict"


class IdempotencyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: IdempotencyDisposition
    task_id: str
    existing_fingerprint: str
    incoming_fingerprint: str


def assess_idempotency(existing: GenerationTask, incoming: GenerationRequest) -> IdempotencyDecision:
    incoming_fingerprint = compute_request_fingerprint(incoming)
    disposition = (
        IdempotencyDisposition.REPLAY
        if existing.request_fingerprint == incoming_fingerprint
        else IdempotencyDisposition.CONFLICT
    )
    return IdempotencyDecision(
        disposition=disposition,
        task_id=existing.task_id,
        existing_fingerprint=existing.request_fingerprint,
        incoming_fingerprint=incoming_fingerprint,
    )
