from __future__ import annotations


class EditingError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PermissionDenied(EditingError):
    def __init__(self) -> None:
        super().__init__("PERMISSION_DENIED", "当前主体没有成片操作权限", status_code=403)


class TimelineNotFound(EditingError):
    def __init__(self) -> None:
        super().__init__("TIMELINE_NOT_FOUND", "时间线不存在或不可访问", status_code=404)


class RenderTaskNotFound(EditingError):
    def __init__(self) -> None:
        super().__init__("RENDER_TASK_NOT_FOUND", "渲染任务不存在或不可访问", status_code=404)


class InvalidRenderTransition(EditingError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_RENDER_TRANSITION", message, status_code=409)


class TrackNotFound(EditingError):
    def __init__(self) -> None:
        super().__init__("TRACK_NOT_FOUND", "轨道不存在或不可访问", status_code=404)


class ClipNotFound(EditingError):
    def __init__(self) -> None:
        super().__init__("CLIP_NOT_FOUND", "片段不存在或不可访问", status_code=404)


class SourceVersionNotFound(EditingError):
    def __init__(self) -> None:
        super().__init__("SOURCE_VERSION_NOT_FOUND", "媒体输入版本不存在或不可访问", status_code=404)


class CrossScopeReference(EditingError):
    def __init__(self) -> None:
        super().__init__("CROSS_SCOPE_REFERENCE", "媒体输入版本不属于当前项目或未获授权", status_code=403)


class VersionConflict(EditingError):
    def __init__(self, *, expected_version: int, current_version: int) -> None:
        super().__init__(
            "VERSION_CONFLICT",
            f"期望版本 {expected_version}，当前版本 {current_version}",
            status_code=409,
        )
        self.expected_version = expected_version
        self.current_version = current_version


class IdempotencyConflict(EditingError):
    def __init__(self) -> None:
        super().__init__("IDEMPOTENCY_CONFLICT", "幂等键已绑定到不同请求", status_code=409)


class InvalidTimeline(EditingError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=422)


class EditingBillingError(EditingError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(code, message, status_code=status_code)


class InsufficientCredits(EditingBillingError):
    def __init__(self) -> None:
        super().__init__("INSUFFICIENT_CREDITS", "工作区可用额度不足，无法冻结渲染费用", status_code=402)
