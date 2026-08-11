"""M10 团队治理 HTTP 契约。"""

from .router import create_team_router, create_unavailable_team_router

__all__ = ["create_team_router", "create_unavailable_team_router"]
