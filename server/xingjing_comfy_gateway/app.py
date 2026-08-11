"""Independent ComfyUI gateway with workflow allowlisting and platform callbacks."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import httpx
from fastapi import FastAPI, HTTPException, Request


class GatewayConfigurationError(RuntimeError):
    pass


class WorkflowCatalog:
    def __init__(self, root: Path, manifest: Mapping[str, object]) -> None:
        self._root = root.resolve()
        raw_workflows = manifest.get("workflows")
        if not isinstance(raw_workflows, Mapping) or not raw_workflows:
            raise GatewayConfigurationError("COMFY_WORKFLOW_MANIFEST_INVALID")
        self._workflows = cast(Mapping[str, object], raw_workflows)

    def materialize(self, key: str, parameters: Mapping[str, object]) -> dict[str, object]:
        raw_definition = self._workflows.get(key)
        if not isinstance(raw_definition, Mapping):
            raise HTTPException(404, detail={"code": "WORKFLOW_NOT_ALLOWED"})
        definition = cast(Mapping[str, object], raw_definition)
        filename = _required_text(definition.get("file"), "WORKFLOW_FILE_REQUIRED")
        path = (self._root / filename).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as error:
            raise GatewayConfigurationError("WORKFLOW_PATH_ESCAPES_ROOT") from error
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise GatewayConfigurationError("WORKFLOW_FILE_INVALID") from error
        if not isinstance(workflow, dict):
            raise GatewayConfigurationError("WORKFLOW_GRAPH_INVALID")
        raw_schema = definition.get("parameters", {})
        if not isinstance(raw_schema, Mapping):
            raise GatewayConfigurationError("WORKFLOW_PARAMETER_SCHEMA_INVALID")
        schema = cast(Mapping[str, object], raw_schema)
        unknown = set(parameters) - set(schema)
        if unknown:
            raise HTTPException(400, detail={"code": "UNKNOWN_WORKFLOW_PARAMETER", "parameters": sorted(unknown)})
        materialized = copy.deepcopy(workflow)
        for name, raw_rule in schema.items():
            if not isinstance(raw_rule, Mapping):
                raise GatewayConfigurationError("WORKFLOW_PARAMETER_RULE_INVALID")
            rule = cast(Mapping[str, object], raw_rule)
            required = rule.get("required", False) is True
            value = parameters.get(name, rule.get("default"))
            if value is None and required:
                raise HTTPException(400, detail={"code": "WORKFLOW_PARAMETER_REQUIRED", "parameter": name})
            if value is None:
                continue
            value = _validate_value(name, value, rule)
            node_id = _required_text(rule.get("node"), "WORKFLOW_PARAMETER_NODE_REQUIRED")
            input_name = _required_text(rule.get("input"), "WORKFLOW_PARAMETER_INPUT_REQUIRED")
            node = materialized.get(node_id)
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                raise GatewayConfigurationError("WORKFLOW_PARAMETER_TARGET_INVALID")
            cast(dict[str, object], node["inputs"])[input_name] = value
        return cast(dict[str, object], materialized)


def create_comfy_gateway_app(
    *,
    comfy_url: str | None = None,
    platform_url: str | None = None,
    gateway_token: str | None = None,
    worker_token: str | None = None,
    catalog: WorkflowCatalog | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    comfy = (comfy_url or os.environ.get("COMFYUI_URL", "")).rstrip("/")
    platform = (platform_url or os.environ.get("XINGJING_PLATFORM_URL", "")).rstrip("/")
    inbound_token = gateway_token or os.environ.get("COMFY_GATEWAY_TOKEN", "")
    callback_token = worker_token or os.environ.get("XINGJING_TASK_WORKER_TOKEN", "")
    configured_catalog = catalog or _catalog_from_environment()
    http = client
    application = FastAPI(title="Xingjing ComfyUI Gateway", version="1.0.0")

    def require_configuration() -> None:
        if not all((comfy, platform, inbound_token, callback_token, configured_catalog)):
            raise HTTPException(503, detail={"code": "COMFY_GATEWAY_NOT_CONFIGURED"})

    def authenticate(request: Request) -> None:
        supplied = request.headers.get("X-Comfy-Gateway-Token", "")
        if not inbound_token or not hmac.compare_digest(supplied, inbound_token):
            raise HTTPException(401, detail={"code": "COMFY_GATEWAY_UNAUTHENTICATED"})

    async def request_json(
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: object | None = None,
    ) -> Mapping[str, object]:
        active_client = http or httpx.AsyncClient(timeout=30)
        owns_client = http is None
        try:
            response = await active_client.request(method, url, headers=headers, json=json_body)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, Mapping):
                raise ValueError("non-object response")
            return cast(Mapping[str, object], value)
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(502, detail={"code": "COMFYUI_UNAVAILABLE"}) from error
        finally:
            if owns_client:
                await active_client.aclose()

    async def callback(
        task_id: str, tenant_id: str, workspace_id: str, callback_id: str, outcome: str, **payload: object
    ) -> None:
        await request_json(
            "POST",
            f"{platform}/api/v1/internal/platform/tasks/{task_id}/result",
            headers={"X-Xingjing-Worker-Token": callback_token, "X-Request-Id": callback_id},
            json_body={
                "tenantId": tenant_id,
                "workspaceId": workspace_id,
                "callbackId": callback_id,
                "outcome": outcome,
                **payload,
            },
        )

    @application.get("/health/live")
    async def live():
        return {"status": "UP"}

    @application.get("/health/ready")
    async def ready():
        require_configuration()
        await request_json("GET", f"{comfy}/system_stats")
        return {"status": "UP"}

    @application.post("/v1/jobs", status_code=202)
    async def submit(request: Request):
        require_configuration()
        authenticate(request)
        body = await _body(request)
        task_id = _required_text(body.get("taskId"), "TASK_ID_REQUIRED")
        tenant_id = _required_text(body.get("tenantId"), "TENANT_ID_REQUIRED")
        workspace_id = _required_text(body.get("workspaceId"), "WORKSPACE_ID_REQUIRED")
        workflow_key = _required_text(body.get("workflowKey"), "WORKFLOW_KEY_REQUIRED")
        raw_parameters = body.get("parameters", {})
        if not isinstance(raw_parameters, Mapping):
            raise HTTPException(400, detail={"code": "WORKFLOW_PARAMETERS_INVALID"})
        workflow = cast(WorkflowCatalog, configured_catalog).materialize(
            workflow_key, cast(Mapping[str, object], raw_parameters)
        )
        client_id = hashlib.sha256(f"{tenant_id}:{workspace_id}:{task_id}".encode()).hexdigest()[:32]
        result = await request_json(
            "POST",
            f"{comfy}/prompt",
            json_body={
                "prompt": workflow,
                "client_id": client_id,
                "extra_data": {
                    "xingjing": {
                        "taskId": task_id,
                        "tenantId": tenant_id,
                        "workspaceId": workspace_id,
                        "workflowKey": workflow_key,
                    }
                },
            },
        )
        prompt_id = _required_text(result.get("prompt_id"), "COMFY_PROMPT_ID_MISSING")
        callback_id = f"comfy:accepted:{prompt_id}"
        await callback(task_id, tenant_id, workspace_id, callback_id, "progress", progress=1, providerJobId=prompt_id)
        return {"data": {"taskId": task_id, "promptId": prompt_id, "workflowKey": workflow_key}}

    @application.post("/v1/jobs/{prompt_id}/sync")
    async def sync(request: Request, prompt_id: str):
        require_configuration()
        authenticate(request)
        body = await _body(request)
        task_id = _required_text(body.get("taskId"), "TASK_ID_REQUIRED")
        tenant_id = _required_text(body.get("tenantId"), "TENANT_ID_REQUIRED")
        workspace_id = _required_text(body.get("workspaceId"), "WORKSPACE_ID_REQUIRED")
        result = await request_json("GET", f"{comfy}/history/{prompt_id}")
        raw_job = result.get(prompt_id)
        if not isinstance(raw_job, Mapping):
            return {"data": {"promptId": prompt_id, "status": "running"}}
        job = cast(Mapping[str, object], raw_job)
        status_value = job.get("status")
        status_data = cast(Mapping[str, object], status_value) if isinstance(status_value, Mapping) else {}
        completed = status_data.get("completed") is True
        messages = status_data.get("messages")
        failed = isinstance(messages, list) and any(
            isinstance(item, list) and item and item[0] == "execution_error" for item in messages
        )
        if failed:
            await callback(
                task_id,
                tenant_id,
                workspace_id,
                f"comfy:failed:{prompt_id}",
                "failed",
                errorCode="COMFY_EXECUTION_FAILED",
                errorMessage="ComfyUI workflow execution failed",
            )
            state = "failed"
        elif completed:
            outputs = job.get("outputs")
            safe_outputs = _safe_output_manifest(outputs)
            await callback(
                task_id,
                tenant_id,
                workspace_id,
                f"comfy:succeeded:{prompt_id}",
                "succeeded",
                result={"provider": "comfyui", "promptId": prompt_id, "outputs": safe_outputs},
            )
            state = "succeeded"
        else:
            await callback(task_id, tenant_id, workspace_id, f"comfy:progress:{prompt_id}", "progress", progress=50)
            state = "running"
        return {"data": {"promptId": prompt_id, "status": state}}

    @application.post("/v1/jobs/{prompt_id}/cancel")
    async def cancel(request: Request, prompt_id: str):
        require_configuration()
        authenticate(request)
        body = await _body(request)
        task_id = _required_text(body.get("taskId"), "TASK_ID_REQUIRED")
        tenant_id = _required_text(body.get("tenantId"), "TENANT_ID_REQUIRED")
        workspace_id = _required_text(body.get("workspaceId"), "WORKSPACE_ID_REQUIRED")
        await request_json("POST", f"{comfy}/interrupt", json_body={"prompt_id": prompt_id})
        await callback(task_id, tenant_id, workspace_id, f"comfy:cancelled:{prompt_id}", "cancelled")
        return {"data": {"promptId": prompt_id, "status": "cancelled"}}

    return application


def _catalog_from_environment() -> WorkflowCatalog | None:
    manifest_path = os.environ.get("COMFY_WORKFLOW_MANIFEST", "").strip()
    root = os.environ.get("COMFY_WORKFLOW_ROOT", "").strip()
    if not manifest_path or not root:
        return None
    try:
        value = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GatewayConfigurationError("COMFY_WORKFLOW_MANIFEST_INVALID") from error
    if not isinstance(value, Mapping):
        raise GatewayConfigurationError("COMFY_WORKFLOW_MANIFEST_INVALID")
    return WorkflowCatalog(Path(root), cast(Mapping[str, object], value))


async def _body(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise HTTPException(400, detail={"code": "INVALID_JSON"}) from error
    if not isinstance(value, Mapping):
        raise HTTPException(400, detail={"code": "INVALID_REQUEST_BODY"})
    return cast(Mapping[str, object], value)


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, detail={"code": code})
    return value.strip()


def _validate_value(name: str, value: object, rule: Mapping[str, object]) -> object:
    kind = rule.get("type")
    valid = (
        (kind == "string" and isinstance(value, str))
        or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (kind == "boolean" and isinstance(value, bool))
    )
    if not valid:
        raise HTTPException(400, detail={"code": "WORKFLOW_PARAMETER_TYPE_INVALID", "parameter": name})
    allowed = rule.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        raise HTTPException(400, detail={"code": "WORKFLOW_PARAMETER_NOT_ALLOWED", "parameter": name})
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum, maximum = rule.get("minimum"), rule.get("maximum")
        if (
            isinstance(minimum, (int, float))
            and value < minimum
            or isinstance(maximum, (int, float))
            and value > maximum
        ):
            raise HTTPException(400, detail={"code": "WORKFLOW_PARAMETER_OUT_OF_RANGE", "parameter": name})
    raw_max_length = rule.get("maxLength", 4096)
    max_length = raw_max_length if isinstance(raw_max_length, int) and not isinstance(raw_max_length, bool) else 4096
    if isinstance(value, str) and len(value) > max_length:
        raise HTTPException(400, detail={"code": "WORKFLOW_PARAMETER_TOO_LONG", "parameter": name})
    return value


def _safe_output_manifest(value: object) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    if not isinstance(value, Mapping):
        return results
    for node_id, raw_output in value.items():
        if not isinstance(raw_output, Mapping):
            continue
        for collection in ("images", "audio", "gifs"):
            entries = raw_output.get(collection)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                filename = entry.get("filename")
                if isinstance(filename, str) and filename and Path(filename).name == filename:
                    results.append(
                        {
                            "nodeId": str(node_id),
                            "kind": collection,
                            "filename": filename,
                            "subfolder": str(entry.get("subfolder", "")),
                            "type": str(entry.get("type", "output")),
                        }
                    )
    return results


app = create_comfy_gateway_app()
