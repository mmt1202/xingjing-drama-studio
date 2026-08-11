from __future__ import annotations

from datetime import UTC, datetime

import httpx

from lib.httpx_shared import get_http_client
from server.xingjing_editing.contracts import TimelineVersion
from server.xingjing_editing.errors import EditingError
from server.xingjing_editing.ports import RendererGateway, RendererSubmission
from server.xingjing_editing.rendering import RenderProfile


class HttpRendererGateway(RendererGateway):
    """Adapter for the separately deployed renderer service.

    Submission is deliberately fail-closed: an unconfigured service, transport
    error, or malformed acceptance response leaves the M08 task unstarted.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._client = client

    def _configured_client(self) -> httpx.AsyncClient:
        if not self._base_url or not self._service_token:
            raise EditingError("RENDERER_UNAVAILABLE", "渲染服务尚未配置", status_code=503)
        return self._client or get_http_client()

    async def submit(
        self,
        *,
        task_id: str,
        attempt: int,
        input_snapshot_sha256: str,
        timeline: TimelineVersion,
        profile: RenderProfile,
        deadline_at: datetime,
    ) -> RendererSubmission:
        client = self._configured_client()
        try:
            response = await client.post(
                f"{self._base_url}/v1/render-jobs",
                headers={"Authorization": f"Bearer {self._service_token}"},
                json={
                    "task_id": task_id,
                    "attempt": attempt,
                    "input_snapshot_sha256": input_snapshot_sha256,
                    "timeline": timeline.model_dump(mode="json"),
                    "profile": profile.model_dump(mode="json"),
                    "deadline_at": deadline_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                },
            )
        except httpx.HTTPError as error:
            raise EditingError("RENDERER_UNAVAILABLE", "渲染服务不可用", status_code=503) from error
        if response.status_code != 202:
            raise EditingError("RENDERER_UNAVAILABLE", "渲染服务未接受任务", status_code=503)
        try:
            payload = response.json()
            renderer_job_id = payload["renderer_job_id"]
            accepted_at = datetime.fromisoformat(payload["accepted_at"].replace("Z", "+00:00"))
            if not isinstance(renderer_job_id, str) or not renderer_job_id.strip() or accepted_at.tzinfo is None:
                raise ValueError("invalid renderer response")
        except (KeyError, TypeError, ValueError) as error:
            raise EditingError("RENDERER_UNAVAILABLE", "渲染服务响应无效", status_code=503) from error
        return RendererSubmission(renderer_job_id=renderer_job_id, accepted_at=accepted_at)

    async def cancel(self, *, renderer_job_id: str) -> None:
        client = self._configured_client()
        try:
            response = await client.delete(
                f"{self._base_url}/v1/render-jobs/{renderer_job_id}",
                headers={"Authorization": f"Bearer {self._service_token}"},
            )
        except httpx.HTTPError as error:
            raise EditingError("RENDERER_UNAVAILABLE", "渲染服务不可用", status_code=503) from error
        if response.status_code not in (200, 202, 204, 404):
            raise EditingError("RENDERER_UNAVAILABLE", "渲染服务无法取消任务", status_code=503)
