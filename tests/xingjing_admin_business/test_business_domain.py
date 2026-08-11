from datetime import UTC, datetime

import pytest

from server.xingjing_admin_business import (
    MANAGE_PERMISSION,
    VIEW_PERMISSION,
    AdminContext,
    ApprovalContext,
    BusinessObject,
    BusinessQuery,
    CrossTenantApprovalRequired,
    InMemoryBusinessStore,
    InvalidTransition,
    PermissionDenied,
    StatusChangeCommand,
    VersionConflict,
)

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def obj(identifier: str, tenant_id: str, *, email: str = "owner@example.com") -> BusinessObject:
    return BusinessObject(
        id=identifier,
        kind="user",
        tenant_id=tenant_id,
        name=f"用户 {identifier}",
        status="active",
        version=1,
        updated_at=NOW,
        attributes={"email": email, "phone": "13800138000", "api_key": "secret"},
    )


def test_query_requires_view_permission_and_never_leaks_data() -> None:
    store = InMemoryBusinessStore([obj("u1", "tenant-a")])
    context = AdminContext(actor_id="staff-1", permissions=frozenset(), tenant_ids=frozenset({"tenant-a"}))

    with pytest.raises(PermissionDenied):
        store.query(BusinessQuery(kind="user"), context)


def test_query_filters_tenant_status_search_and_redacts_sensitive_fields() -> None:
    store = InMemoryBusinessStore([obj("alice", "tenant-a"), obj("bob", "tenant-b"), obj("disabled", "tenant-a")])
    context = AdminContext(
        actor_id="staff-1", permissions=frozenset({VIEW_PERMISSION}), tenant_ids=frozenset({"tenant-a"})
    )

    page = store.query(BusinessQuery(kind="user", statuses=frozenset({"active"}), search="alice"), context)

    assert [item.id for item in page.items] == ["alice"]
    assert page.items[0].attributes == {
        "email": "o***@example.com",
        "phone": "138****8000",
        "api_key": "***",
    }


def test_query_uses_stable_cursor_with_id_tie_breaker() -> None:
    store = InMemoryBusinessStore([obj("u1", "tenant-a"), obj("u3", "tenant-a"), obj("u2", "tenant-a")])
    context = AdminContext(
        actor_id="staff-1", permissions=frozenset({VIEW_PERMISSION}), tenant_ids=frozenset({"tenant-a"})
    )

    first = store.query(BusinessQuery(kind="user", limit=2), context)
    second = store.query(BusinessQuery(kind="user", limit=2, cursor=first.next_cursor), context)

    assert [item.id for item in first.items] == ["u3", "u2"]
    assert [item.id for item in second.items] == ["u1"]
    assert first.total == 3


def test_cross_tenant_query_requires_reason_and_scoped_approval() -> None:
    store = InMemoryBusinessStore([obj("u1", "tenant-b")])
    context = AdminContext(
        actor_id="staff-1",
        permissions=frozenset({VIEW_PERMISSION}),
        tenant_ids=frozenset({"tenant-a"}),
    )
    with pytest.raises(CrossTenantApprovalRequired):
        store.query(BusinessQuery(kind="user", tenant_id="tenant-b", accessed_at=NOW), context)

    approved = AdminContext(
        actor_id="staff-1",
        permissions=frozenset({VIEW_PERMISSION}),
        tenant_ids=frozenset({"tenant-a"}),
        access_reason="support case",
        approval=ApprovalContext(
            "approval-query",
            "security-1",
            NOW,
            datetime(2026, 7, 16, tzinfo=UTC),
            frozenset({"tenant-b"}),
            "query",
        ),
    )
    page = store.query(BusinessQuery(kind="user", tenant_id="tenant-b", accessed_at=NOW), approved)

    assert [item.id for item in page.items] == ["u1"]
    assert store.audit_entries()[0].action == "cross_tenant_query"
    assert store.audit_entries()[0].reason == "support case"


def test_cross_tenant_status_change_requires_reason_and_live_scoped_approval() -> None:
    store = InMemoryBusinessStore([obj("u1", "tenant-b")])
    context = AdminContext(
        actor_id="staff-1",
        permissions=frozenset({MANAGE_PERMISSION}),
        tenant_ids=frozenset({"tenant-a"}),
        request_id="req-1",
    )
    command = StatusChangeCommand("u1", "suspended", 1, "idem-1", "risk", NOW)

    with pytest.raises(CrossTenantApprovalRequired):
        store.change_status(command, context)

    approved = AdminContext(
        actor_id="staff-1",
        permissions=frozenset({MANAGE_PERMISSION}),
        tenant_ids=frozenset({"tenant-a"}),
        request_id="req-1",
        access_reason="fraud investigation",
        approval=ApprovalContext(
            approval_id="approval-1",
            approved_by="security-1",
            approved_at=NOW,
            expires_at=datetime(2026, 7, 16, tzinfo=UTC),
            tenant_ids=frozenset({"tenant-b"}),
            action="change_status",
        ),
    )
    changed = store.change_status(command, approved)

    assert (changed.status, changed.version) == ("suspended", 2)
    assert store.audit_entries()[0].approval_id == "approval-1"


def test_status_change_is_atomic_idempotent_versioned_and_audited() -> None:
    store = InMemoryBusinessStore([obj("u1", "tenant-a")])
    context = AdminContext(
        actor_id="staff-1",
        permissions=frozenset({MANAGE_PERMISSION, VIEW_PERMISSION}),
        tenant_ids=frozenset({"tenant-a"}),
        request_id="req-1",
    )
    command = StatusChangeCommand("u1", "suspended", 1, "idem-1", "confirmed abuse", NOW)

    first = store.change_status(command, context)
    repeated = store.change_status(command, context)

    assert first == repeated
    assert len(store.audit_entries()) == 1
    assert store.audit_entries()[0].before == {"status": "active", "version": 1}
    assert store.audit_entries()[0].after == {"status": "suspended", "version": 2}
    with pytest.raises(VersionConflict):
        store.change_status(StatusChangeCommand("u1", "active", 1, "idem-2", "appeal accepted", NOW), context)


def test_invalid_status_transition_does_not_mutate_and_is_audited_as_rejected() -> None:
    store = InMemoryBusinessStore([obj("u1", "tenant-a")])
    context = AdminContext(
        actor_id="staff-1", permissions=frozenset({MANAGE_PERMISSION}), tenant_ids=frozenset({"tenant-a"})
    )

    with pytest.raises(InvalidTransition):
        store.change_status(StatusChangeCommand("u1", "deleted", 1, "idem-1", "cleanup", NOW), context)

    assert store.audit_entries()[0].result == "invalid_transition"
    page = store.query(
        BusinessQuery(kind="user"),
        AdminContext("reader", frozenset({VIEW_PERMISSION}), frozenset({"tenant-a"})),
    )
    assert page.items[0].status == "active"


def test_dashboard_aggregates_visible_records_instead_of_static_values() -> None:
    records = [
        obj("u1", "tenant-a"),
        BusinessObject("p1", "project", "tenant-a", "项目", "active", 1, NOW),
        BusinessObject("t1", "task", "tenant-a", "任务", "running", 1, NOW),
        BusinessObject("c1", "content", "tenant-a", "成片", "blocked", 1, NOW),
        obj("hidden", "tenant-b"),
    ]
    store = InMemoryBusinessStore(records)
    context = AdminContext(
        actor_id="staff-1", permissions=frozenset({VIEW_PERMISSION}), tenant_ids=frozenset({"tenant-a"})
    )

    dashboard = store.dashboard(context)

    assert (dashboard.total_users, dashboard.active_projects, dashboard.running_tasks) == (1, 1, 1)
    assert dashboard.content_by_status == {"blocked": 1}
