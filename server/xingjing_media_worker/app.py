"""Independent, fail-closed FFmpeg worker for S05 media operations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

import httpx
from fastapi import FastAPI, HTTPException, Request

ALLOWED_OPERATIONS = frozenset({"probe", "transcode", "thumbnail", "concat", "burn_subtitles", "mix_audio"})


class MediaWorker:
    def __init__(
        self,
        *,
        input_root: Path,
        output_root: Path,
        platform_url: str,
        worker_token: str,
        api_token: str,
        timeout_seconds: int = 3600,
        max_output_bytes: int = 20 * 1024 * 1024 * 1024,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.input_root = input_root.resolve()
        self.output_root = output_root.resolve()
        self.platform_url = platform_url.rstrip("/")
        self.worker_token = worker_token
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.client = client
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    def configured(self) -> bool:
        return bool(
            self.platform_url
            and self.worker_token
            and self.api_token
            and shutil.which("ffmpeg")
            and shutil.which("ffprobe")
        )

    def authenticate(self, request: Request) -> None:
        supplied = request.headers.get("X-Media-Worker-Token", "")
        if not self.api_token or not hmac.compare_digest(supplied, self.api_token):
            raise HTTPException(401, detail={"code": "MEDIA_WORKER_UNAUTHENTICATED"})

    async def execute(self, body: Mapping[str, object]) -> dict[str, object]:
        task_id = _text(body.get("taskId"), "TASK_ID_REQUIRED")
        tenant_id = _text(body.get("tenantId"), "TENANT_ID_REQUIRED")
        workspace_id = _text(body.get("workspaceId"), "WORKSPACE_ID_REQUIRED")
        operation = _text(body.get("operation"), "MEDIA_OPERATION_REQUIRED")
        if operation not in ALLOWED_OPERATIONS:
            raise HTTPException(400, detail={"code": "MEDIA_OPERATION_NOT_ALLOWED"})
        raw_inputs = body.get("inputs", [])
        if not isinstance(raw_inputs, list) or not all(isinstance(item, str) for item in raw_inputs):
            raise HTTPException(400, detail={"code": "MEDIA_INPUTS_INVALID"})
        inputs = tuple(self._input_path(cast(str, item)) for item in raw_inputs)
        output_value = body.get("output")
        output = self._output_path(output_value) if isinstance(output_value, str) and output_value else None
        raw_options = body.get("options", {})
        if not isinstance(raw_options, Mapping):
            raise HTTPException(400, detail={"code": "MEDIA_OPTIONS_INVALID"})
        options = cast(Mapping[str, object], raw_options)
        await self._callback(task_id, tenant_id, workspace_id, f"media:started:{task_id}", "progress", progress=5)
        try:
            if operation == "probe":
                if len(inputs) != 1:
                    raise HTTPException(400, detail={"code": "MEDIA_INPUT_COUNT_INVALID"})
                evidence = await self.probe(inputs[0])
                result = {"operation": operation, "media": evidence}
            else:
                if output is None:
                    raise HTTPException(400, detail={"code": "MEDIA_OUTPUT_REQUIRED"})
                command, cleanup = self._command(operation, inputs, output, options)
                try:
                    await self._run(task_id, command)
                finally:
                    for path in cleanup:
                        path.unlink(missing_ok=True)
                if not output.is_file() or output.stat().st_size <= 0:
                    raise RuntimeError("MEDIA_OUTPUT_MISSING")
                if output.stat().st_size > self.max_output_bytes:
                    output.unlink(missing_ok=True)
                    raise RuntimeError("MEDIA_OUTPUT_TOO_LARGE")
                evidence = await self.probe(output)
                result = {
                    "operation": operation,
                    "objectKey": output.relative_to(self.output_root).as_posix(),
                    "sha256": await asyncio.to_thread(_sha256, output),
                    "sizeBytes": output.stat().st_size,
                    "media": evidence,
                }
        except asyncio.CancelledError:
            await self._callback(task_id, tenant_id, workspace_id, f"media:cancelled:{task_id}", "cancelled")
            raise
        except Exception as error:
            await self._callback(
                task_id,
                tenant_id,
                workspace_id,
                f"media:failed:{task_id}",
                "failed",
                errorCode=_error_code(error),
                errorMessage=str(error)[:500],
            )
            raise
        await self._callback(task_id, tenant_id, workspace_id, f"media:succeeded:{task_id}", "succeeded", result=result)
        return result

    async def cancel(self, task_id: str) -> bool:
        async with self._lock:
            process = self._processes.get(task_id)
        if process is None or process.returncode is not None:
            return False
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            process.kill()
            await process.wait()
        return True

    async def probe(self, path: Path) -> dict[str, object]:
        command = (
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            "-protocol_whitelist",
            "file",
            str(path),
        )
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("MEDIA_PROBE_TIMEOUT") from None
        if process.returncode != 0:
            raise RuntimeError(f"MEDIA_CORRUPT:{stderr.decode(errors='replace')[:200]}")
        try:
            value = json.loads(stdout)
        except ValueError as error:
            raise RuntimeError("MEDIA_PROBE_INVALID") from error
        if not isinstance(value, Mapping):
            raise RuntimeError("MEDIA_PROBE_INVALID")
        streams_value = value.get("streams")
        streams = streams_value if isinstance(streams_value, list) else []
        video = next(
            (item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "video"), None
        )
        audio = next(
            (item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"), None
        )
        format_value = value.get("format")
        format_data = cast(Mapping[str, object], format_value) if isinstance(format_value, Mapping) else {}
        return {
            "mime": _mime(path.suffix),
            "format": str(format_data.get("format_name", "")),
            "durationMs": _milliseconds(format_data.get("duration")),
            "width": _number(video, "width"),
            "height": _number(video, "height"),
            "frameRate": _frame_rate(video),
            "hasVideo": video is not None,
            "hasAudio": audio is not None,
        }

    def _command(
        self, operation: str, inputs: Sequence[Path], output: Path, options: Mapping[str, object]
    ) -> tuple[list[str], list[Path]]:
        output.parent.mkdir(parents=True, exist_ok=True)
        common = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-threads",
            str(_bounded_int(options.get("threads"), 1, 8, 2)),
        ]
        if operation == "transcode" and len(inputs) == 1:
            return common + [
                "-i",
                str(inputs[0]),
                "-map_metadata",
                "-1",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(_bounded_int(options.get("crf"), 16, 35, 23)),
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output),
            ], []
        if operation == "thumbnail" and len(inputs) == 1:
            return common + [
                "-ss",
                str(_bounded_float(options.get("atSeconds"), 0, 86400, 0)),
                "-i",
                str(inputs[0]),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(1280,iw)':-2",
                str(output),
            ], []
        if operation == "burn_subtitles" and len(inputs) == 2:
            if inputs[1].suffix.lower() not in {".srt", ".ass", ".vtt"}:
                raise HTTPException(400, detail={"code": "SUBTITLE_FORMAT_NOT_ALLOWED"})
            escaped = str(inputs[1]).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            return common + [
                "-i",
                str(inputs[0]),
                "-vf",
                f"subtitles='{escaped}'",
                "-c:v",
                "libx264",
                "-c:a",
                "copy",
                str(output),
            ], []
        if operation == "mix_audio" and len(inputs) >= 2:
            command = common.copy()
            for path in inputs:
                command += ["-i", str(path)]
            command += [
                "-filter_complex",
                f"amix=inputs={len(inputs)}:duration=longest:normalize=0",
                "-c:a",
                "aac",
                str(output),
            ]
            return command, []
        if operation == "concat" and inputs:
            with NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False, dir=output.parent) as handle:
                for path in inputs:
                    handle.write(f"file '{str(path).replace("'", "'\\''")}'\n")
                manifest = Path(handle.name)
            return common + ["-f", "concat", "-safe", "1", "-i", str(manifest), "-c", "copy", str(output)], [manifest]
        raise HTTPException(400, detail={"code": "MEDIA_OPERATION_INPUTS_INVALID"})

    async def _run(self, task_id: str, command: Sequence[str]) -> None:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        async with self._lock:
            if task_id in self._processes:
                process.kill()
                await process.wait()
                raise RuntimeError("MEDIA_TASK_ALREADY_RUNNING")
            self._processes[task_id] = process
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("MEDIA_PROCESS_TIMEOUT") from None
        finally:
            async with self._lock:
                self._processes.pop(task_id, None)
        if process.returncode != 0:
            raise RuntimeError(f"MEDIA_PROCESS_FAILED:{stderr.decode(errors='replace')[:300]}")

    def _input_path(self, value: str) -> Path:
        path = (self.input_root / value).resolve()
        try:
            path.relative_to(self.input_root)
        except ValueError as error:
            raise HTTPException(400, detail={"code": "MEDIA_INPUT_OUTSIDE_ROOT"}) from error
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, detail={"code": "MEDIA_INPUT_NOT_FOUND"})
        return path

    def _output_path(self, value: str) -> Path:
        path = (self.output_root / value).resolve()
        try:
            path.relative_to(self.output_root)
        except ValueError as error:
            raise HTTPException(400, detail={"code": "MEDIA_OUTPUT_OUTSIDE_ROOT"}) from error
        if path.exists():
            raise HTTPException(409, detail={"code": "MEDIA_OUTPUT_ALREADY_EXISTS"})
        return path

    async def _callback(
        self, task_id: str, tenant_id: str, workspace_id: str, callback_id: str, outcome: str, **payload: object
    ) -> None:
        active = self.client or httpx.AsyncClient(timeout=30)
        owns = self.client is None
        try:
            response = await active.post(
                f"{self.platform_url}/api/v1/internal/platform/tasks/{task_id}/result",
                headers={"X-Xingjing-Worker-Token": self.worker_token, "X-Request-Id": callback_id},
                json={
                    "tenantId": tenant_id,
                    "workspaceId": workspace_id,
                    "callbackId": callback_id,
                    "outcome": outcome,
                    **payload,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise RuntimeError("PLATFORM_TASK_CALLBACK_FAILED") from error
        finally:
            if owns:
                await active.aclose()


def create_media_worker_app(worker: MediaWorker | None = None) -> FastAPI:
    runtime = worker or _from_environment()
    application = FastAPI(title="Xingjing Media Worker", version="1.0.0")

    @application.get("/health/live")
    async def live():
        return {"status": "UP"}

    @application.get("/health/ready")
    async def ready():
        if not runtime.configured():
            raise HTTPException(503, detail={"code": "MEDIA_WORKER_NOT_CONFIGURED"})
        return {"status": "UP"}

    @application.post("/v1/jobs")
    async def execute(request: Request):
        runtime.authenticate(request)
        body = await _body(request)
        return {"data": await runtime.execute(body)}

    @application.post("/v1/jobs/{task_id}/cancel")
    async def cancel(request: Request, task_id: str):
        runtime.authenticate(request)
        return {"data": {"taskId": task_id, "cancelled": await runtime.cancel(task_id)}}

    return application


def _from_environment() -> MediaWorker:
    return MediaWorker(
        input_root=Path(os.environ.get("MEDIA_INPUT_ROOT", "/data/input")),
        output_root=Path(os.environ.get("MEDIA_OUTPUT_ROOT", "/data/output")),
        platform_url=os.environ.get("XINGJING_PLATFORM_URL", ""),
        worker_token=os.environ.get("XINGJING_TASK_WORKER_TOKEN", ""),
        api_token=os.environ.get("MEDIA_WORKER_TOKEN", ""),
        timeout_seconds=_env_int("MEDIA_TASK_TIMEOUT_SECONDS", 3600),
        max_output_bytes=_env_int("MEDIA_MAX_OUTPUT_BYTES", 20 * 1024 * 1024 * 1024),
    )


async def _body(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise HTTPException(400, detail={"code": "INVALID_JSON"}) from error
    if not isinstance(value, Mapping):
        raise HTTPException(400, detail={"code": "INVALID_REQUEST_BODY"})
    return cast(Mapping[str, object], value)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, detail={"code": code})
    return value.strip()


def _bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    candidate = value if isinstance(value, int) and not isinstance(value, bool) else default
    return min(max(candidate, minimum), maximum)


def _bounded_float(value: object, minimum: float, maximum: float, default: float) -> float:
    candidate = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    return min(max(candidate, minimum), maximum)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value) if value.isdigit() and int(value) > 0 else default


def _number(value: object, key: str) -> int | None:
    if isinstance(value, Mapping):
        result = value.get(key)
        return result if isinstance(result, int) and not isinstance(result, bool) else None
    return None


def _milliseconds(value: object) -> int | None:
    try:
        return round(float(str(value)) * 1000)
    except ValueError:
        return None


def _frame_rate(value: object) -> float | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("avg_frame_rate")
    if not isinstance(raw, str) or "/" not in raw:
        return None
    numerator, denominator = raw.split("/", 1)
    try:
        return round(float(numerator) / float(denominator), 3) if float(denominator) else None
    except ValueError:
        return None


def _mime(suffix: str) -> str:
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(suffix.lower(), "application/octet-stream")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _error_code(error: Exception) -> str:
    if isinstance(error, HTTPException):
        detail = error.detail
        if isinstance(detail, Mapping):
            code = cast(Mapping[str, object], detail).get("code")
            if isinstance(code, str):
                return code
    text = str(error).split(":", 1)[0]
    return text if text.startswith("MEDIA_") else "MEDIA_WORKER_FAILED"


app = create_media_worker_app()
