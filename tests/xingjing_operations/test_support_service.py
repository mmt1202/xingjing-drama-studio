from datetime import UTC, datetime

import pytest

from server.xingjing_operations import (
    Actor,
    ApprovalRequired,
    InMemorySupportRepository,
    SupportService,
)

NOW = datetime(2026, 7, 15, tzinfo=UTC)
AGENT = Actor("tenant-a", "agent-1", "req-support")


def test_ticket_can_be_replied_assigned_escalated_and_closed():
    service = SupportService(InMemorySupportRepository(), clock=lambda: NOW)
    ticket = service.create_ticket(AGENT, "t-1", "billing", "order-9", "high", "charged twice")
    replied = service.reply(AGENT, "t-2", ticket.id, ticket.version, "investigating")
    assigned = service.assign(AGENT, "t-3", ticket.id, replied.version, "agent-2")
    escalated = service.escalate(AGENT, "t-4", ticket.id, assigned.version, "finance")
    closed = service.close(AGENT, "t-5", ticket.id, escalated.version, "refunded externally")

    assert closed.status == "closed"
    assert closed.assignee_id == "agent-2"
    assert [message.body for message in service.get_ticket(AGENT, ticket.id).messages] == [
        "charged twice",
        "investigating",
    ]


def test_compensation_requires_distinct_approver_before_execution():
    executed = []

    class CompensationPort:
        def execute(self, request):
            executed.append(request)
            return "external-ref-1"

    service = SupportService(InMemorySupportRepository(), compensation_port=CompensationPort(), clock=lambda: NOW)
    ticket = service.create_ticket(AGENT, "t-1", "billing", "order-9", "high", "charged twice")
    request = service.request_compensation(
        AGENT, "c-1", ticket.id, ticket.version, "credits", 50, "verified duplicate charge"
    )

    with pytest.raises(ApprovalRequired):
        service.execute_compensation(AGENT, "c-2", request.id, request.version)

    approved = service.approve_compensation(
        Actor("tenant-a", "supervisor-1", "req-approve"), "c-3", request.id, request.version
    )
    completed = service.execute_compensation(AGENT, "c-4", approved.id, approved.version)
    replay = service.execute_compensation(AGENT, "c-4", approved.id, approved.version)

    assert completed.status == "completed"
    assert replay == completed
    assert len(executed) == 1


def test_stale_compensation_version_never_reaches_external_port():
    executed = []

    class CompensationPort:
        def execute(self, request):
            executed.append(request)
            return "must-not-run"

    service = SupportService(InMemorySupportRepository(), compensation_port=CompensationPort(), clock=lambda: NOW)
    ticket = service.create_ticket(AGENT, "t-1", "billing", "order-9", "high", "charged twice")
    request = service.request_compensation(AGENT, "c-1", ticket.id, ticket.version, "credits", 50, "duplicate")
    approved = service.approve_compensation(
        Actor("tenant-a", "supervisor-1", "req-approve"), "c-2", request.id, request.version
    )

    from server.xingjing_operations import VersionConflict

    with pytest.raises(VersionConflict):
        service.execute_compensation(AGENT, "c-3", approved.id, request.version)
    assert executed == []
