"""HTTP contract for the M04 asset domain."""

from .router import create_asset_router, create_unavailable_asset_router

__all__ = ["create_asset_router", "create_unavailable_asset_router"]
