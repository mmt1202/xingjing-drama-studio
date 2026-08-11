"""FastAPI wiring for the commercial order domain."""

from .dependencies import (
    CommercialHttpDependencies,
    CommercialOrderIndex,
    CommercialOrderRef,
    create_commercial_dependencies,
)
from .router import create_commercial_router, router

__all__ = [
    "CommercialHttpDependencies",
    "CommercialOrderIndex",
    "CommercialOrderRef",
    "create_commercial_dependencies",
    "create_commercial_router",
    "router",
]
