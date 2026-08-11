from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from server.xingjing_editing.rendering import RenderProfile
from server.xingjing_editing_http.renderer import HttpRendererGateway


@pytest.mark.asyncio
async def test_renderer_gateway_submits_a_scoped_idempotent_job_to_configured_service() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            202,
            json={"renderer_job_id": "job-1", "accepted_at": "2026-07-16T08:00:01Z"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://renderer.example")
    gateway = HttpRendererGateway(base_url="https://renderer.example", service_token="renderer-secret", client=client)

    submission = await gateway.submit(
        task_id="render-task-1",
        attempt=2,
        input_snapshot_sha256="a" * 64,
        profile=RenderProfile(
            container="mp4",
            video_codec="h264",
            audio_codec="aac",
            width=1920,
            height=1080,
            frame_rate_milli=25_000,
        ),
        deadline_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
    )

    await client.aclose()

    assert submission.renderer_job_id == "job-1"
    assert submission.accepted_at == datetime(2026, 7, 16, 8, 0, 1, tzinfo=UTC)
    assert seen == {
        "method": "POST",
        "path": "/v1/render-jobs",
        "authorization": "Bearer renderer-secret",
        "body": {
            "task_id": "render-task-1",
            "attempt": 2,
            "input_snapshot_sha256": "a" * 64,
            "profile": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac", "width": 1920, "height": 1080, "frame_rate_milli": 25_000},
            "deadline_at": "2026-07-16T09:00:00Z",
        },
    }
