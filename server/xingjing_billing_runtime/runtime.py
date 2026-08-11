from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from csv import DictWriter
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import isawaitable
from io import StringIO
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_audio_persistence.models import audio_billing_holds, audio_billing_journals
from server.xingjing_billing_persistence import BillingFinanceRepository
from server.xingjing_billing_persistence.models import BillingOrderRow
from server.xingjing_editing_persistence.models import EditingRenderBillingHoldRow, EditingRenderBillingJournalRow
from server.xingjing_generation_persistence.repository import (
    GenerationBillingAccountRow,
    GenerationBillingHoldRow,
    GenerationBillingJournalRow,
)
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)

type ContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]


@dataclass(frozen=True, slots=True)
class BillingPageQuery:
    page_size: int = 25
    page_token: str | None = None
    query: str | None = None

    def __post_init__(self) -> None:
        if self.page_size < 1 or self.page_size > 100:
            raise ValueError("PAGE_SIZE_INVALID")


class BillingRuntimeConfigurationError(RuntimeError):
    pass


class BillingRuntimePermissionDenied(PermissionError):
    pass


class BillingRuntime:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
        context_resolver: ContextResolver,
        payment_callback_secret: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine: AsyncEngine | None = engine
        self._context_resolver = context_resolver
        self._payment_callback_secret = payment_callback_secret
        self._payment_sweeper: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._payment_sweeper is None:
            self._payment_sweeper = asyncio.create_task(self._payment_sweeper_loop())

    async def _payment_sweeper_loop(self) -> None:
        while True:
            try:
                await self.close_expired_payment_orders()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - background loop retries without killing the app
                pass
            await asyncio.sleep(60)

    async def close_expired_payment_orders(self) -> int:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            scopes = list(
                (
                    await session.execute(
                        select(BillingOrderRow.tenant_id, BillingOrderRow.workspace_id)
                        .where(BillingOrderRow.status == "pending", BillingOrderRow.expires_at <= now)
                        .distinct()
                    )
                ).all()
            )
        return sum(
            [
                await BillingFinanceRepository(
                    self._session_factory,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                ).close_expired_payment_orders(now=now)
                for tenant_id, workspace_id in scopes
            ]
        )

    async def apply_payment_callback(
        self, *, raw_body: bytes, signature: str, expected_workspace_id: str | None = None
    ) -> dict[str, object]:
        secret = self._payment_callback_secret
        if not secret:
            raise BillingRuntimeConfigurationError("PAYMENT_CALLBACK_NOT_CONFIGURED")
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        normalized = signature.removeprefix("sha256=").strip().lower()
        if not normalized or not hmac.compare_digest(normalized, expected):
            raise BillingRuntimePermissionDenied("PAYMENT_CALLBACK_SIGNATURE_INVALID")
        try:
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("PAYMENT_CALLBACK_JSON_INVALID") from error
        if not isinstance(body, dict):
            raise ValueError("PAYMENT_CALLBACK_JSON_INVALID")
        tenant_id = _required_string(body, "tenantId")
        workspace_id = _required_string(body, "workspaceId")
        if expected_workspace_id is not None and workspace_id != expected_workspace_id:
            raise BillingRuntimePermissionDenied("WORKSPACE_SCOPE_MISMATCH")
        occurred_at = _required_datetime(body, "occurredAt")
        return await BillingFinanceRepository(
            self._session_factory,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        ).apply_payment_callback(
            order_id=_required_string(body, "orderId"),
            event_id=_required_string(body, "eventId"),
            external_reference=_required_string(body, "externalReference"),
            paid_minor=_required_positive_integer(body, "paidMinor"),
            occurred_at=occurred_at,
        )

    async def create_payment_order(
        self,
        context: TrustedWorkspaceContext,
        *,
        order_id: str,
        amount_minor: int,
        currency: str,
        order_type: str,
        idempotency_key: str,
        expires_at: datetime,
    ) -> dict[str, object]:
        return await self._finance_repository(context).create_payment_order(
            order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            order_type=order_type,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            occurred_at=datetime.now(UTC),
        )

    async def refund_payment_order(
        self,
        context: TrustedWorkspaceContext,
        *,
        order_id: str,
        amount_minor: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        return await self._finance_repository(context).refund_payment_order(
            order_id=order_id,
            amount_minor=amount_minor,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            occurred_at=datetime.now(UTC),
        )

    async def resolve(self, request: Request, workspace_id: str, permission: str) -> TrustedWorkspaceContext:
        context = self._context_resolver(request)
        if isawaitable(context):
            context = await context
        if context.workspace_id != workspace_id:
            raise BillingRuntimePermissionDenied("WORKSPACE_SCOPE_MISMATCH")
        if permission not in context.permissions:
            raise BillingRuntimePermissionDenied(permission)
        return context

    async def billing(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        account, holds, journals = await self._billing_records(context)
        repository = self._finance_repository(context)
        invoices = await repository.invoices()
        orders = await repository.orders()
        plan = await repository.plan()
        financial_documents = sorted(
            [*invoices, *orders],
            key=lambda item: (str(item["occurred_at"]), str(item["id"])),
            reverse=True,
        )
        items, next_token = _page_records(financial_documents, page)
        return {
            "items": items,
            "next_page_token": next_token,
            "account": account,
            "plan": plan,
            "hold_count": len(holds),
            "journal_count": len(journals),
            "summary": [
                {
                    "key": "available",
                    "label": "可用算力",
                    "value": account["available_minor"],
                    "format": "minor",
                    "currency": account["currency"],
                },
                {
                    "key": "held",
                    "label": "冻结算力",
                    "value": account["held_minor"],
                    "format": "minor",
                    "currency": account["currency"],
                },
                {
                    "key": "spent",
                    "label": "累计消耗",
                    "value": account["spent_minor"],
                    "format": "minor",
                    "currency": account["currency"],
                },
            ],
        }

    async def holds(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        account, holds, _ = await self._billing_records(context)
        items, next_token = _page_records(holds, page)
        return {"items": items, "account": account, "next_page_token": next_token}

    async def journals(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        account, _, journals = await self._billing_records(context)
        items, next_token = _page_records(journals, page)
        return {"items": items, "account": account, "next_page_token": next_token}

    async def plan(self, context: TrustedWorkspaceContext) -> dict[str, object]:
        plan = await self._finance_repository(context).plan()
        return {"plan": plan} if plan is not None else {}

    async def costs(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        account, holds, _ = await self._billing_records(context)
        items = _cost_items(holds, str(account["currency"]))
        paged_items, next_token = _page_records(items, page)
        return {
            "items": paged_items,
            "next_page_token": next_token,
            "summary": [
                {
                    "key": "total",
                    "label": "实际成本",
                    "value": sum(_integer(item["actual_minor"]) for item in items),
                    "format": "minor",
                    "currency": account["currency"],
                },
                {
                    "key": "released",
                    "label": "已退回",
                    "value": sum(_integer(item["released_minor"]) for item in items),
                    "format": "minor",
                    "currency": account["currency"],
                },
                {
                    "key": "tasks",
                    "label": "计费任务",
                    "value": sum(_integer(item["task_count"]) for item in items),
                    "format": "integer",
                },
            ],
        }

    async def audit_events(
        self,
        context: TrustedWorkspaceContext,
        page: BillingPageQuery,
        *,
        request_id: str | None,
        actor_id: str | None,
        object_id: str | None,
    ) -> dict[str, object]:
        records = list(
            await self._finance_repository(context).audit_events(
                request_id=request_id,
                actor_id=actor_id,
                object_id=object_id,
            )
        )
        items, next_token = _page_records(records, page)
        return {"items": items, "next_page_token": next_token}

    async def export_csv(
        self,
        context: TrustedWorkspaceContext,
        *,
        dataset: str,
        query: str | None,
    ) -> tuple[str, bytes]:
        account, holds, journals = await self._billing_records(context)
        repository = self._finance_repository(context)
        if dataset == "documents":
            records = [*await repository.invoices(), *await repository.orders()]
        elif dataset == "holds":
            records = holds
        elif dataset == "journals":
            records = journals
        elif dataset == "costs":
            records = _cost_items(holds, str(account["currency"]))
        else:
            raise ValueError("BILLING_EXPORT_DATASET_INVALID")
        BillingPageQuery(page_size=100, query=query)
        # Export must not silently truncate; apply the same server-side filter
        # without the interactive page limit.
        query_text = (query or "").strip().casefold()
        if query_text:
            filtered = [
                record
                for record in records
                if query_text in " ".join(str(value) for value in record.values()).casefold()
            ]
        else:
            filtered = list(records)
        csv_bytes = _csv_bytes(filtered)
        await repository.record_export(
            actor_id=context.actor_id,
            request_id=context.request_id,
            dataset=dataset,
            row_count=len(filtered),
            occurred_at=datetime.now(UTC),
        )
        return f"xingjing-{dataset}-{context.workspace_id}.csv", csv_bytes

    async def request_invoice(
        self,
        context: TrustedWorkspaceContext,
        *,
        invoice_title: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        if amount_minor <= 0 or len(currency) != 3 or not invoice_title.strip():
            raise ValueError("INVALID_INVOICE_REQUEST")
        return await self._finance_repository(context).request_invoice(
            invoice_title=invoice_title.strip(),
            amount_minor=amount_minor,
            currency=currency.upper(),
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            occurred_at=datetime.now(UTC),
        )

    async def _billing_records(
        self, context: TrustedWorkspaceContext
    ) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
        async with self._session_factory() as session:
            account_row = await session.get(GenerationBillingAccountRow, context.workspace_id)
            generation_holds = (
                await session.scalars(
                    select(GenerationBillingHoldRow).where(
                        GenerationBillingHoldRow.workspace_id == context.workspace_id
                    )
                )
            ).all()
            generation_journals = (
                await session.scalars(
                    select(GenerationBillingJournalRow).where(
                        GenerationBillingJournalRow.workspace_id == context.workspace_id
                    )
                )
            ).all()
            audio_holds = (
                (
                    await session.execute(
                        select(audio_billing_holds).where(
                            audio_billing_holds.c.tenant_id == context.tenant_id,
                            audio_billing_holds.c.workspace_id == context.workspace_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            audio_journals = (
                (
                    await session.execute(
                        select(audio_billing_journals).where(
                            audio_billing_journals.c.tenant_id == context.tenant_id,
                            audio_billing_journals.c.workspace_id == context.workspace_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            editing_holds = (
                await session.scalars(
                    select(EditingRenderBillingHoldRow).where(
                        EditingRenderBillingHoldRow.tenant_id == context.tenant_id,
                        EditingRenderBillingHoldRow.workspace_id == context.workspace_id,
                    )
                )
            ).all()
            editing_journals = (
                await session.scalars(
                    select(EditingRenderBillingJournalRow).where(
                        EditingRenderBillingJournalRow.tenant_id == context.tenant_id,
                        EditingRenderBillingJournalRow.workspace_id == context.workspace_id,
                    )
                )
            ).all()
        account = {
            "workspace_id": context.workspace_id,
            "currency": "CNY" if account_row is None else account_row.currency,
            "available_minor": 0 if account_row is None else account_row.available_minor,
            "held_minor": 0 if account_row is None else account_row.held_minor,
            "spent_minor": 0 if account_row is None else account_row.spent_minor,
            "version": 0 if account_row is None else account_row.version,
            "updated_at": None if account_row is None else account_row.updated_at.isoformat(),
        }
        holds = (
            [_hold(row, source="generation") for row in generation_holds]
            + [_mapping_hold(row, source="audio") for row in audio_holds]
            + [_hold(row, source="editing") for row in editing_holds]
        )
        journals = (
            [_journal(row, source="generation") for row in generation_journals]
            + [_mapping_journal(row, source="audio") for row in audio_journals]
            + [_journal(row, source="editing") for row in editing_journals]
        )
        holds.sort(key=lambda item: (str(item["updated_at"]), str(item["id"])), reverse=True)
        journals.sort(key=lambda item: (str(item["occurred_at"]), str(item["id"])), reverse=True)
        return account, holds, journals

    def _finance_repository(self, context: TrustedWorkspaceContext) -> BillingFinanceRepository:
        return BillingFinanceRepository(
            self._session_factory, tenant_id=context.tenant_id, workspace_id=context.workspace_id
        )

    async def close(self) -> None:
        if self._payment_sweeper is not None:
            self._payment_sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self._payment_sweeper
            self._payment_sweeper = None
        if self._engine is not None:
            engine, self._engine = self._engine, None
            await engine.dispose()


def create_production_billing_runtime() -> BillingRuntime:
    url = os.environ.get("XINGJING_BILLING_DATABASE_URL", "").strip()
    if not url:
        raise BillingRuntimeConfigurationError("XINGJING_BILLING_DATABASE_URL_REQUIRED")
    if not url.startswith("postgresql+asyncpg://"):
        raise BillingRuntimeConfigurationError("XINGJING_BILLING_DATABASE_URL_MUST_USE_POSTGRESQL_ASYNCPG")
    engine = create_async_engine(url, pool_pre_ping=True, isolation_level="REPEATABLE READ")
    return BillingRuntime(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        engine=engine,
        context_resolver=TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        payment_callback_secret=os.environ.get("XINGJING_PAYMENT_CALLBACK_SECRET", "").strip() or None,
    )


def _required_string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key.upper()}_REQUIRED")
    return item.strip()


def _required_positive_integer(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise ValueError(f"{key.upper()}_INVALID")
    return item


def _required_datetime(value: dict[str, object], key: str) -> datetime:
    raw = _required_string(value, key)
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{key.upper()}_INVALID") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{key.upper()}_INVALID")
    return result.astimezone(UTC)


def _hold(row: Any, *, source: str) -> dict[str, object]:
    return {
        "id": row.task_id,
        "business_number": row.task_id,
        "project_id": row.project_id,
        "task_id": row.task_id,
        "source": source,
        "amount_minor": row.actual_minor or row.estimated_minor,
        "estimated_minor": row.estimated_minor,
        "actual_minor": row.actual_minor,
        "released_minor": row.released_minor,
        "currency": row.currency,
        "status": row.status,
        "occurred_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _mapping_hold(row: RowMapping, *, source: str) -> dict[str, object]:
    return {
        "id": row["task_id"],
        "business_number": row["task_id"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "source": source,
        "amount_minor": row["actual_minor"] or row["estimated_minor"],
        "estimated_minor": row["estimated_minor"],
        "actual_minor": row["actual_minor"],
        "released_minor": row["released_minor"],
        "currency": row["currency"],
        "status": row["status"],
        "occurred_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _journal(row: Any, *, source: str) -> dict[str, object]:
    return {
        "id": row.event_id,
        "business_number": row.reference,
        "project_id": row.project_id,
        "task_id": row.task_id,
        "source": source,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "status": row.action,
        "occurred_at": getattr(row, "occurred_at", row.created_at).isoformat(),
    }


def _mapping_journal(row: RowMapping, *, source: str) -> dict[str, object]:
    return {
        "id": row["event_id"],
        "business_number": row["reference"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "source": source,
        "amount_minor": row["amount_minor"],
        "currency": row["currency"],
        "status": row["action"],
        "occurred_at": _iso(row.get("occurred_at", row.get("created_at"))),
    }


def _iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise BillingRuntimeConfigurationError("BILLING_TIMESTAMP_INVALID")
    return value.isoformat()


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BillingRuntimeConfigurationError("BILLING_AMOUNT_INVALID")
    return value


def _cost_items(holds: list[dict[str, object]], currency: str) -> list[dict[str, object]]:
    projects: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"estimated_minor": 0, "actual_minor": 0, "released_minor": 0, "task_count": 0}
    )
    for hold in holds:
        project = projects[str(hold["project_id"])]
        project["estimated_minor"] += _integer(hold["estimated_minor"])
        project["actual_minor"] += _integer(hold["actual_minor"])
        project["released_minor"] += _integer(hold["released_minor"])
        project["task_count"] += 1
    return [
        {"id": project_id, "project_id": project_id, "currency": currency, **values}
        for project_id, values in sorted(projects.items())
    ]


def _csv_bytes(records: list[dict[str, object]]) -> bytes:
    if not records:
        return b""
    fields = sorted({key for record in records for key in record})
    stream = StringIO(newline="")
    writer = DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow({key: _csv_value(value) for key, value in record.items()})
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _csv_value(value: object) -> object:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _page_records(
    records: list[dict[str, object]], page: BillingPageQuery
) -> tuple[list[dict[str, object]], str | None]:
    query = (page.query or "").strip().casefold()
    if query:
        records = [
            record for record in records if query in " ".join(str(value) for value in record.values()).casefold()
        ]
    start = 0
    cursor = _decode_cursor(page.page_token)
    if cursor is not None:
        try:
            start = next(index + 1 for index, record in enumerate(records) if _record_cursor(record) == cursor)
        except StopIteration as error:
            raise ValueError("PAGE_TOKEN_INVALID") from error
    selected = records[start : start + page.page_size]
    next_index = start + len(selected)
    next_token = _encode_cursor(_record_cursor(selected[-1])) if selected and next_index < len(records) else None
    return selected, next_token


def _record_cursor(record: dict[str, object]) -> str:
    identity = record.get("id") or record.get("project_id")
    if not isinstance(identity, str) or not identity:
        raise BillingRuntimeConfigurationError("BILLING_CURSOR_ID_MISSING")
    sort_time = record.get("occurred_at") or record.get("updated_at") or ""
    return f"{sort_time}\x1f{identity}"


def _encode_cursor(cursor: str) -> str:
    return urlsafe_b64encode(json.dumps({"cursor": cursor}, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(token: str | None) -> str | None:
    if not token:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        value = json.loads(urlsafe_b64decode(token + padding).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("PAGE_TOKEN_INVALID") from error
    if not isinstance(value, dict) or not isinstance(value.get("cursor"), str) or not value["cursor"]:
        raise ValueError("PAGE_TOKEN_INVALID")
    return value["cursor"]
