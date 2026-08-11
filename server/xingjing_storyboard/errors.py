from __future__ import annotations


class StoryboardError(RuntimeError):
    """带稳定错误码且不泄露跨租户对象信息的领域异常。"""

    def __init__(self, code: str, *, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(code)


class ContractViolation(StoryboardError, ValueError):
    pass


class PermissionDenied(StoryboardError):
    pass


class StoryboardNotFound(StoryboardError):
    pass


class VersionConflict(StoryboardError):
    pass


class IdempotencyConflict(StoryboardError):
    pass


class InvalidAssetReference(StoryboardError):
    pass


class FrozenStoryboardViolation(StoryboardError):
    pass


class QualityGateFailed(StoryboardError):
    pass


class ImportValidationError(StoryboardError):
    pass
