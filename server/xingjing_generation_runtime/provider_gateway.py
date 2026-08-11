"""Configured HTTP adapter for a real asynchronous M06 generation provider.

The adapter deliberately has no local-success mode.  A deployment must point
it at a provider gateway that accepts jobs and returns an immutable job id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from server.xingjing_generation.contracts import GenerationRequest
from server.xingjing_generation.ports import ProviderSubmission


class GenerationProviderGatewayError(RuntimeError):
    pass


class HttpGenerationProviderGateway:
    def __init__(
        self,
        *,
        provider_id: str,
        default_model_id: str,
        submit_url: str,
        cancel_url_template: str,
        bearer_token: str,
    ) -> None:
        values = (provider_id, default_model_id, submit_url, cancel_url_template, bearer_token)
        if not all(value.strip() for value in values):
            raise ValueError("GENERATION_PROVIDER_CONFIGURATION_REQUIRED")
        if "{job_id}" not in cancel_url_template:
            raise ValueError("GENERATION_PROVIDER_CANCEL_URL_TEMPLATE_INVALID")
        self.provider_id = provider_id
        self.default_model_id = default_model_id
        self._submit_url = submit_url
        self._cancel_url_template = cancel_url_template
        self._bearer_token = bearer_token

    async def submit(self, request: GenerationRequest, *, task_id: str, attempt: int, deadline: datetime) -> ProviderSubmission:
        if request.requested_provider_id and request.requested_provider_id != self.provider_id:
            raise GenerationProviderGatewayError("GENERATION_PROVIDER_NOT_AVAILABLE")
        model_id = request.requested_model_id or self.default_model_id
        payload: dict[str, Any] = {
            "taskId": task_id,
            "workspaceId": request.workspace_id,
            "projectId": request.project_id,
            "mediaType": request.media_type.value,
            "capability": request.capability,
            "prompt": request.prompt,
            "parameters": request.parameters,
            "inputAssetIds": list(request.input_asset_ids),
            "providerId": self.provider_id,
            "modelId": model_id,
            "attempt": attempt,
            "deadlineAt": deadline.isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                response = await client.post(
                    self._submit_url,
                    headers={"Authorization": f"Bearer {self._bearer_token}", "Accept": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise GenerationProviderGatewayError("GENERATION_PROVIDER_SUBMISSION_FAILED") from error
        if not isinstance(body, dict):
            raise GenerationProviderGatewayError("GENERATION_PROVIDER_INVALID_RESPONSE")
        job_id = body.get("jobId", body.get("job_id"))
        accepted_at = body.get("acceptedAt", body.get("accepted_at"))
        if not isinstance(job_id, str) or not job_id.strip():
            raise GenerationProviderGatewayError("GENERATION_PROVIDER_JOB_ID_MISSING")
        if isinstance(accepted_at, str):
            try:
                accepted = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
            except ValueError:
                accepted = datetime.now(UTC)
        else:
            accepted = datetime.now(UTC)
        return ProviderSubmission(provider_job_id=job_id, accepted_at=accepted)

    async def cancel(self, provider_job_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
                response = await client.post(
                    self._cancel_url_template.format(job_id=provider_job_id),
                    headers={"Authorization": f"Bearer {self._bearer_token}", "Accept": "application/json"},
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise GenerationProviderGatewayError("GENERATION_PROVIDER_CANCELLATION_FAILED") from error
