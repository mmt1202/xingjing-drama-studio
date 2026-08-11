from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from server.xingjing_commercial import AccountingRequest
from server.xingjing_commercial.models import AccountingAction
from server.xingjing_commercial_persistence import FailClosedAccountingPort


@pytest.mark.parametrize(
    ("method_name", "action"),
    (
        ("freeze_settlement", "freeze"),
        ("resume_settlement", "resume"),
        ("pay_settlement", "pay"),
    ),
)
def test_unconfigured_accounting_port_fails_closed(method_name: str, action: str) -> None:
    request_time = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    request = AccountingRequest(
        action=cast(AccountingAction, action),
        operation_id=f"operation-{action}",
        owner_workspace_id="workspace-owner",
        payee_workspace_id="workspace-contractor",
        order_id="order-1",
        milestone_id="milestone-1",
        settlement_id="settlement-1",
        amount_minor=10_000,
        currency="CNY",
        requested_by="owner-1",
        request_id="request-1",
    )

    receipt = getattr(FailClosedAccountingPort(now=lambda: request_time), method_name)(request)

    assert receipt.operation_id == f"operation-{action}"
    assert receipt.receipt_id == f"unconfigured:operation-{action}"
    assert receipt.succeeded is False
    assert receipt.occurred_at == request_time
    assert receipt.failure_code == "ACCOUNTING_PORT_NOT_CONFIGURED"
