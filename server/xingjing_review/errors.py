class ReviewError(Exception):
    code = "REVIEW_ERROR"


class PermissionDenied(ReviewError):
    code = "FORBIDDEN"


class LinkUnavailable(ReviewError):
    """统一隐藏不存在、过期、撤销和凭证错误等外链细节。"""

    code = "REVIEW_LINK_UNAVAILABLE"


class VersionConflict(ReviewError):
    code = "VERSION_CONFLICT"


class InvalidTransition(ReviewError):
    code = "INVALID_TRANSITION"


class ValidationError(ReviewError):
    code = "VALIDATION_ERROR"
