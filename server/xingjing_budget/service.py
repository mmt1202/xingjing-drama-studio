"""Budget admission uses the caller's transaction and the shared account lock."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from server.xingjing_generation.contracts import GenerationRequest
from server.xingjing_platform_persistence.persistence import ProjectRow

from .models import BudgetPolicyRow, BudgetTaskAttributionRow


class BudgetError(ValueError):
    def __init__(self, code: str, *, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.details = dict(details or {})
        self.status_code = (
            403
            if code in {"BUDGET_SHARED_SCOPE_FORBIDDEN", "BUDGET_MANAGEMENT_FORBIDDEN"}
            else 404
            if code.endswith("NOT_FOUND")
            else 409
        )
        super().__init__(code)


@dataclass(frozen=True)
class BudgetTarget:
    kind: Literal["episode", "project_shared"]
    episode_id: int | None = None
    source_kind: str = "explicit"
    source_reference: str = ""


@dataclass(frozen=True)
class EpisodeBudgetPlan:
    limit_minor: int | None
    title: str | None = None


EpisodeCatalogLoader = Callable[[str], Awaitable[Mapping[int, EpisodeBudgetPlan]]]


@dataclass
class LockedBudgetScope:
    tenant_id: str
    workspace_id: str
    project_id: str
    project: ProjectRow
    catalog: dict[int, EpisodeBudgetPlan] | None = None


@dataclass(frozen=True)
class BudgetUsage:
    currency: str
    consumed_minor: int = 0
    reserved_minor: int = 0

    @property
    def committed_minor(self) -> int:
        return self.consumed_minor + self.reserved_minor


@dataclass(frozen=True)
class BudgetSnapshot:
    project_limit_minor: int | None
    currency: str
    usage: tuple[BudgetUsage, ...]
    unattributed_usage: tuple[BudgetUsage, ...]
    episodes: tuple[dict[str, object], ...]
    requires_attribution: bool
    episode_policy_confirmed: bool


async def _file_catalog(project_id: str) -> Mapping[int, EpisodeBudgetPlan]:
    from lib.project_manager import get_project_manager

    def read() -> dict[int, EpisodeBudgetPlan]:
        project = get_project_manager().load_project(project_id)
        result: dict[int, EpisodeBudgetPlan] = {}
        for item in project.get("episodes", []):
            if not isinstance(item, dict):
                raise BudgetError("BUDGET_EPISODE_CATALOG_UNAVAILABLE")
            episode, limit = item.get("episode"), item.get("budget_minor")
            if type(episode) is not int or episode < 1 or episode in result:
                raise BudgetError("BUDGET_EPISODE_CATALOG_UNAVAILABLE")
            if limit is not None and (type(limit) is not int or limit < 0):
                raise BudgetError("BUDGET_EPISODE_POLICY_UNCONFIRMED")
            result[episode] = EpisodeBudgetPlan(limit, str(item.get("title", ""))[:255] or None)
        return result

    try:
        return await asyncio.to_thread(read)
    except BudgetError:
        raise
    except Exception as error:
        raise BudgetError("BUDGET_EPISODE_CATALOG_UNAVAILABLE") from error


class BudgetAdmissionService:
    def __init__(self, *, episode_catalog_loader: EpisodeCatalogLoader | None = None) -> None:
        self._catalog_loader = episode_catalog_loader or _file_catalog

    async def lock_scope(
        self, session: AsyncSession, *, workspace_id: str, project_id: str, tenant_id: str | None = None
    ) -> LockedBudgetScope:
        self._assert_source_database(session)
        conditions = [
            ProjectRow.id == project_id,
            ProjectRow.workspace_id == workspace_id,
            ProjectRow.deleted_at.is_(None),
        ]
        if tenant_id is not None:
            conditions.append(ProjectRow.tenant_id == tenant_id)
        row = await session.scalar(
            select(ProjectRow).where(*conditions).with_for_update().execution_options(populate_existing=True)
        )
        if row is None:
            raise BudgetError("BUDGET_PROJECT_NOT_FOUND")
        return LockedBudgetScope(row.tenant_id, workspace_id, project_id, row)

    @staticmethod
    def _assert_source_database(session: AsyncSession) -> None:
        from server.xingjing_analytics.database import identity

        current = session.get_bind().engine.url
        for variable in (
            "XINGJING_PROJECT_DATABASE_URL",
            "XINGJING_GENERATION_DATABASE_URL",
            "XINGJING_AUDIO_DATABASE_URL",
            "XINGJING_EDITING_DATABASE_URL",
        ):
            configured = os.environ.get(variable, "").strip()
            if configured:
                try:
                    if identity(current) != identity(configured):
                        raise ValueError("database differs")
                except (ValueError, TypeError) as error:
                    raise BudgetError("BUDGET_SOURCE_DATABASE_MISMATCH") from error

    @staticmethod
    def _scope(table: Any, scope: LockedBudgetScope) -> tuple[Any, ...]:
        return (
            table.tenant_id == scope.tenant_id,
            table.workspace_id == scope.workspace_id,
            table.project_id == scope.project_id,
        )

    async def _policies(self, session: AsyncSession, scope: LockedBudgetScope) -> dict[str, BudgetPolicyRow]:
        return {
            row.scope_key: row
            for row in await session.scalars(select(BudgetPolicyRow).where(*self._scope(BudgetPolicyRow, scope)))
        }

    async def _catalog(self, scope: LockedBudgetScope) -> dict[int, EpisodeBudgetPlan]:
        if scope.catalog is None:
            scope.catalog = dict(await self._catalog_loader(scope.project_id))
        return scope.catalog

    @staticmethod
    async def _account_lock(session: AsyncSession, scope: LockedBudgetScope) -> None:
        statement = text("SELECT workspace_id FROM xingjing_generation_billing_accounts WHERE workspace_id=:workspace")
        if session.get_bind().dialect.name != "sqlite":
            statement = text(str(statement) + " FOR UPDATE")
        await session.execute(statement, {"workspace": scope.workspace_id})

    async def _costs(self, session: AsyncSession, scope: LockedBudgetScope) -> list[Mapping[str, Any]]:
        self._assert_source_database(session)
        queries = []
        for module, name, tenant in (
            ("generation", "xingjing_generation_billing_holds", False),
            ("audio", "xingjing_audio_billing_holds", True),
            ("editing", "xingjing_editing_render_billing_holds", True),
        ):
            queries.append(
                f"SELECT '{module}' AS module,h.task_id,h.currency,h.estimated_minor,h.actual_minor,h.released_minor,h.status,a.kind,a.episode_id FROM {name} h LEFT JOIN xingjing_budget_task_attributions a ON a.tenant_id=:tenant AND a.workspace_id=h.workspace_id AND a.project_id=h.project_id AND a.module='{module}' AND a.task_id=h.task_id WHERE h.workspace_id=:workspace AND h.project_id=:project"
                + (" AND h.tenant_id=:tenant" if tenant else "")
            )
        combined = " UNION ALL ".join(queries)
        aggregate = (
            "SELECT currency,kind,episode_id,SUM(actual_minor) AS actual_minor,"
            "SUM(actual_minor + CASE WHEN status='active' THEN estimated_minor-actual_minor-released_minor ELSE 0 END) AS estimated_minor,"
            "0 AS released_minor,'active' AS status,"
            "SUM(CASE WHEN actual_minor < 0 OR (status='active' AND estimated_minor-actual_minor-released_minor < 0) THEN 1 ELSE 0 END) AS invalid_count "
            f"FROM ({combined}) scoped_costs GROUP BY currency,kind,episode_id"
        )
        return [
            dict(row)
            for row in (
                (
                    await session.execute(
                        text(aggregate),
                        {"tenant": scope.tenant_id, "workspace": scope.workspace_id, "project": scope.project_id},
                    )
                ).mappings()
            )
        ]

    @staticmethod
    def _usage(
        costs: list[Mapping[str, Any]], *, episode: int | None = None, unattributed: bool = False
    ) -> tuple[BudgetUsage, ...]:
        totals: dict[str, list[int]] = {}
        for row in costs:
            if row.get("invalid_count", 0):
                raise BudgetError("BUDGET_USAGE_INVALID")
            if episode is not None and (row["kind"] != "episode" or row["episode_id"] != episode):
                continue
            if unattributed and row["kind"] not in (None, "unattributed"):
                continue
            actual = int(row["actual_minor"])
            remaining = (
                int(row["estimated_minor"]) - actual - int(row["released_minor"]) if row["status"] == "active" else 0
            )
            if actual < 0 or remaining < 0:
                raise BudgetError("BUDGET_USAGE_INVALID")
            value = totals.setdefault(str(row["currency"]), [0, 0])
            value[0] += actual
            value[1] += remaining
        return tuple(BudgetUsage(currency, *values) for currency, values in sorted(totals.items()))

    @staticmethod
    def _limit(policy: BudgetPolicyRow | None, scope: LockedBudgetScope) -> tuple[int | None, str]:
        if policy is not None:
            return policy.limit_minor, policy.currency
        value = scope.project.configuration.get("budgetMinor")
        if value is not None and (type(value) is not int or value < 0):
            raise BudgetError("BUDGET_POLICY_INVALID")
        return value, "CNY"

    @staticmethod
    def _check_limit(
        limit: int | None,
        currency: str,
        usage: tuple[BudgetUsage, ...],
        increment: int,
        requested_currency: str,
        code: str,
    ) -> None:
        if limit is None:
            return
        if requested_currency != currency or any(item.currency != currency and item.committed_minor for item in usage):
            raise BudgetError("BUDGET_CURRENCY_MISMATCH")
        committed = sum(item.committed_minor for item in usage)
        if committed + increment > limit:
            raise BudgetError(
                code,
                details={
                    "limitMinor": limit,
                    "committedMinor": committed,
                    "requestedMinor": increment,
                    "currency": currency,
                },
            )

    async def _confirmed_catalog(
        self, session: AsyncSession, scope: LockedBudgetScope, policies: dict[str, BudgetPolicyRow], *, record: bool
    ) -> dict[int, EpisodeBudgetPlan]:
        catalog = await self._catalog(scope)
        for episode, plan in catalog.items():
            policy = policies.get(f"episode:{episode}")
            if plan.limit_minor is not None and (policy is None or not policy.active or not policy.confirmed):
                raise BudgetError("BUDGET_EPISODE_POLICY_UNCONFIRMED")
            if record and policy is None:
                policy = BudgetPolicyRow(
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                    scope_key=f"episode:{episode}",
                    episode_id=episode,
                    title=plan.title,
                    currency="CNY",
                    limit_minor=None,
                    active=True,
                    confirmed=False,
                    version=1,
                    updated_by="episode-catalog",
                    updated_at=datetime.now(UTC),
                )
                session.add(policy)
                policies[policy.scope_key] = policy
        return catalog

    async def check_reservation(
        self,
        session: AsyncSession,
        scope: LockedBudgetScope,
        *,
        module: str,
        task_id: str,
        currency: str,
        estimated_minor: int,
        target: BudgetTarget | None = None,
    ) -> BudgetUsage:
        if module not in {"generation", "audio", "editing"} or estimated_minor <= 0:
            raise BudgetError("BUDGET_RESERVATION_INVALID")
        await self._account_lock(session, scope)
        policies = await self._policies(session, scope)
        catalog = await self._confirmed_catalog(session, scope, policies, record=True)
        identity = (scope.tenant_id, scope.workspace_id, scope.project_id, module, task_id)
        existing = await session.get(BudgetTaskAttributionRow, identity)
        if existing is not None:
            original = (
                None
                if existing.kind == "unattributed"
                else BudgetTarget(
                    cast(Literal["episode", "project_shared"], existing.kind),
                    existing.episode_id,
                    existing.source_kind,
                    existing.source_reference,
                )
            )
            if target is not None and target != original:
                raise BudgetError("BUDGET_ATTRIBUTION_CONFLICT")
            target = original
        requires = any(
            row.active and row.episode_id is not None and row.limit_minor is not None for row in policies.values()
        )
        if target is None and requires:
            raise BudgetError("BUDGET_ATTRIBUTION_REQUIRED")
        if target is not None:
            if (
                target.kind not in {"episode", "project_shared"}
                or not target.source_kind
                or len(target.source_kind) > 64
                or len(target.source_reference) > 512
            ):
                raise BudgetError("BUDGET_ATTRIBUTION_INVALID")
            if target.kind == "episode" and (type(target.episode_id) is not int or target.episode_id not in catalog):
                raise BudgetError("BUDGET_EPISODE_NOT_FOUND")
            if target.kind == "project_shared" and target.episode_id is not None:
                raise BudgetError("BUDGET_ATTRIBUTION_INVALID")
        costs = await self._costs(session, scope)
        normalized = currency.upper()
        usage = self._usage(costs)
        limit, policy_currency = self._limit(policies.get("project"), scope)
        self._check_limit(limit, policy_currency, usage, estimated_minor, normalized, "PROJECT_BUDGET_EXCEEDED")
        if target is not None and target.kind == "episode":
            episode_policy = policies.get(f"episode:{target.episode_id}")
            if episode_policy is not None and not episode_policy.active:
                raise BudgetError("BUDGET_EPISODE_NOT_FOUND")
            self._check_limit(
                episode_policy.limit_minor if episode_policy else None,
                episode_policy.currency if episode_policy else "CNY",
                self._usage(costs, episode=target.episode_id),
                estimated_minor,
                normalized,
                "EPISODE_BUDGET_EXCEEDED",
            )
        if existing is None:
            session.add(
                BudgetTaskAttributionRow(
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                    module=module,
                    task_id=task_id,
                    kind=target.kind if target else "unattributed",
                    episode_id=target.episode_id if target else None,
                    source_kind=target.source_kind if target else "legacy_unspecified",
                    source_reference=target.source_reference if target else "",
                    created_at=datetime.now(UTC),
                )
            )
        return next((value for value in usage if value.currency == normalized), BudgetUsage(normalized))

    async def resource_target(
        self, session: AsyncSession, scope: LockedBudgetScope, *, module: str, source_kind: str, source_reference: str
    ) -> BudgetTarget | None:
        rows = list(
            await session.scalars(
                select(BudgetTaskAttributionRow).where(
                    *self._scope(BudgetTaskAttributionRow, scope),
                    BudgetTaskAttributionRow.module == module,
                    BudgetTaskAttributionRow.source_kind == source_kind,
                    BudgetTaskAttributionRow.source_reference == source_reference,
                    BudgetTaskAttributionRow.kind != "unattributed",
                )
            )
        )
        targets = {(row.kind, row.episode_id) for row in rows}
        if len(targets) > 1:
            raise BudgetError("BUDGET_RESOURCE_SCOPE_CONFLICT")
        if not rows:
            return None
        row = rows[0]
        return BudgetTarget(
            cast(Literal["episode", "project_shared"], row.kind), row.episode_id, row.source_kind, row.source_reference
        )

    async def set_project_limit(
        self,
        session: AsyncSession,
        scope: LockedBudgetScope,
        *,
        limit_minor: int | None,
        currency: str = "CNY",
        actor_id: str,
    ) -> BudgetSnapshot:
        self._validate_limit(limit_minor, currency)
        await self._account_lock(session, scope)
        costs = await self._costs(session, scope)
        self._check_limit(limit_minor, currency, self._usage(costs), 0, currency, "BUDGET_BELOW_COMMITTED_USAGE")
        policies = await self._policies(session, scope)
        self._put_policy(session, scope, policies.get("project"), "project", None, limit_minor, currency, actor_id)
        await session.flush()
        return await self.snapshot(session, scope, allow_missing_catalog=True)

    async def set_episode_limits(
        self,
        session: AsyncSession,
        scope: LockedBudgetScope,
        *,
        limits: Mapping[int, int | None],
        currency: str = "CNY",
        actor_id: str,
        titles: Mapping[int, str] | None = None,
    ) -> BudgetSnapshot:
        for episode, limit in limits.items():
            if type(episode) is not int or episode < 1:
                raise BudgetError("BUDGET_EPISODE_NOT_FOUND")
            self._validate_limit(limit, currency)
        await self._account_lock(session, scope)
        costs = await self._costs(session, scope)
        if any(value is not None for value in limits.values()) and any(
            value.committed_minor for value in self._usage(costs, unattributed=True)
        ):
            raise BudgetError("BUDGET_HISTORICAL_ATTRIBUTION_UNCONFIRMED")
        policies = await self._policies(session, scope)
        for episode, limit in limits.items():
            self._check_limit(
                limit, currency, self._usage(costs, episode=episode), 0, currency, "BUDGET_BELOW_COMMITTED_USAGE"
            )
            self._put_policy(
                session,
                scope,
                policies.get(f"episode:{episode}"),
                f"episode:{episode}",
                episode,
                limit,
                currency,
                actor_id,
                (titles or {}).get(episode),
            )
        for row in policies.values():
            if row.episode_id is not None and row.episode_id not in limits and row.active:
                row.active = False
                row.version += 1
                row.updated_by = actor_id
                row.updated_at = datetime.now(UTC)
        await session.flush()
        return await self.snapshot(session, scope, allow_missing_catalog=True)

    @staticmethod
    def _validate_limit(limit: int | None, currency: str) -> None:
        if (limit is not None and (type(limit) is not int or limit < 0)) or currency != "CNY":
            raise BudgetError("BUDGET_POLICY_INVALID")

    @staticmethod
    def _put_policy(
        session: AsyncSession,
        scope: LockedBudgetScope,
        row: BudgetPolicyRow | None,
        key: str,
        episode: int | None,
        limit: int | None,
        currency: str,
        actor: str,
        title: str | None = None,
    ) -> None:
        if row is None:
            session.add(
                BudgetPolicyRow(
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                    scope_key=key,
                    episode_id=episode,
                    title=title,
                    currency=currency,
                    limit_minor=limit,
                    active=True,
                    confirmed=True,
                    version=1,
                    updated_by=actor,
                    updated_at=datetime.now(UTC),
                )
            )
        elif (row.limit_minor, row.currency, row.active, row.confirmed, row.title) != (
            limit,
            currency,
            True,
            True,
            title,
        ):
            row.limit_minor = limit
            row.currency = currency
            row.active = True
            row.confirmed = True
            row.title = title
            row.version += 1
            row.updated_by = actor
            row.updated_at = datetime.now(UTC)

    async def snapshot(
        self, session: AsyncSession, scope: LockedBudgetScope, *, allow_missing_catalog: bool = False
    ) -> BudgetSnapshot:
        policies = await self._policies(session, scope)
        costs = await self._costs(session, scope)
        catalog_available = True
        try:
            catalog = await self._catalog(scope)
        except BudgetError:
            if not allow_missing_catalog:
                raise
            catalog = {}
            catalog_available = False
        limit, currency = self._limit(policies.get("project"), scope)
        confirmed = catalog_available and all(
            plan.limit_minor is None
            or (
                policies.get(f"episode:{episode}") is not None
                and policies[f"episode:{episode}"].active
                and policies[f"episode:{episode}"].confirmed
            )
            for episode, plan in catalog.items()
        )
        episodes = tuple(
            {
                "episodeId": number,
                "title": plan.title,
                "limitMinor": policies[f"episode:{number}"].limit_minor
                if f"episode:{number}" in policies and policies[f"episode:{number}"].active
                else None,
                "plannedLimitMinor": plan.limit_minor,
                "confirmed": f"episode:{number}" in policies
                and policies[f"episode:{number}"].active
                and policies[f"episode:{number}"].confirmed,
                "usage": self._usage(costs, episode=number),
            }
            for number, plan in sorted(catalog.items())
        )
        return BudgetSnapshot(
            limit,
            currency,
            self._usage(costs),
            self._usage(costs, unattributed=True),
            episodes,
            any(row.active and row.episode_id is not None and row.limit_minor is not None for row in policies.values()),
            confirmed,
        )

    async def resolve_generation_target(
        self,
        session: AsyncSession,
        scope: LockedBudgetScope,
        *,
        request: GenerationRequest,
        permissions: frozenset[str],
    ) -> BudgetTarget | None:
        parameters = request.parameters
        source_keys = [key for key in ("storyboard_id", "asset_id", "cover_plan_id") if parameters.get(key) is not None]
        if len(source_keys) > 1 or any(
            not isinstance(parameters[key], str) or not parameters[key] or len(str(parameters[key])) > 128
            for key in source_keys
        ):
            raise BudgetError("BUDGET_SOURCE_INVALID")
        source_version = parameters.get("storyboard_version")
        if "storyboard_id" in source_keys and (
            not isinstance(source_version, int)
            or isinstance(source_version, bool)
            or source_version < 1
            or not isinstance(parameters.get("shot_id"), str)
        ):
            raise BudgetError("BUDGET_SOURCE_INVALID")
        if "asset_id" in source_keys and not isinstance(parameters.get("source_version_id"), str):
            raise BudgetError("BUDGET_SOURCE_INVALID")
        target: BudgetTarget | None = None
        if parameters.get("storyboard_id") is not None:
            from server.xingjing_storyboard.models import Storyboard

            raw = await session.scalar(
                text(
                    "SELECT payload FROM xingjing_storyboards WHERE tenant_id=:tenant AND workspace_id=:workspace AND project_id=:project AND storyboard_id=:id"
                ),
                {
                    "tenant": scope.tenant_id,
                    "workspace": scope.workspace_id,
                    "project": scope.project_id,
                    "id": parameters["storyboard_id"],
                },
            )
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(payload, Mapping):
                    raise ValueError("storyboard source missing")
                board = Storyboard.from_dict(payload)
                version = next(item for item in board.versions if item.number == parameters.get("storyboard_version"))
                shot = next(item for item in version.shots if item.shot_id == parameters.get("shot_id"))
                if request.prompt != shot.prompt:
                    raise ValueError("prompt differs")
                target = BudgetTarget(
                    "episode",
                    int(board.episode_id),
                    "storyboard",
                    f"{board.storyboard_id}:{version.number}:{shot.shot_id}",
                )
            except (ValueError, TypeError, KeyError, StopIteration) as error:
                raise BudgetError("BUDGET_SOURCE_INVALID") from error
        elif parameters.get("cover_plan_id") is not None:
            if "settings.manage" not in permissions:
                raise BudgetError("BUDGET_SOURCE_INVALID")
            row = (
                await session.execute(
                    text(
                        "SELECT p.episode_id,t.snapshot FROM xingjing_cover_plans p "
                        "JOIN xingjing_editing_final_video_versions f ON f.tenant_id=p.tenant_id AND f.workspace_id=p.workspace_id AND f.project_id=p.project_id AND f.version_id=p.final_version_id "
                        "JOIN xingjing_editing_timeline_versions t ON t.tenant_id=f.tenant_id AND t.workspace_id=f.workspace_id AND t.project_id=f.project_id AND t.timeline_id=f.timeline_id AND t.version_id=f.timeline_version_id "
                        "WHERE p.tenant_id=:tenant AND p.workspace_id=:workspace AND p.project_id=:project AND p.id=:id"
                    ),
                    {
                        "tenant": scope.tenant_id,
                        "workspace": scope.workspace_id,
                        "project": scope.project_id,
                        "id": parameters["cover_plan_id"],
                    },
                )
            ).one_or_none()
            try:
                from server.xingjing_editing.contracts import TimelineVersion

                if row is None:
                    raise ValueError("cover source missing")
                raw = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                timeline = TimelineVersion.model_validate(raw)
                if not timeline.episode_id or str(timeline.episode_id) != row[0]:
                    raise ValueError("cover episode differs")
                target = BudgetTarget("episode", int(timeline.episode_id), "cover", str(parameters["cover_plan_id"]))
            except (ValueError, TypeError, KeyError) as error:
                raise BudgetError("BUDGET_SOURCE_INVALID") from error
        elif parameters.get("asset_id") is not None:
            row = (
                await session.execute(
                    text(
                        "SELECT a.owner_project_id,v.content_sha256 FROM xingjing_asset_records a JOIN xingjing_asset_versions v ON v.tenant_id=a.tenant_id AND v.workspace_id=a.workspace_id AND v.asset_id=a.asset_id WHERE a.tenant_id=:tenant AND a.workspace_id=:workspace AND a.asset_id=:asset AND v.version_id=:version"
                    ),
                    {
                        "tenant": scope.tenant_id,
                        "workspace": scope.workspace_id,
                        "asset": parameters["asset_id"],
                        "version": parameters.get("source_version_id"),
                    },
                )
            ).one_or_none()
            if (
                row is None
                or row[0] != scope.project_id
                or "asset.manage" not in permissions
                or (
                    parameters.get("source_content_sha256") is not None
                    and parameters["source_content_sha256"] != row[1]
                )
            ):
                raise BudgetError("BUDGET_SOURCE_INVALID")
            target = BudgetTarget(
                "project_shared", None, "asset", f"{parameters['asset_id']}:{parameters['source_version_id']}"
            )
        selection = request.budget_scope
        if selection is not None:
            selected = BudgetTarget(selection.kind, selection.episode_id, "explicit", "")
            if target is not None and (target.kind, target.episode_id) != (selected.kind, selected.episode_id):
                raise BudgetError("BUDGET_ATTRIBUTION_CONFLICT")
            if target is None:
                if selected.kind == "project_shared" and not {"billing.budget.manage", "project.manage"} & permissions:
                    raise BudgetError("BUDGET_SHARED_SCOPE_FORBIDDEN")
                target = selected
        return target
