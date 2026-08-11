from __future__ import annotations

import importlib.util

from fastapi import APIRouter


def test_http_package_is_available_for_application_wiring() -> None:
    spec = importlib.util.find_spec("server.xingjing_commercial_http")

    assert spec is not None


def test_http_package_exports_router_and_dependency_factory() -> None:
    import server.xingjing_commercial_http as xingjing_commercial_http

    assert isinstance(getattr(xingjing_commercial_http, "router", None), APIRouter)
    assert callable(getattr(xingjing_commercial_http, "create_commercial_dependencies", None))
    assert callable(getattr(xingjing_commercial_http, "create_commercial_router", None))
