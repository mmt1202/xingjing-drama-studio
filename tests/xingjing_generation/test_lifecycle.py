from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_generation import (
    CallbackDisposition,
    Failure,
    GenerationRequest,
    GenerationTask,
    InvalidTransition,
    MediaType,
    ProviderCallback,
    RetryPolicy,
    TaskStatus,
    apply_provider_callback,
    cancel_task,
    expire_task,
    mark_cancelled,
    prepare_retry,
    queue_due_retry,
    start_task,
)

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def queued_task(*, timeout_seconds: int = 600) -> GenerationTask:
    request = GenerationRequest(
        workspace_id="ws-1",
        project_id="project-1",
        media_type=MediaType.VIDEO,
        capability="image_to_video",
        prompt="镜头缓慢推进",
        parameters={"duration": 5},
        input_asset_ids=("asset-1",),
    )
    return GenerationTask.create(
        task_id="task-1",
        request=request,
        idempotency_key="shot-1-video-v1",
        created_at=NOW,
        timeout_at=NOW + timedelta(seconds=timeout_seconds),
    )


def running_task() -> GenerationTask:
    return start_task(
        queued_task(),
        provider_id="provider-a",
        model_id="video-v3",
        provider_job_id="job-1",
        at=NOW + timedelta(seconds=1),
    )


def test_queued_task_starts_with_resolved_provider_evidence() -> None:
    task = running_task()

    assert task.status is TaskStatus.RUNNING
    assert task.attempt == 1
    assert task.resolved_provider_id == "provider-a"
    assert task.resolved_model_id == "video-v3"
    assert task.provider_job_id == "job-1"
    assert task.version == 2


def test_illegal_state_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransition, match="queued.*cancelled"):
        mark_cancelled(queued_task(), at=NOW + timedelta(seconds=1))


def test_retryable_failure_uses_capped_exponential_backoff() -> None:
    task = running_task()
    policy = RetryPolicy(max_attempts=5, initial_delay_seconds=10, multiplier=3, max_delay_seconds=25)
    failure = Failure(code="PROVIDER_BUSY", message="供应商繁忙", retryable=True)

    first = prepare_retry(task, failure=failure, policy=policy, at=NOW + timedelta(seconds=2))
    second_running = start_task(
        queue_due_retry(first, at=NOW + timedelta(seconds=12)),
        provider_id="provider-a",
        model_id="video-v3",
        provider_job_id="job-2",
        at=NOW + timedelta(seconds=13),
    )
    second = prepare_retry(second_running, failure=failure, policy=policy, at=NOW + timedelta(seconds=14))

    assert first.status is TaskStatus.RETRYING
    assert first.next_attempt_at == NOW + timedelta(seconds=12)
    assert second.next_attempt_at == NOW + timedelta(seconds=39)


def test_non_retryable_or_exhausted_failure_becomes_terminal() -> None:
    policy = RetryPolicy(max_attempts=1, initial_delay_seconds=10)
    failed = prepare_retry(
        running_task(),
        failure=Failure(code="INVALID_INPUT", message="输入不合法", retryable=False),
        policy=policy,
        at=NOW + timedelta(seconds=2),
    )

    assert failed.status is TaskStatus.FAILED
    assert failed.completed_at == NOW + timedelta(seconds=2)
    assert failed.next_attempt_at is None


def test_retry_cannot_be_requeued_before_due_time() -> None:
    retrying = prepare_retry(
        running_task(),
        failure=Failure(code="RATE_LIMITED", message="限流", retryable=True),
        policy=RetryPolicy(max_attempts=3, initial_delay_seconds=10),
        at=NOW + timedelta(seconds=2),
    )

    with pytest.raises(InvalidTransition, match="尚未到达重试时间"):
        queue_due_retry(retrying, at=NOW + timedelta(seconds=11))


def test_cancel_is_immediate_when_queued_and_two_phase_when_running() -> None:
    queued_cancelled = cancel_task(queued_task(), at=NOW + timedelta(seconds=1))
    cancelling = cancel_task(running_task(), at=NOW + timedelta(seconds=2))
    cancelled = mark_cancelled(cancelling, at=NOW + timedelta(seconds=3))

    assert queued_cancelled.status is TaskStatus.CANCELLED
    assert queued_cancelled.completed_at == NOW + timedelta(seconds=1)
    assert cancelling.status is TaskStatus.CANCELLING
    assert cancelled.status is TaskStatus.CANCELLED


def test_timeout_is_terminal_and_does_not_become_user_cancellation() -> None:
    timed_out = expire_task(running_task(), at=NOW + timedelta(minutes=10))

    assert timed_out.status is TaskStatus.TIMEOUT
    assert timed_out.failure is not None
    assert timed_out.failure.code == "TASK_TIMEOUT"


def test_expire_rejects_check_before_deadline() -> None:
    with pytest.raises(InvalidTransition, match="尚未超时"):
        expire_task(running_task(), at=NOW + timedelta(minutes=9))


def test_task_cannot_start_at_or_after_deadline() -> None:
    with pytest.raises(InvalidTransition, match="已经超时"):
        start_task(
            queued_task(),
            provider_id="provider-a",
            model_id="video-v3",
            provider_job_id="job-late",
            at=NOW + timedelta(minutes=10),
        )


def test_success_callback_completes_matching_running_attempt() -> None:
    result = apply_provider_callback(
        running_task(),
        ProviderCallback(
            provider_job_id="job-1",
            attempt=1,
            event_id="evt-1",
            succeeded=True,
            output_asset_ids=("generated-1",),
        ),
        at=NOW + timedelta(seconds=5),
    )

    assert result.disposition is CallbackDisposition.APPLIED
    assert result.task.status is TaskStatus.SUCCEEDED
    assert result.task.output_asset_ids == ("generated-1",)


def test_callback_after_deadline_times_out_instead_of_winning_race() -> None:
    result = apply_provider_callback(
        running_task(),
        ProviderCallback(
            provider_job_id="job-1",
            attempt=1,
            event_id="evt-after-deadline",
            succeeded=True,
            output_asset_ids=("too-late",),
        ),
        at=NOW + timedelta(minutes=10),
    )

    assert result.disposition is CallbackDisposition.IGNORED_LATE
    assert result.task.status is TaskStatus.TIMEOUT
    assert result.task.output_asset_ids == ()


@pytest.mark.parametrize("terminalizer", ["cancel", "timeout"])
def test_late_success_callback_cannot_resurrect_terminal_task(terminalizer: str) -> None:
    task = running_task()
    if terminalizer == "cancel":
        task = mark_cancelled(cancel_task(task, at=NOW + timedelta(seconds=2)), at=NOW + timedelta(seconds=3))
    else:
        task = expire_task(task, at=NOW + timedelta(minutes=10))

    result = apply_provider_callback(
        task,
        ProviderCallback(
            provider_job_id="job-1",
            attempt=1,
            event_id="evt-late",
            succeeded=True,
            output_asset_ids=("late-asset",),
        ),
        at=NOW + timedelta(minutes=11),
    )

    assert result.disposition is CallbackDisposition.IGNORED_LATE
    assert result.task == task


def test_callback_from_old_attempt_is_ignored() -> None:
    retrying = prepare_retry(
        running_task(),
        failure=Failure(code="RATE_LIMITED", message="限流", retryable=True),
        policy=RetryPolicy(max_attempts=3, initial_delay_seconds=1),
        at=NOW + timedelta(seconds=2),
    )
    running_again = start_task(
        queue_due_retry(retrying, at=NOW + timedelta(seconds=3)),
        provider_id="provider-a",
        model_id="video-v3",
        provider_job_id="job-2",
        at=NOW + timedelta(seconds=4),
    )

    result = apply_provider_callback(
        running_again,
        ProviderCallback(
            provider_job_id="job-1",
            attempt=1,
            event_id="evt-old",
            succeeded=True,
            output_asset_ids=("old-asset",),
        ),
        at=NOW + timedelta(seconds=5),
    )

    assert result.disposition is CallbackDisposition.IGNORED_STALE_ATTEMPT
    assert result.task == running_again


def test_duplicate_callback_event_is_idempotent() -> None:
    callback = ProviderCallback(
        provider_job_id="job-1",
        attempt=1,
        event_id="evt-1",
        succeeded=True,
        output_asset_ids=("generated-1",),
    )
    first = apply_provider_callback(running_task(), callback, at=NOW + timedelta(seconds=5))
    duplicate = apply_provider_callback(first.task, callback, at=NOW + timedelta(seconds=6))

    assert duplicate.disposition is CallbackDisposition.DUPLICATE
    assert duplicate.task == first.task
