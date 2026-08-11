from datetime import UTC, datetime, timedelta

from server.xingjing_generation import (
    GenerationRequest,
    GenerationTask,
    IdempotencyDisposition,
    MediaType,
    assess_idempotency,
)

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def request(prompt: str = "雨夜") -> GenerationRequest:
    return GenerationRequest(
        workspace_id="ws-1",
        project_id="project-1",
        media_type=MediaType.IMAGE,
        capability="text_to_image",
        prompt=prompt,
    )


def existing_task() -> GenerationTask:
    return GenerationTask.create(
        task_id="task-1",
        request=request(),
        idempotency_key="shot-1",
        created_at=NOW,
        timeout_at=NOW + timedelta(minutes=10),
    )


def test_same_key_and_payload_replays_existing_task() -> None:
    decision = assess_idempotency(existing_task(), request())

    assert decision.disposition is IdempotencyDisposition.REPLAY
    assert decision.task_id == "task-1"


def test_same_key_with_different_payload_is_a_conflict() -> None:
    decision = assess_idempotency(existing_task(), request(prompt="晴天"))

    assert decision.disposition is IdempotencyDisposition.CONFLICT
    assert decision.task_id == "task-1"
    assert decision.existing_fingerprint != decision.incoming_fingerprint


def test_idempotency_decision_is_serializable_for_api_adapters() -> None:
    decision = assess_idempotency(existing_task(), request())

    assert type(decision).model_validate_json(decision.model_dump_json()) == decision
