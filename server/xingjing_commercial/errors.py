class CommercialError(Exception):
    code = "COMMERCIAL_ERROR"


class PermissionDenied(CommercialError):
    code = "FORBIDDEN"


class OrderNotFound(CommercialError):
    code = "COMMERCIAL_ORDER_NOT_FOUND"


class ValidationError(CommercialError):
    code = "VALIDATION_ERROR"


class VersionConflict(CommercialError):
    code = "VERSION_CONFLICT"


class IdempotencyConflict(CommercialError):
    code = "IDEMPOTENCY_CONFLICT"


class InvalidTransition(CommercialError):
    code = "INVALID_TRANSITION"


class SettlementBlocked(CommercialError):
    code = "SETTLEMENT_BLOCKED_BY_DISPUTE"


class AccountingRejected(CommercialError):
    code = "ACCOUNTING_REJECTED"
