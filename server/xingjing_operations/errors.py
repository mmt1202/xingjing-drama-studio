class OperationsError(Exception):
    """Base error exposed by the operations domain."""


class NotFound(OperationsError):
    pass


class VersionConflict(OperationsError):
    pass


class IdempotencyConflict(OperationsError):
    pass


class ApprovalRequired(OperationsError):
    pass


class SelfApprovalForbidden(OperationsError):
    pass


class InvalidTransition(OperationsError):
    pass
