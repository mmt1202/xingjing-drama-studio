"""成片剪辑领域的 FastAPI 接线。"""

from .dependencies import (
    AccessContextResolver,
    EditingDependencies,
    ProjectScopeAuthorizer,
    RenderInputValidator,
    create_editing_dependencies,
)
from .router import create_editing_router, create_unavailable_editing_router, router

__all__ = [
    "AccessContextResolver",
    "EditingDependencies",
    "ProjectScopeAuthorizer",
    "RenderInputValidator",
    "create_editing_dependencies",
    "create_editing_router",
    "create_unavailable_editing_router",
    "router",
]
