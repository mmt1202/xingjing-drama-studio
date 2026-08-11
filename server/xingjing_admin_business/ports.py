from __future__ import annotations

from typing import Protocol

from .domain import AdminContext, AuditEntry, BusinessObject, BusinessQuery, Dashboard, Page, StatusChangeCommand


class BusinessQueryPort(Protocol):
    def query(self, query: BusinessQuery, context: AdminContext) -> Page: ...

    def dashboard(self, context: AdminContext) -> Dashboard: ...


class BusinessCommandPort(Protocol):
    def change_status(self, command: StatusChangeCommand, context: AdminContext) -> BusinessObject: ...


class AuditQueryPort(Protocol):
    def audit_entries(self) -> tuple[AuditEntry, ...]: ...
