"""持久化合规证据驱动的正式导出运行时。"""

from typing import TYPE_CHECKING

from .errors import (
    AuthorityDataMissing,
    CompliancePermissionDenied,
    ComplianceProjectScopeDenied,
    ComplianceRuntimeUnavailable,
    RuntimeConfigurationError,
)
from .runtime import ComplianceRuntime
from .store import SqliteComplianceAuthorityStore

if TYPE_CHECKING:
    from .production import (
        ComplianceRequestContext,
        ProductionComplianceRuntime,
        create_production_compliance_runtime,
    )

__all__ = [
    "AuthorityDataMissing",
    "CompliancePermissionDenied",
    "ComplianceProjectScopeDenied",
    "ComplianceRequestContext",
    "ComplianceRuntime",
    "ComplianceRuntimeUnavailable",
    "ProductionComplianceRuntime",
    "RuntimeConfigurationError",
    "SqliteComplianceAuthorityStore",
    "create_production_compliance_runtime",
]


def __getattr__(name: str):
    """Avoid loading production composition while persistence imports contracts.

    The Alembic metadata imports ``contracts`` from this package.  Loading the
    production factory at package-import time would re-import persistence and
    leave it partially initialized.  Production symbols remain public, but are
    resolved only when an application actually asks for them.
    """
    if name in {"ComplianceRequestContext", "ProductionComplianceRuntime", "create_production_compliance_runtime"}:
        from .production import (
            ComplianceRequestContext,
            ProductionComplianceRuntime,
            create_production_compliance_runtime,
        )

        return {
            "ComplianceRequestContext": ComplianceRequestContext,
            "ProductionComplianceRuntime": ProductionComplianceRuntime,
            "create_production_compliance_runtime": create_production_compliance_runtime,
        }[name]
    raise AttributeError(name)
