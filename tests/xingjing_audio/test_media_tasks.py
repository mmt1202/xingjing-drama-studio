from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from server.xingjing_audio import (
    FallbackPolicy,
    InvalidTaskTransition,
    MediaTask,
    MediaTaskKind,
    MediaTaskStatus,
    ProviderFailure,
)
from server.xingjing_audio_runtime.runtime import AudioRuntime


@pytest.mark.asyncio
async def test_provider_callback_validates_billing_before_importing_output() -> None:
    class Runtime(AudioRuntime):
        async def _get_task(self, scope, task_id):
            return {
                "id": task_id,
                "provider_task_id": "remote-1",
                "provider_name": "provider-a",
                "status": "running",
                "version": 2,
                "attempt_count": 1,
                "max_attempts": 2,
            }

    class Storage:
        def __init__(self) -> None:
            self.imports = 0

        async def inspect(self, **_kwargs):
            raise AssertionError("not used")

        async def import_provider_output(self, **_kwargs):
            self.imports += 1
            raise AssertionError("output must not be imported before billing validation")

    storage = Storage()
    runtime = Runtime(
        repository=None,
        session_factory=None,
        context_resolver=None,
        object_storage=storage,
        provider_callback_secret="callback-secret",
    )
    payload = {
        "tenantId": "tenant-a",
        "workspaceId": "workspace-a",
        "projectId": "project-a",
        "taskId": "task-a",
        "providerTaskId": "remote-1",
        "eventId": "event-a",
        "status": "succeeded",
        "outputs": [{"outputId": "audio-a", "sourcePath": "audio.wav"}],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"callback-secret", raw, hashlib.sha256).hexdigest()

    with pytest.raises(ValueError, match="AUDIO_PROVIDER_CALLBACK_BILLING_REQUIRED"):
        await runtime.apply_provider_callback(raw_body=raw, signature=signature, payload=payload)

    assert storage.imports == 0


def test_media_task_contract_preserves_external_references() -> None:
    task = MediaTask.queued(
        task_id="task-1",
        workspace_id="ws-1",
        project_id="p-1",
        kind=MediaTaskKind.LIP_SYNC,
        idempotency_key="request-1",
        input_version_ids=("audio:4", "subtitle:6", "video:2"),
        selection_start_ms=100,
        selection_end_ms=300,
        fallback_policy=FallbackPolicy.KEEP_SOURCE,
    )

    running = task.start(provider="provider-id", provider_job_id="remote-job-9")
    succeeded = running.succeed(output_object_key="lip-sync/task-1.mp4", output_version_id="video:3")

    assert succeeded.status is MediaTaskStatus.SUCCEEDED
    assert succeeded.provider_job_id == "remote-job-9"
    assert succeeded.output_object_key == "lip-sync/task-1.mp4"


def test_failed_task_keeps_failure_and_explicit_source_fallback() -> None:
    task = MediaTask.queued(
        "task-1",
        "ws-1",
        "p-1",
        MediaTaskKind.LIP_SYNC,
        "request-1",
        ("video:2",),
        0,
        500,
        FallbackPolicy.KEEP_SOURCE,
    ).start("provider-id", "remote-job")

    failed = task.fail(ProviderFailure(code="FACE_NOT_VISIBLE", message="face unavailable", retryable=False))
    fallback = failed.apply_fallback(source_version_id="video:2")

    assert failed.status is MediaTaskStatus.FAILED
    assert fallback.status is MediaTaskStatus.FALLBACK
    assert fallback.output_version_id == "video:2"
    assert fallback.failure == failed.failure


def test_cancellation_is_terminal_and_cannot_publish_output() -> None:
    cancelled = MediaTask.queued(
        "task-1",
        "ws-1",
        "p-1",
        MediaTaskKind.TTS,
        "request-1",
        ("subtitle:6",),
        0,
        500,
        FallbackPolicy.NONE,
    ).cancel(reason="user")

    assert cancelled.status is MediaTaskStatus.CANCELLED
    with pytest.raises(InvalidTaskTransition):
        cancelled.succeed("audio/task-1.wav", "audio:7")


def test_running_cancellation_blocks_output_until_worker_confirms_terminal_state() -> None:
    running = MediaTask.queued(
        "task-1",
        "ws-1",
        "p-1",
        MediaTaskKind.TTS,
        "request-1",
        ("subtitle:6",),
        0,
        500,
        FallbackPolicy.NONE,
    ).start("tts-provider", "remote-1")

    cancelling = running.request_cancel(reason="user")

    assert cancelling.status is MediaTaskStatus.CANCELLING
    with pytest.raises(InvalidTaskTransition):
        cancelling.succeed("audio/task-1.wav", "audio:7")
    assert cancelling.confirm_cancelled().status is MediaTaskStatus.CANCELLED
