from __future__ import annotations

from datetime import timedelta

from server.xingjing_editing import RenderCallbackDisposition, RenderStatus, StoredRenderObject

from .fakes import NOW, OUTPUT_KEY, EditingHttpHarness


def test_submit_render_and_read_task_status_through_domain_service() -> None:
    harness = EditingHttpHarness()

    submitted = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="render-v4"),
        json=harness.render_payload(),
    )

    assert submitted.status_code == 202
    task = submitted.json()
    assert task["status"] == RenderStatus.RUNNING
    assert task["task_revision"] == 2
    assert task["attempt"] == 1
    assert harness.renderer.submissions == [
        (task["task_id"], 1, task["preview"]["input_snapshot_sha256"]),
    ]

    fetched = harness.client.get(
        f"/api/v1/projects/project-1/render-tasks/{task['task_id']}",
        headers=harness.headers(permissions="final.view"),
    )

    assert fetched.status_code == 200
    assert fetched.json() == task


def test_render_submission_is_idempotent_and_conflicting_payload_returns_409() -> None:
    harness = EditingHttpHarness()
    headers = harness.headers(idempotency_key="render-v4")

    first = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=headers,
        json=harness.render_payload(),
    )
    replay = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=headers,
        json=harness.render_payload(),
    )
    conflict = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=headers,
        json=harness.render_payload(width=1_280),
    )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert len(harness.renderer.submissions) == 1
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_render_submission_maps_timeline_version_conflict() -> None:
    harness = EditingHttpHarness()

    response = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="stale-render"),
        json=harness.render_payload(expected_revision=3),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "VERSION_CONFLICT",
        "message": "期望版本 3，当前版本 4",
        "expected_version": 3,
        "current_version": 4,
    }


def test_permissions_are_resolved_on_every_request() -> None:
    harness = EditingHttpHarness()
    submitted = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="permission-render"),
        json=harness.render_payload(),
    )
    task_id = submitted.json()["task_id"]

    revoked = harness.client.get(
        f"/api/v1/projects/project-1/render-tasks/{task_id}",
        headers=harness.headers(permissions=""),
    )

    assert revoked.status_code == 403
    assert revoked.json()["detail"]["code"] == "PERMISSION_DENIED"


def test_cross_workspace_task_lookup_does_not_disclose_task() -> None:
    harness = EditingHttpHarness()
    submitted = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="workspace-render"),
        json=harness.render_payload(),
    )
    headers = harness.headers(permissions="final.view")
    headers["X-Workspace-ID"] = "workspace-2"

    hidden = harness.client.get(
        f"/api/v1/projects/project-1/render-tasks/{submitted.json()['task_id']}",
        headers=headers,
    )

    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "RENDER_TASK_NOT_FOUND"


def test_cancel_running_render_requests_provider_cancellation() -> None:
    harness = EditingHttpHarness()
    submitted = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="cancel-render"),
        json=harness.render_payload(),
    ).json()

    cancelled = harness.client.post(
        f"/api/v1/projects/project-1/render-tasks/{submitted['task_id']}/cancel",
        headers=harness.headers(),
        json={"expected_task_revision": submitted["task_revision"], "reason": "导演停止当前合成"},
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == RenderStatus.CANCELLING
    assert cancelled.json()["task_revision"] == submitted["task_revision"] + 1
    assert harness.renderer.cancellations == ["renderer-job-1"]


def test_late_success_callback_cannot_resurrect_cancelling_task() -> None:
    harness = EditingHttpHarness()
    running = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="late-callback"),
        json=harness.render_payload(),
    ).json()
    cancelling = harness.client.post(
        f"/api/v1/projects/project-1/render-tasks/{running['task_id']}/cancel",
        headers=harness.headers(),
        json={"expected_task_revision": running["task_revision"], "reason": "停止渲染"},
    ).json()

    callback = harness.client.post(
        f"/api/v1/render-tasks/{running['task_id']}/callbacks",
        headers=harness.headers(permissions=""),
        json={
            "expected_task_revision": cancelling["task_revision"],
            "event_id": "callback-late-success",
            "renderer_job_id": running["renderer_job_id"],
            "attempt": running["attempt"],
            "outcome": "succeeded",
            "occurred_at": (NOW + timedelta(seconds=2)).isoformat(),
            "output": {
                "object_key": OUTPUT_KEY,
                "content_sha256": "c" * 64,
                "rendered_input_snapshot_sha256": running["preview"]["input_snapshot_sha256"],
                "rendered_composition_sha256": running["preview"]["composition_sha256"],
            },
        },
    )

    assert callback.status_code == 200
    assert callback.json()["disposition"] == RenderCallbackDisposition.IGNORED_LATE
    assert callback.json()["task"]["status"] == RenderStatus.CANCELLING
    assert harness.storage.lookups == []
    assert harness.repository.versions_by_dedupe == {}


def test_success_callback_without_object_storage_evidence_is_failed() -> None:
    harness = EditingHttpHarness()
    running = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="missing-object"),
        json=harness.render_payload(),
    ).json()

    callback = harness.client.post(
        f"/api/v1/render-tasks/{running['task_id']}/callbacks",
        headers=harness.headers(permissions=""),
        json={
            "expected_task_revision": running["task_revision"],
            "event_id": "callback-unverified-output",
            "renderer_job_id": running["renderer_job_id"],
            "attempt": running["attempt"],
            "outcome": "succeeded",
            "occurred_at": (NOW + timedelta(seconds=2)).isoformat(),
            "output": {
                "object_key": OUTPUT_KEY,
                "content_sha256": "c" * 64,
                "rendered_input_snapshot_sha256": running["preview"]["input_snapshot_sha256"],
                "rendered_composition_sha256": running["preview"]["composition_sha256"],
            },
        },
    )

    assert callback.status_code == 200
    result = callback.json()
    assert result["disposition"] == RenderCallbackDisposition.APPLIED
    assert result["task"]["status"] == RenderStatus.FAILED
    assert result["task"]["failure"]["code"] == "RENDER_OBJECT_UNVERIFIED"
    assert result["version"] is None
    assert harness.storage.lookups == [("tenant-1", "workspace-1", OUTPUT_KEY)]
    assert harness.repository.versions_by_dedupe == {}


def test_retry_failed_render_resubmits_once_with_idempotency() -> None:
    harness = EditingHttpHarness()
    running = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="retryable-render"),
        json=harness.render_payload(),
    ).json()
    failed = harness.client.post(
        f"/api/v1/render-tasks/{running['task_id']}/callbacks",
        headers=harness.headers(permissions=""),
        json={
            "expected_task_revision": running["task_revision"],
            "event_id": "callback-provider-busy",
            "renderer_job_id": running["renderer_job_id"],
            "attempt": running["attempt"],
            "outcome": "failed",
            "occurred_at": (NOW + timedelta(seconds=2)).isoformat(),
            "failure": {
                "code": "PROVIDER_BUSY",
                "message": "供应商繁忙",
                "retryable": True,
            },
        },
    ).json()["task"]

    retry_headers = harness.headers(idempotency_key="retry-provider-busy")
    retried = harness.client.post(
        f"/api/v1/projects/project-1/render-tasks/{running['task_id']}/retry",
        headers=retry_headers,
        json={"expected_task_revision": failed["task_revision"]},
    )
    replay = harness.client.post(
        f"/api/v1/projects/project-1/render-tasks/{running['task_id']}/retry",
        headers=retry_headers,
        json={"expected_task_revision": failed["task_revision"]},
    )

    assert retried.status_code == replay.status_code == 200
    assert replay.json() == retried.json()
    assert retried.json()["status"] == RenderStatus.RUNNING
    assert retried.json()["attempt"] == 2
    assert retried.json()["renderer_job_id"] == "renderer-job-2"
    assert len(harness.renderer.submissions) == 2


def test_verified_success_callback_creates_one_final_version() -> None:
    harness = EditingHttpHarness()
    harness.storage.objects[OUTPUT_KEY] = StoredRenderObject(
        object_key=OUTPUT_KEY,
        content_sha256="c" * 64,
        size_bytes=8_192,
        duration_ms=4_000,
    )
    running = harness.client.post(
        "/api/v1/projects/project-1/render-tasks",
        headers=harness.headers(idempotency_key="verified-output"),
        json=harness.render_payload(),
    ).json()
    callback_payload = {
        "expected_task_revision": running["task_revision"],
        "event_id": "callback-verified-success",
        "renderer_job_id": running["renderer_job_id"],
        "attempt": running["attempt"],
        "outcome": "succeeded",
        "occurred_at": (NOW + timedelta(seconds=2)).isoformat(),
        "output": {
            "object_key": OUTPUT_KEY,
            "content_sha256": "c" * 64,
            "rendered_input_snapshot_sha256": running["preview"]["input_snapshot_sha256"],
            "rendered_composition_sha256": running["preview"]["composition_sha256"],
        },
    }

    completed = harness.client.post(
        f"/api/v1/render-tasks/{running['task_id']}/callbacks",
        headers=harness.headers(permissions=""),
        json=callback_payload,
    )
    callback_payload["expected_task_revision"] = completed.json()["task"]["task_revision"]
    duplicate = harness.client.post(
        f"/api/v1/render-tasks/{running['task_id']}/callbacks",
        headers=harness.headers(permissions=""),
        json=callback_payload,
    )

    assert completed.status_code == duplicate.status_code == 200
    assert completed.json()["task"]["status"] == RenderStatus.SUCCEEDED
    assert completed.json()["version"]["output"]["object_key"] == OUTPUT_KEY
    assert duplicate.json()["disposition"] == RenderCallbackDisposition.DUPLICATE
    assert len(harness.repository.versions_by_dedupe) == 1
