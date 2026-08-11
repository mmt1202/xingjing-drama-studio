"""M18 operations, support, notification, and runtime configuration contracts."""

# ruff: noqa: F401 -- this module is the package's intentional public facade.

from .errors import (
    ApprovalRequired,
    IdempotencyConflict,
    InvalidTransition,
    NotFound,
    OperationsError,
    SelfApprovalForbidden,
    VersionConflict,
)
from .models import (
    Actor,
    AuditRecord,
    CompensationRequest,
    ConfigurationVersion,
    DeliveryLog,
    DeliveryReceipt,
    Incident,
    MessageTemplate,
    NotificationRule,
    OperationsSnapshot,
    OutboundMessage,
    ProbeReading,
    SourceState,
    SupportTicket,
    TicketMessage,
)
from .ports import CompensationPort, NotificationProvider, ObservabilityProbe
from .repositories import (
    AuditTrail,
    InMemoryConfigurationRepository,
    InMemoryNotificationRepository,
    InMemoryOperationsRepository,
    InMemorySupportRepository,
)
from .runtime import (
    OperationsRuntime,
    create_production_operations_runtime,
    create_unavailable_operations_routers,
)
from .services import NotificationService, OperationsConfigurationService, OperationsService, SupportService

__all__ = [name for name in globals() if not name.startswith("_")]  # pyright: ignore[reportUnsupportedDunderAll]
