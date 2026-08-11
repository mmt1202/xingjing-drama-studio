"""商单结算的安全默认账务边界。

本模块不连接支付、钱包或银行通道。主线必须显式替换该实现，未配置时
所有资金动作均失败关闭，确保不会把本地状态误标为已冻结或已支付。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select

from server.xingjing_commercial import AccountingReceipt, AccountingRequest
from server.xingjing_generation_persistence.repository import (
    GenerationBillingAccountRow,
    GenerationBillingJournalRow,
)

from .transaction_context import current_commercial_session


class FailClosedAccountingPort:
    """未配置真实账务系统时拒绝所有结算副作用。"""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def freeze_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._reject(request)

    def resume_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._reject(request)

    def pay_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._reject(request)

    def _reject(self, request: AccountingRequest) -> AccountingReceipt:
        return AccountingReceipt(
            operation_id=request.operation_id,
            receipt_id=f"unconfigured:{request.operation_id}",
            succeeded=False,
            occurred_at=self._now(),
            failure_code="ACCOUNTING_PORT_NOT_CONFIGURED",
        )


class SqlAlchemyCommercialAccountingPort:
    """在商单聚合事务内执行守恒的冻结、解冻与付款分录。"""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def freeze_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._apply(request, "freeze")

    def resume_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._apply(request, "resume")

    def pay_settlement(self, request: AccountingRequest) -> AccountingReceipt:
        return self._apply(request, "pay")

    def _apply(self, request: AccountingRequest, action: str) -> AccountingReceipt:
        session = current_commercial_session()
        existing = session.get(GenerationBillingJournalRow, request.operation_id)
        occurred_at = self._now()
        if existing is not None:
            return AccountingReceipt(request.operation_id, existing.event_id, True, existing.occurred_at)
        owner = session.scalar(select(GenerationBillingAccountRow).where(
            GenerationBillingAccountRow.workspace_id == request.owner_workspace_id,
        ).with_for_update())
        if owner is None or owner.currency != request.currency:
            return self._failure(request, occurred_at, "ACCOUNT_NOT_FOUND_OR_CURRENCY_MISMATCH")
        amount = request.amount_minor
        if amount <= 0:
            return self._failure(request, occurred_at, "INVALID_SETTLEMENT_AMOUNT")
        postings: list[dict[str, object]]
        if action == "freeze":
            if owner.available_minor < amount:
                return self._failure(request, occurred_at, "INSUFFICIENT_AVAILABLE_BALANCE")
            owner.available_minor -= amount
            owner.held_minor += amount
            postings = [{"account": "workspace.available", "amount_minor": -amount},
                        {"account": "workspace.held", "amount_minor": amount}]
        elif action == "resume":
            if owner.held_minor < amount:
                return self._failure(request, occurred_at, "INSUFFICIENT_HELD_BALANCE")
            owner.held_minor -= amount
            owner.available_minor += amount
            postings = [{"account": "workspace.held", "amount_minor": -amount},
                        {"account": "workspace.available", "amount_minor": amount}]
        else:
            if owner.available_minor < amount:
                return self._failure(request, occurred_at, "INSUFFICIENT_AVAILABLE_BALANCE")
            payee = session.scalar(select(GenerationBillingAccountRow).where(
                GenerationBillingAccountRow.workspace_id == request.payee_workspace_id,
            ).with_for_update())
            if payee is None:
                payee = GenerationBillingAccountRow(
                    workspace_id=request.payee_workspace_id, currency=request.currency,
                    available_minor=0, held_minor=0, spent_minor=0, version=1, updated_at=occurred_at,
                )
                session.add(payee)
            elif payee.currency != request.currency:
                return self._failure(request, occurred_at, "PAYEE_CURRENCY_MISMATCH")
            owner.available_minor -= amount
            owner.spent_minor += amount
            payee.available_minor += amount
            payee.version += 1
            payee.updated_at = occurred_at
            postings = [{"account": "owner.available", "amount_minor": -amount},
                        {"account": "payee.available", "amount_minor": amount}]
        owner.version += 1
        owner.updated_at = occurred_at
        session.add(GenerationBillingJournalRow(
            event_id=request.operation_id, workspace_id=request.owner_workspace_id,
            project_id=request.order_id, task_id=None, action=f"commercial_{action}",
            currency=request.currency, amount_minor=amount, postings=postings,
            reference=f"commercial:{request.order_id}:{request.settlement_id}:{request.request_id}",
            occurred_at=occurred_at,
        ))
        session.flush()
        return AccountingReceipt(request.operation_id, request.operation_id, True, occurred_at)

    @staticmethod
    def _failure(request: AccountingRequest, occurred_at: datetime, code: str) -> AccountingReceipt:
        return AccountingReceipt(request.operation_id, f"rejected:{request.operation_id}", False, occurred_at, code)
