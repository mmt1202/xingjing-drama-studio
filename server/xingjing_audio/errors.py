from __future__ import annotations


class AudioDomainError(ValueError):
    """Base error with a stable machine-readable code for API adapters."""

    code = "AUDIO_DOMAIN_ERROR"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class CueValidationError(AudioDomainError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class ConcurrencyConflict(AudioDomainError):
    code = "VERSION_CONFLICT"

    def __init__(self, expected_version: int, current_version: int) -> None:
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(f"expected version {expected_version}, current version {current_version}")


class BoundaryViolation(AudioDomainError):
    code = "INVALID_REGENERATION_BOUNDARY"


class InvalidTaskTransition(AudioDomainError):
    code = "INVALID_TASK_TRANSITION"


class TaskNotFound(AudioDomainError):
    code = "TASK_NOT_FOUND"


class TrackNotFound(AudioDomainError):
    code = "TRACK_NOT_FOUND"


class CrossScopeReference(AudioDomainError):
    code = "CROSS_SCOPE_REFERENCE"


class MediaObjectNotFound(AudioDomainError):
    code = "MEDIA_OBJECT_NOT_FOUND"
