from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from server.xingjing_generation import (
    GenerationRequest,
    GenerationTask,
    MediaType,
    TaskStatus,
    compute_request_fingerprint,
)

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def make_request(**overrides: object) -> GenerationRequest:
    values: dict[str, object] = {
        "workspace_id": "ws-1",
        "project_id": "project-1",
        "media_type": MediaType.IMAGE,
        "capability": "text_to_image",
        "prompt": "雨夜的霓虹街道",
        "parameters": {"aspect_ratio": "16:9", "seed": 42},
        "input_asset_ids": (),
        "requested_provider_id": None,
        "requested_model_id": None,
    }
    values.update(overrides)
    return GenerationRequest.model_validate(values)


def test_request_contract_round_trips_as_json() -> None:
    request = make_request(parameters={"references": ["asset-1"], "cfg": 7.5})

    restored = GenerationRequest.model_validate_json(request.model_dump_json())

    assert restored == request


def test_request_rejects_non_json_provider_payload() -> None:
    with pytest.raises(ValidationError):
        make_request(parameters={"invalid": object()})


def test_request_fingerprint_is_stable_across_mapping_order() -> None:
    first = make_request(parameters={"seed": 42, "aspect_ratio": "16:9"})
    second = make_request(parameters={"aspect_ratio": "16:9", "seed": 42})

    assert compute_request_fingerprint(first) == compute_request_fingerprint(second)
    assert len(compute_request_fingerprint(first)) == 64


def test_task_contract_preserves_idempotency_scope_and_round_trips() -> None:
    request = make_request()
    task = GenerationTask.create(
        task_id="task-1",
        request=request,
        idempotency_key="generate-shot-7",
        created_at=NOW,
        timeout_at=NOW + timedelta(minutes=10),
    )

    restored = GenerationTask.model_validate_json(task.model_dump_json())

    assert restored == task
    assert task.status is TaskStatus.QUEUED
    assert task.idempotency_scope == "ws-1:generate-shot-7"
    assert task.request_fingerprint == compute_request_fingerprint(request)


@pytest.mark.parametrize("key", ["", " has-space", "x" * 129, "中文键"])
def test_task_rejects_invalid_idempotency_keys(key: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        GenerationTask.create(
            task_id="task-1",
            request=make_request(),
            idempotency_key=key,
            created_at=NOW,
            timeout_at=NOW + timedelta(minutes=10),
        )


def test_task_requires_timezone_aware_ordered_timeout() -> None:
    with pytest.raises((ValueError, ValidationError)):
        GenerationTask.create(
            task_id="task-1",
            request=make_request(),
            idempotency_key="request-1",
            created_at=NOW,
            timeout_at=NOW,
        )
