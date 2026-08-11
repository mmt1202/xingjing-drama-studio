from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _enabled(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _positive_integer(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name}_INVALID") from error
    if value < 1:
        raise RuntimeError(f"{name}_INVALID")
    return value


@dataclass(slots=True)
class _RateWindow:
    started_at: float
    count: int


class _FixedWindowLimiter:
    def __init__(self, *, window_seconds: int = 60) -> None:
        self._window_seconds = window_seconds
        self._windows: dict[tuple[str, str], _RateWindow] = {}
        self._last_cleanup = 0.0

    def claim(self, subject: str, category: str, limit: int) -> tuple[bool, int, int]:
        now = time.monotonic()
        key = (subject, category)
        window = self._windows.get(key)
        if window is None or now - window.started_at >= self._window_seconds:
            window = _RateWindow(now, 0)
            self._windows[key] = window
        window.count += 1
        retry_after = max(1, int(self._window_seconds - (now - window.started_at)))
        remaining = max(0, limit - window.count)
        if now - self._last_cleanup >= self._window_seconds:
            self._windows = {
                item_key: item
                for item_key, item in self._windows.items()
                if now - item.started_at < self._window_seconds
            }
            self._last_cleanup = now
        return window.count <= limit, remaining, retry_after


class PlatformSecurityMiddleware:
    """Fail-closed request limits and browser security headers for S07.

    HTTPS enforcement is opt-in for local development and mandatory in the
    production compose profile. Forwarded protocol headers are trusted only
    when the deployment explicitly enables proxy-header trust.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int | None = None,
        require_https: bool | None = None,
        trust_proxy_headers: bool | None = None,
        csp: str | None = None,
        exempt_paths: Iterable[str] = ("/health", "/health/live", "/health/ready"),
    ) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes or _positive_integer(
            "XINGJING_MAX_REQUEST_BODY_BYTES", default=16 * 1024 * 1024
        )
        self._require_https = _enabled("XINGJING_REQUIRE_HTTPS") if require_https is None else require_https
        self._trust_proxy_headers = (
            _enabled("XINGJING_TRUST_PROXY_HEADERS") if trust_proxy_headers is None else trust_proxy_headers
        )
        self._csp = csp or os.environ.get(
            "XINGJING_CONTENT_SECURITY_POLICY",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "object-src 'none'; form-action 'self'; img-src 'self' data: blob:; "
            "media-src 'self' blob:; connect-src 'self' ws: wss:; "
            "font-src 'self' https://fonts.gstatic.com data:; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        )
        self._exempt_paths = frozenset(exempt_paths)
        self._limiter = _FixedWindowLimiter()
        self._rate_limits = {
            "authentication": _positive_integer("XINGJING_RATE_LIMIT_AUTH_PER_MINUTE", default=20),
            "upload": _positive_integer("XINGJING_RATE_LIMIT_UPLOAD_PER_MINUTE", default=60),
            "task": _positive_integer("XINGJING_RATE_LIMIT_TASK_PER_MINUTE", default=120),
            "admin": _positive_integer("XINGJING_RATE_LIMIT_ADMIN_PER_MINUTE", default=60),
            "api": _positive_integer("XINGJING_RATE_LIMIT_API_PER_MINUTE", default=600),
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id", "").strip()
        if not request_id:
            request_id = str(uuid4())
            scope["headers"] = [*scope.get("headers", []), (b"x-request-id", request_id.encode("ascii"))]
            headers = Headers(scope=scope)

        content_length = headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                await self._reject(scope, receive, send, 400, "CONTENT_LENGTH_INVALID")
                return
            if declared_size < 0 or declared_size > self._max_body_bytes:
                await self._reject(scope, receive, send, 413, "REQUEST_BODY_TOO_LARGE")
                return

        path = str(scope.get("path", ""))
        if self._require_https and path not in self._exempt_paths and not self._is_https(scope, headers):
            await self._reject(scope, receive, send, 400, "TLS_REQUIRED")
            return
        if path not in self._exempt_paths:
            category = self._rate_category(path, headers)
            allowed, remaining, retry_after = self._limiter.claim(
                self._subject(scope, headers), category, self._rate_limits[category]
            )
            if not allowed:
                await self._reject(
                    scope,
                    receive,
                    send,
                    429,
                    "RATE_LIMITED",
                    headers={"Retry-After": str(retry_after), "X-RateLimit-Remaining": str(remaining)},
                )
                return

        request_messages: list[Message] = []
        consumed = 0
        while True:
            message = await receive()
            request_messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            consumed += len(message.get("body", b""))
            if consumed > self._max_body_bytes:
                await self._reject(scope, _empty_receive, send, 413, "REQUEST_BODY_TOO_LARGE")
                return
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(request_messages):
                message = request_messages[message_index]
                message_index += 1
                return message
            return {"type": "http.disconnect"}

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.setdefault("X-Request-Id", request_id)
                response_headers.setdefault("X-Content-Type-Options", "nosniff")
                response_headers.setdefault("X-Frame-Options", "DENY")
                response_headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                response_headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                response_headers.setdefault("Content-Security-Policy", self._csp)
                response_headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                response_headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                if self._is_https(scope, headers):
                    response_headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self._app(scope, replay_receive, secured_send)

    def _is_https(self, scope: Scope, headers: Headers) -> bool:
        if scope.get("scheme") == "https":
            return True
        return self._trust_proxy_headers and headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() == "https"

    def _subject(self, scope: Scope, headers: Headers) -> str:
        if self._trust_proxy_headers:
            forwarded = headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded[:128]
        client = scope.get("client")
        return str(client[0])[:128] if isinstance(client, tuple) and client else "unknown"

    @staticmethod
    def _rate_category(path: str, headers: Headers) -> str:
        normalized = path.casefold()
        if any(token in normalized for token in ("/auth/", "/password-reset", "/login", "/register")):
            return "authentication"
        if "multipart/form-data" in headers.get("content-type", "").casefold() or any(
            token in normalized for token in ("/upload", "/files")
        ):
            return "upload"
        if normalized.startswith("/api/v1/admin/") and normalized.endswith("/actions"):
            return "admin"
        if any(token in normalized for token in ("generation-tasks", "render-tasks", "media-tasks")):
            return "task"
        return "api"

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        code: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        request_id = Headers(scope=scope).get("x-request-id", "unknown")
        response_headers = {
            "X-Request-Id": request_id,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": self._csp,
            **(headers or {}),
        }
        response = JSONResponse(
            {"error": {"code": code, "message": code, "retryable": False, "details": {}}, "meta": {"requestId": request_id}},
            status_code=status_code,
            headers=response_headers,
        )
        await response(scope, receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


__all__ = ["PlatformSecurityMiddleware"]
