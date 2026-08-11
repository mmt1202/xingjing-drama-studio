class TeamDomainError(Exception):
    code = "TEAM_DOMAIN_ERROR"


class Forbidden(TeamDomainError):
    code = "FORBIDDEN"


class InvitationStateError(TeamDomainError):
    code = "INVITATION_STATE_INVALID"


class IdempotencyConflict(TeamDomainError):
    code = "IDEMPOTENCY_CONFLICT"


class SeatLimitReached(TeamDomainError):
    code = "SEAT_LIMIT_REACHED"


class VersionConflict(TeamDomainError):
    code = "VERSION_CONFLICT"


class NotFound(TeamDomainError):
    code = "NOT_FOUND"
