from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from csv import DictWriter
from datetime import UTC, datetime
from inspect import isawaitable
from io import StringIO
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_audio_persistence.models import (
    audio_billing_holds,
    audio_billing_journals,
)
from server.xingjing_audio_persistence.models import (
    generation_tasks as audio_generation_tasks,
)
from server.xingjing_billing_persistence import BillingFinanceRepository
from server.xingjing_editing_persistence.models import (
    EditingAuditRow,
    EditingRenderBillingHoldRow,
    EditingRenderBillingJournalRow,
    RenderTaskRow,
    TimelineVersionRow,
)
from server.xingjing_generation_persistence.repository import (
    AuditRow as GenerationAuditRow,
)
from server.xingjing_generation_persistence.repository import (
    GenerationBillingAccountRow,
    GenerationBillingHoldRow,
    GenerationBillingJournalRow,
)
from server.xingjing_generation_persistence.repository import (
    TaskRow as GenerationTaskRow,
)
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_team_persistence.repository import WorkspaceRow

from .billing_workspace import (
    BillingWorkspaceUnavailable,
    available_delta,
    ledger_item,
    project_workspace,
    require_workspace_database,
)
from .query import BillingPageQuery, filter_records

type ContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]

logger = logging.getLogger(__name__)


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
        self._payment_status: Callable[[], dict[str, object]] | None = None

    async def start(self) -> None:
        # The unified payment runtime owns expiry and reconciliation.
        return None

    async def close_expired_payment_orders(self) -> int:
        raise BillingRuntimeConfigurationError("PAYMENT_USE_UNIFIED_GATEWAY")

    async def apply_payment_callback(
        self, *, raw_body: bytes, signature: str, expected_workspace_id: str | None = None
    ) -> dict[str, object]:
        # Only explicitly injected internal adapters retain this compatibility path.
        # Production HTTP callbacks are served by the unified payment runtime.
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
        raise BillingRuntimeConfigurationError("PAYMENT_USE_UNIFIED_GATEWAY")

    async def refund_payment_order(
        self,
        context: TrustedWorkspaceContext,
        *,
        order_id: str,
        amount_minor: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        raise BillingRuntimeConfigurationError("PAYMENT_USE_UNIFIED_GATEWAY")

    async def resolve(self, request: Request, workspace_id: str, permission: str) -> TrustedWorkspaceContext:
        context = self._context_resolver(request)
        if isawaitable(context):
            context = await context
        if context.workspace_id != workspace_id:
            raise BillingRuntimePermissionDenied("WORKSPACE_SCOPE_MISMATCH")
        if permission not in context.permissions:
            raise BillingRuntimePermissionDenied(permission)
        return context

    def set_payment_status(self, callback: Callable[[], dict[str, object]]) -> None:
        self._payment_status = callback

    async def workspace(
        self,
        context: TrustedWorkspaceContext,
        page: BillingPageQuery,
    ) -> dict[str, object]:
        if context.role.casefold() == "api_key" or "billing.view" not in context.permissions:
            raise BillingRuntimePermissionDenied("BILLING_INTERACTIVE_SESSION_REQUIRED")
        payment = (
            self._payment_status()
            if self._payment_status is not None
            else {
                "configured": False,
                "reason": "PAYMENT_PROVIDER_NOT_CONFIGURED",
            }
        )
        async with self._session_factory() as session, session.begin():
            require_workspace_database(session.get_bind().engine.url)
            tenants = list(
                (
                    await session.scalars(
                        select(WorkspaceRow.tenant_id).where(
                            WorkspaceRow.workspace_id == context.workspace_id,
                        )
                    )
                ).all()
            )
            if tenants != [context.tenant_id]:
                raise BillingWorkspaceUnavailable("BILLING_WORKSPACE_SCOPE_UNVERIFIED")
            account, _holds, journals = await self._billing_records(context, session=session)
            plan = await self._finance_repository(context).plan(session=session)
            result, records = project_workspace(
                context,
                account,
                journals,
                plan,
                page,
                payment,
                now=datetime.now(UTC),
            )
            selected, token = _page_records(records, page)
            result["ledger"] = {
                "items": [ledger_item(row) for row in selected],
                "nextToken": token,
                "pageSize": page.page_size,
            }
        return result

    async def billing(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        async with self._session_factory() as session:
            account, holds, journals = await self._billing_records(context, session=session)
            repository = self._finance_repository(context)
            invoices = await repository.invoices(session=session)
            orders = await repository.orders(session=session)
            plan = await repository.plan(session=session)
        financial_documents = sorted(
            [*invoices, *orders],
            key=lambda item: (str(item["occurred_at"]), str(item["id"])),
            reverse=True,
        )
        items, next_token = _page_records(filter_records(financial_documents, page), page)
        return {
            "items": items,
            "next_page_token": next_token,
            "account": account,
            "plan": plan,
            "available_actions": ["requestInvoice"],
            "payment_provider_ready": False,
            "hold_count": len(holds),
            "journal_count": len(journals),
            "summary": _account_summary(account),
        }

    async def holds(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        account, holds, _ = await self._billing_records(context)
        items, next_token = _page_records(filter_records(holds, page), page)
        return {"items": items, "account": account, "summary": _account_summary(account), "next_page_token": next_token}

    async def journals(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        account, _, journals = await self._billing_records(context)
        items, next_token = _page_records(filter_records(journals, page), page)
        return {"items": items, "account": account, "summary": _account_summary(account), "next_page_token": next_token}

    async def plan(self, context: TrustedWorkspaceContext) -> dict[str, object]:
        plan = await self._finance_repository(context).plan()
        return {"plan": plan} if plan is not None else {}

    async def costs(self, context: TrustedWorkspaceContext, page: BillingPageQuery) -> dict[str, object]:
        account, holds, _ = await self._billing_records(context)
        items = _filtered_cost_items(holds, str(account["currency"]), page)
        paged_items, next_token = _page_records(items, page)
        return {
            "items": paged_items,
            "next_page_token": next_token,
            "summary": _cost_summary(items),
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
        items, next_token = _page_records(filter_records(records, page), page)
        return {"items": items, "next_page_token": next_token}

    async def export_csv(
        self,
        context: TrustedWorkspaceContext,
        *,
        dataset: str,
        query: str | None,
        project_id: str | None = None,
        filters: BillingPageQuery | None = None,
    ) -> tuple[str, bytes]:
        page = filters or BillingPageQuery(query=query, project_id=project_id)
        repository = self._finance_repository(context)
        if dataset == "documents":
            async with self._session_factory() as session:
                records = [*await repository.invoices(session=session), *await repository.orders(session=session)]
        elif dataset in {"holds", "journals", "costs"}:
            account, holds, journals = await self._billing_records(context)
            records = holds if dataset == "holds" else journals
            if dataset == "costs":
                records = _filtered_cost_items(holds, str(account["currency"]), page)
        else:
            raise ValueError("BILLING_EXPORT_DATASET_INVALID")
        filtered = records if dataset == "costs" else filter_records(records, page)
        csv_bytes = _csv_bytes(filtered)
        await repository.record_export(
            actor_id=context.actor_id,
            request_id=context.request_id,
            dataset=dataset,
            row_count=len(filtered),
            occurred_at=datetime.now(UTC),
        )
        return f"xingjing-{dataset}.csv", csv_bytes

    async def request_invoice(
        self,
        context: TrustedWorkspaceContext,
        *,
        invoice_title: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        if (
            isinstance(amount_minor, bool)
            or amount_minor <= 0
            or amount_minor > 2**63 - 1
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or not invoice_title.strip()
            or len(invoice_title.strip()) > 512
        ):
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
        self, context: TrustedWorkspaceContext, *, session: AsyncSession | None = None
    ) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
        if session is None:
            async with self._session_factory() as owned_session:
                return await self._billing_records(context, session=owned_session)
        account_row = await session.get(GenerationBillingAccountRow, context.workspace_id)
        generation_holds = (
            await session.scalars(
                select(GenerationBillingHoldRow).where(GenerationBillingHoldRow.workspace_id == context.workspace_id)
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
        generation_tasks = (
            await session.scalars(
                select(GenerationTaskRow).where(GenerationTaskRow.workspace_id == context.workspace_id)
            )
        ).all()
        generation_audits = (
            await session.scalars(
                select(GenerationAuditRow).where(GenerationAuditRow.workspace_id == context.workspace_id)
            )
        ).all()
        audio_tasks = (
            (
                await session.execute(
                    select(audio_generation_tasks).where(
                        audio_generation_tasks.c.tenant_id == context.tenant_id,
                        audio_generation_tasks.c.workspace_id == context.workspace_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        editing_tasks = (
            await session.scalars(
                select(RenderTaskRow).where(
                    RenderTaskRow.tenant_id == context.tenant_id,
                    RenderTaskRow.workspace_id == context.workspace_id,
                )
            )
        ).all()
        editing_audits = (
            await session.scalars(
                select(EditingAuditRow).where(
                    EditingAuditRow.tenant_id == context.tenant_id,
                    EditingAuditRow.workspace_id == context.workspace_id,
                )
            )
        ).all()
        editing_timelines = (
            await session.scalars(
                select(TimelineVersionRow).where(
                    TimelineVersionRow.tenant_id == context.tenant_id,
                    TimelineVersionRow.workspace_id == context.workspace_id,
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
            "updated_at": None if account_row is None else _iso(account_row.updated_at),
        }
        holds = (
            [_hold(row, source="generation") for row in generation_holds]
            + [_mapping_hold(row, source="audio") for row in audio_holds]
            + [_hold(row, source="editing") for row in editing_holds]
        )
        _enrich_cost_dimensions(
            holds,
            generation_tasks=generation_tasks,
            generation_audits=generation_audits,
            audio_tasks=audio_tasks,
            editing_tasks=editing_tasks,
            editing_audits=editing_audits,
            editing_timelines=editing_timelines,
        )
        journals = (
            [_journal(row, source="generation") for row in generation_journals]
            + [_mapping_journal(row, source="audio") for row in audio_journals]
            + [_journal(row, source="editing") for row in editing_journals]
        )
        _enrich_cost_dimensions(
            journals,
            generation_tasks=generation_tasks,
            generation_audits=generation_audits,
            audio_tasks=audio_tasks,
            editing_tasks=editing_tasks,
            editing_audits=editing_audits,
            editing_timelines=editing_timelines,
        )
        holds.sort(key=lambda item: (str(item["updated_at"]), str(item["id"])), reverse=True)
        journals.sort(key=lambda item: (str(item["occurred_at"]), str(item["id"])), reverse=True)
        return account, holds, journals

    def _finance_repository(self, context: TrustedWorkspaceContext) -> BillingFinanceRepository:
        return BillingFinanceRepository(
            self._session_factory, tenant_id=context.tenant_id, workspace_id=context.workspace_id
        )

    async def close(self) -> None:
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
        "amount_minor": row.estimated_minor if row.status == "active" else row.actual_minor,
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
        "amount_minor": row["estimated_minor"] if row["status"] == "active" else row["actual_minor"],
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
        "available_delta_minor": available_delta(row.postings),
        "occurred_at": _iso(getattr(row, "occurred_at", None) or getattr(row, "created_at", None)),
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
        "available_delta_minor": available_delta(row["postings"]),
        "occurred_at": _iso(row.get("occurred_at", row.get("created_at"))),
    }


def _iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise BillingRuntimeConfigurationError("BILLING_TIMESTAMP_INVALID")
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BillingRuntimeConfigurationError("BILLING_AMOUNT_INVALID")
    return value


def _cost_items(holds: list[dict[str, object]], currency: str) -> list[dict[str, object]]:
    dimensions: defaultdict[tuple[str, str, str, str, str, str, str], dict[str, int]] = defaultdict(
        lambda: {"estimated_minor": 0, "actual_minor": 0, "released_minor": 0, "task_count": 0}
    )
    tasks: defaultdict[tuple[str, ...], list[str]] = defaultdict(list)
    for hold in holds:
        key = (
            str(hold["project_id"]),
            str(hold.get("source") or "unknown"),
            str(hold.get("model_id") or "unattributed"),
            str(hold.get("actor_id") or "unattributed"),
            str(hold.get("episode_id") or "unattributed"),
            str(hold.get("shot_id") or "unattributed"),
            str(hold.get("currency") or currency),
        )
        task_id = hold.get("task_id")
        if isinstance(task_id, str):
            tasks[key].append(task_id)
        values = dimensions[key]
        values["estimated_minor"] += _integer(hold["estimated_minor"])
        values["actual_minor"] += _integer(hold["actual_minor"])
        values["released_minor"] += _integer(hold["released_minor"])
        values["task_count"] += 1
    return [
        {
            "id": hashlib.sha256(json.dumps(key).encode()).hexdigest(),
            "project_id": key[0],
            "source": key[1],
            "model_id": None if key[2] == "unattributed" else key[2],
            "actor_id": None if key[3] == "unattributed" else key[3],
            "episode_id": None if key[4] == "unattributed" else key[4],
            "shot_id": None if key[5] == "unattributed" else key[5],
            "currency": key[6],
            "task_ids": sorted(set(tasks[key])),
            **values,
        }
        for key, values in sorted(dimensions.items())
    ]


def _filtered_cost_items(
    holds: list[dict[str, object]], currency: str, page: BillingPageQuery
) -> list[dict[str, object]]:
    scoped_holds = filter_records(holds, page, include_text=False)
    return filter_records(_cost_items(scoped_holds, currency), page, text_only=True)


def _account_summary(account: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "key": key,
            "label": f"billing.{key}",
            "value": account[f"{key}_minor"],
            "format": "minor",
            "currency": account["currency"],
        }
        for key in ("available", "held", "spent")
    ]


def _cost_summary(items: list[dict[str, object]]) -> list[dict[str, object]]:
    currencies = sorted({str(item["currency"]) for item in items})
    summary: list[dict[str, object]] = []
    for currency in currencies:
        rows = [item for item in items if item["currency"] == currency]
        for key, field in (("total", "actual_minor"), ("released", "released_minor")):
            summary.append(
                {
                    "key": f"{key}:{currency}",
                    "label": f"billing.{key}",
                    "value": sum(_integer(item[field]) for item in rows),
                    "format": "minor",
                    "currency": currency,
                }
            )
    summary.append(
        {
            "key": "tasks",
            "label": "billing.tasks",
            "value": sum(_integer(item["task_count"]) for item in items),
            "format": "integer",
        }
    )
    return summary


def _enrich_cost_dimensions(
    holds: list[dict[str, object]],
    *,
    generation_tasks: Sequence[GenerationTaskRow],
    generation_audits: Sequence[GenerationAuditRow],
    audio_tasks: Sequence[RowMapping],
    editing_tasks: Sequence[RenderTaskRow],
    editing_audits: Sequence[EditingAuditRow],
    editing_timelines: Sequence[TimelineVersionRow],
) -> None:
    generation_actors: dict[str, str] = {}
    for audit in sorted(generation_audits, key=lambda item: item.occurred_at):
        actor = audit.payload.get("actor_id")
        action = audit.payload.get("action")
        if action == "generation.provider_submitted" and isinstance(actor, str) and not actor.startswith("provider:"):
            generation_actors.setdefault(audit.task_id, actor)
    generation_dimensions: dict[str, tuple[str | None, str | None, str | None, str | None]] = {}
    for task in generation_tasks:
        snapshot = task.snapshot
        request_value = snapshot.get("request")
        request: dict[str, Any] = request_value if isinstance(request_value, dict) else {}
        parameters_value = request.get("parameters")
        parameters: dict[str, Any] = parameters_value if isinstance(parameters_value, dict) else {}
        model_id = snapshot.get("resolved_model_id") or request.get("requested_model_id")
        generation_dimensions[task.task_id] = (
            str(model_id) if isinstance(model_id, str) and model_id else None,
            generation_actors.get(task.task_id),
            _dimension_value(parameters, "episode_id", "episodeId"),
            _dimension_value(parameters, "shot_id", "shotId"),
        )

    audio_dimensions = {
        str(task["id"]): (
            str(task["provider_name"]) if task.get("provider_name") else None,
            str(task["created_by"]),
            str(task["resource_id"]) if task.get("resource_type") == "episode" else None,
            str(task["resource_id"]) if task.get("resource_type") == "shot" else None,
        )
        for task in audio_tasks
    }
    editing_actors: dict[str, str] = {}
    for audit in sorted(editing_audits, key=lambda item: item.occurred_at):
        if audit.object_type == "render_task":
            editing_actors.setdefault(audit.object_id, audit.actor_id)
    editing_episodes = {
        (row.timeline_id, row.version_id): _dimension_value(row.snapshot, "episode_id", "episodeId")
        for row in editing_timelines
    }
    editing_dimensions = {
        task.task_id: (
            "editing-renderer",
            editing_actors.get(task.task_id),
            editing_episodes.get((task.timeline_id, task.timeline_version_id)),
            None,
        )
        for task in editing_tasks
    }

    sources = {
        "generation": generation_dimensions,
        "audio": audio_dimensions,
        "editing": editing_dimensions,
    }
    for hold in holds:
        dimensions = sources.get(str(hold.get("source")), {}).get(str(hold.get("task_id")))
        if dimensions is not None:
            hold["model_id"], hold["actor_id"], hold["episode_id"], hold["shot_id"] = dimensions


def _dimension_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


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
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _page_records(
    records: list[dict[str, object]], page: BillingPageQuery
) -> tuple[list[dict[str, object]], str | None]:
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
    return f"{sort_time}\x1f{record.get('source', '')}\x1f{identity}"


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
