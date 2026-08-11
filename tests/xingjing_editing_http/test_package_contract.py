from __future__ import annotations

from fastapi import APIRouter


def test_http_package_exports_router_and_dependency_factories() -> None:
    from server import xingjing_editing_http

    assert isinstance(xingjing_editing_http.router, APIRouter)
    assert callable(xingjing_editing_http.create_editing_dependencies)
    assert callable(xingjing_editing_http.create_editing_router)
