from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.engine import URL

from server.xingjing_analytics.database import identity
from server.xingjing_identity_context import TrustedWorkspaceContext

from .query import BillingPageQuery, filter_records


class BillingWorkspaceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspacePageQuery(BillingPageQuery):
    def __post_init__(self) -> None:
        if self.source not in (None, "generation", "audio", "editing", "payment", "refund"):
            raise ValueError("BILLING_SOURCE_INVALID")
        values = asdict(self)
        values["source"] = None
        BillingPageQuery(**values)


def require_workspace_database(bound: URL) -> None:
    for variable in (
        "XINGJING_PAYMENT_DATABASE_URL",
        "XINGJING_BILLING_DATABASE_URL",
        "XINGJING_GENERATION_DATABASE_URL",
        "XINGJING_AUDIO_DATABASE_URL",
        "XINGJING_EDITING_DATABASE_URL",
        "XINGJING_FINANCE_DATABASE_URL",
        "XINGJING_TEAM_DATABASE_URL",
    ):
        configured = os.environ.get(variable, "").strip()
        if configured:
            try:
                if identity(bound) != identity(configured):
                    raise ValueError("different financial database")
            except (TypeError, ValueError) as error:
                raise BillingWorkspaceUnavailable("BILLING_WORKSPACE_DATABASE_MISMATCH") from error


def available_delta(postings: object) -> int | None:
    if not isinstance(postings, list):
        return None
    total = 0
    for posting in postings:
        if not isinstance(posting, Mapping):
            return None
        if posting.get("account") not in {"workspace.available", "team.available", "credit.available"}:
            continue
        amount = posting.get("delta_minor", posting.get("amount_minor"))
        if type(amount) is not int:
            return None
        total += amount
    return total


def _amount(value: object) -> int:
    if type(value) is not int:
        raise BillingWorkspaceUnavailable("BILLING_WORKSPACE_AMOUNT_INVALID")
    return value


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise BillingWorkspaceUnavailable("BILLING_WORKSPACE_TIMESTAMP_INVALID")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(UTC)
    except ValueError as error:
        raise BillingWorkspaceUnavailable("BILLING_WORKSPACE_TIMESTAMP_INVALID") from error


def project_workspace(
    context: TrustedWorkspaceContext,
    account: dict[str, object],
    journals: list[dict[str, object]],
    plan: dict[str, object] | None,
    page: BillingPageQuery,
    payment: dict[str, object],
    *,
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    now = now.astimezone(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = [(today - timedelta(days=offset)).date().isoformat() for offset in range(6, -1, -1)]
    account_present = _amount(account["version"]) > 0 and account["updated_at"] is not None
    currency = str(account["currency"]) if account_present else None
    monthly = 0
    daily = {day: {"settledMinor": 0, "settlementCount": 0} for day in days}
    sources = {source: {"settledMinor": 0, "settlementCount": 0} for source in ("generation", "audio", "editing")}
    excluded_currency = 0
    records = [
        dict(
            row,
            source=("payment" if row.get("status") == "payment_grant" else "refund")
            if row.get("source") == "generation"
            and row.get("status")
            in {
                "payment_grant",
                "refund_reserve",
                "refund_release",
                "refund_settle",
            }
            else row.get("source"),
        )
        for row in journals
    ]
    for row in records:
        if row.get("status") != "settle":
            continue
        occurred = _instant(row["occurred_at"])
        if occurred >= now:
            continue
        in_month = month_start <= occurred
        in_week = occurred.date().isoformat() in daily
        if not account_present or row.get("currency") != currency:
            excluded_currency += int(account_present and (in_month or in_week))
            continue
        amount = _amount(row["amount_minor"])
        if in_month:
            monthly += amount
            source = str(row["source"])
            if source not in sources:
                raise BillingWorkspaceUnavailable("BILLING_WORKSPACE_SOURCE_INVALID")
            sources[source]["settledMinor"] += amount
            sources[source]["settlementCount"] += 1
        date = occurred.date().isoformat()
        if in_week:
            daily[date]["settledMinor"] += amount
            daily[date]["settlementCount"] += 1
    records.sort(key=lambda row: (str(row["occurred_at"]), str(row["source"]), str(row["id"])), reverse=True)
    filtered = filter_records(records, page)
    unknown_direction = sum(type(row.get("available_delta_minor")) is not int for row in filtered)
    notes = ["BUDGET_NOT_CONFIGURED"]
    if not account_present:
        notes.append("ACCOUNT_NOT_ESTABLISHED")
    if plan is None:
        notes.append("PLAN_NOT_ESTABLISHED")
    if excluded_currency:
        notes.append("OTHER_CURRENCIES_EXCLUDED")
    if unknown_direction:
        notes.append("LEDGER_DIRECTION_UNKNOWN")
    return {
        "scope": {"tenantId": context.tenant_id, "workspaceId": context.workspace_id, "actorId": context.actor_id},
        "observedAt": now.isoformat(),
        "moneyUnit": {"unit": "minor", "currency": currency},
        "account": None
        if not account_present
        else {
            "availableMinor": str(_amount(account["available_minor"])),
            "heldMinor": str(_amount(account["held_minor"])),
            "spentMinor": str(_amount(account["spent_minor"])),
            "version": account["version"],
            "updatedAt": account["updated_at"],
        },
        "consumption": {
            "month": {
                "from": month_start.isoformat(),
                "to": now.isoformat(),
                "settledMinor": str(monthly) if account_present else None,
            },
            "last7Days": [
                {
                    "date": day,
                    "settledMinor": str(daily[day]["settledMinor"]) if account_present else None,
                    "settlementCount": daily[day]["settlementCount"],
                }
                for day in days
            ],
            "bySource": [
                {
                    "source": source,
                    "settledMinor": str(value["settledMinor"]) if account_present else None,
                    "settlementCount": value["settlementCount"],
                }
                for source, value in sources.items()
            ],
            "budgetMinor": None,
            "estimatedDaysRemaining": None,
        },
        "coverage": {
            "accountPresent": account_present,
            "excludedCurrencyCount": excluded_currency,
            "ledgerDirectionUnknownCount": unknown_direction,
            "notes": notes,
        },
        "plan": None
        if plan is None
        else {
            "planId": plan["plan_id"],
            "status": plan["status"],
            "seatLimit": plan["seat_limit"],
            "features": plan["features"],
            "quotas": plan["quotas"],
            "quotaRemaining": plan["quota_remaining"],
            "version": plan["version"],
            "updatedAt": plan["updated_at"],
        },
        "permissions": {
            "canView": "billing.view" in context.permissions,
            "canManage": "billing.manage" in context.permissions,
        },
        "payment": payment,
    }, filtered


def ledger_item(row: dict[str, object]) -> dict[str, object]:
    delta = row.get("available_delta_minor")
    return {
        "id": row["id"],
        "source": row["source"],
        "action": row["status"],
        "amountMinor": str(_amount(row["amount_minor"])),
        "availableDeltaMinor": str(delta) if type(delta) is int else None,
        "currency": row["currency"],
        "occurredAt": row["occurred_at"],
        "projectId": row.get("project_id") or None,
        "taskId": row.get("task_id") or None,
        "reference": row.get("business_number", ""),
    }
