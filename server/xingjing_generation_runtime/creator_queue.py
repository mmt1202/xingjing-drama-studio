"""Creator-facing task names and controls, projected independently of provider execution and settlement."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_assets_persistence.repository import AssetRow
from server.xingjing_generation import GenerationTask
from server.xingjing_generation_persistence.repository import AuditRow, TaskRow, TaskScopeNotFound
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_model_control.runtime import ModelRouteReservationRow
from server.xingjing_storyboard_persistence.repository import StoryboardRow

from .native_image.models import NativeImageExecutionRow

STOP_WAITING = "generation.waiting_stopped"
TERMINAL = ("succeeded", "cancelled", "failed", "timeout")
RESTARTABLE = ("waiting_stopped", "failed", "timeout", "cancelled")
GROUPS = {
    "working": ("queued", "running", "retrying", "cancelling", "publishing"),
    "attention": ("checking", "waiting_stopped"),
    "completed": ("succeeded",),
    "ended": ("cancelled", "failed", "timeout"),
}


def waiting_stopped():
    return exists(
        select(AuditRow.audit_id).where(
            AuditRow.workspace_id == TaskRow.workspace_id,
            AuditRow.project_id == TaskRow.project_id,
            AuditRow.task_id == TaskRow.task_id,
            AuditRow.payload["action"].as_string() == STOP_WAITING,
        )
    ).correlate(TaskRow)


def _records(context: TrustedWorkspaceContext, project: str):
    native = NativeImageExecutionRow
    route = ModelRouteReservationRow
    dispatched = and_(route.kind == "gateway", route.dispatch_started_at.is_not(None))
    state = case(
        (TaskRow.status.in_(TERMINAL), TaskRow.status),
        (native.status == "result_ready", "publishing"),
        (waiting_stopped(), "waiting_stopped"),
        (
            or_(
                native.status == "unknown",
                and_(dispatched, TaskRow.status == "queued"),
                and_(
                    dispatched,
                    TaskRow.status == "running",
                    TaskRow.snapshot["timeout_at"].as_string() < datetime.now(UTC).isoformat(),
                ),
            ),
            "checking",
        ),
        else_=TaskRow.status,
    ).label("creator_state")
    query = select(TaskRow.snapshot, state, native.status).where(
        TaskRow.workspace_id == context.workspace_id, TaskRow.project_id == project
    )
    for model in (native, route):
        query = query.outerjoin(
            model,
            and_(
                model.workspace_id == TaskRow.workspace_id,
                model.project_id == TaskRow.project_id,
                model.task_id == TaskRow.task_id,
                model.tenant_id == context.tenant_id,
            ),
        )
    return query, state


def _action(task: GenerationTask, state: str, native: str | None, gateway_available: bool) -> str | None:
    if state in TERMINAL or state in {"waiting_stopped", "publishing"}:
        return None
    if state == "checking" or state == "cancelling":
        return "stop_waiting"
    if state in {"queued", "retrying"} or (not native and task.provider_job_id and gateway_available):
        return "cancel"
    return "stop_waiting"


class CreatorQueue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, gateway_available: bool):
        self.sessions = sessions
        self.gateway_available = gateway_available

    async def can_restart(self, context: TrustedWorkspaceContext, project: str, task_id: str) -> bool:
        query, state = _records(context, project)
        async with self.sessions() as session:
            return await session.scalar(query.with_only_columns(state).where(TaskRow.task_id == task_id)) in RESTARTABLE

    async def read(
        self,
        context: TrustedWorkspaceContext,
        project: str,
        *,
        task_id: str | None = None,
        group: str | None = None,
        media: str | None = None,
        status: str | None = None,
        capability: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ):
        query, state = _records(context, project)
        if task_id:
            query = query.where(TaskRow.task_id == task_id)
        if group:
            query = query.where(state.in_(GROUPS[group]))
        if status:
            query = query.where(TaskRow.status == status)
        request = TaskRow.snapshot["request"]
        if media:
            query = query.where(request["media_type"].as_string() == media)
        if capability:
            query = query.where(request["capability"].as_string() == capability)
        async with self.sessions() as session:
            total = await session.scalar(select(func.count()).select_from(query.subquery())) if not task_id else 1
            rows = (
                await session.execute(
                    query.order_by(TaskRow.created_at.desc(), TaskRow.task_id.desc()).offset(offset).limit(limit)
                )
            ).all()
            tasks = [GenerationTask.model_validate(row[0]) for row in rows]
            assets, shots = await self._subjects(session, context, project, tasks)
        results = []
        for task, (_, stage, native_state) in zip(tasks, rows, strict=True):
            params = task.request.parameters
            asset = assets.get(str(params.get("asset_id")))
            shot = shots.get((str(params.get("storyboard_id")), str(params.get("shot_id"))))
            kind = "generation"
            if params.get("asset_operation") == "reference_image":
                kind = "reference"
            elif task.request.capability in {"character_turnaround", "character_expressions"}:
                kind = task.request.capability
            elif params.get("shot_id"):
                kind = "shot"
            can_manage = "generation.manage" in context.permissions
            payload = task.creator_payload()
            payload["creator"] = {
                "kind": kind,
                "state": stage,
                "action": _action(task, stage, native_state, self.gateway_available) if can_manage else None,
                "canRetry": can_manage and stage in RESTARTABLE,
                "canChooseResult": can_manage and stage == "succeeded",
                "subject": asset[0] if asset else None,
                "assetKind": asset[1] if asset else None,
                "sourceAssetId": params.get("asset_id") if asset else None,
                "episode": shot[0] if shot else None,
                "shot": shot[1] if shot else None,
            }
            results.append(payload)
        return results, total or 0

    @staticmethod
    async def _subjects(
        session: AsyncSession, context: TrustedWorkspaceContext, project: str, tasks: list[GenerationTask]
    ):
        params = [task.request.parameters for task in tasks]
        assets: dict[str, tuple[str, str]] = {}
        ids = {value for p in params if isinstance(value := p.get("asset_id"), str)}
        if ids and "asset.view" in context.permissions:
            rows = await session.execute(
                select(AssetRow.asset_id, AssetRow.name, AssetRow.kind).where(
                    AssetRow.tenant_id == context.tenant_id,
                    AssetRow.workspace_id == context.workspace_id,
                    AssetRow.owner_project_id == project,
                    AssetRow.asset_id.in_(ids),
                )
            )
            assets = {asset_id: (name, kind) for asset_id, name, kind in rows}
        shots: dict[tuple[str, str], tuple[int | None, str]] = {}
        boards = {value for p in params if isinstance(value := p.get("storyboard_id"), str)}
        if boards and "shot.view" in context.permissions:
            rows = await session.execute(
                select(StoryboardRow.storyboard_id, StoryboardRow.episode_id, StoryboardRow.payload["shots"]).where(
                    StoryboardRow.tenant_id == context.tenant_id,
                    StoryboardRow.workspace_id == context.workspace_id,
                    StoryboardRow.project_id == project,
                    StoryboardRow.storyboard_id.in_(boards),
                )
            )
            for board, episode, items in rows:
                for item in items or ():
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("shot_id"), str)
                        and isinstance(item.get("shot_number"), str)
                    ):
                        shots[(board, item["shot_id"])] = (
                            int(episode) if episode.isdecimal() else None,
                            item["shot_number"],
                        )
        return assets, shots

    async def stop_waiting(self, context: TrustedWorkspaceContext, project: str, task_id: str, expected_version: int):
        # The task stays available to callbacks and financial reconciliation. Do not bump its execution version.
        async with self.sessions.begin() as session:
            row = await session.get(TaskRow, (context.workspace_id, project, task_id), with_for_update=True)
            if row is None:
                raise TaskScopeNotFound()
            task = GenerationTask.model_validate(row.snapshot)
            key = str(uuid5(NAMESPACE_URL, f"generation/wait/{context.workspace_id}/{project}/{task_id}"))
            if await session.get(AuditRow, key) is not None:
                return task
            query, _ = _records(context, project)
            _, stage, native = (await session.execute(query.where(TaskRow.task_id == task_id))).one()
            if (
                task.version != expected_version
                or _action(task, stage, native, self.gateway_available) != "stop_waiting"
            ):
                raise ValueError("GENERATION_TASK_VERSION_CONFLICT")
            marker = AuditRow(
                audit_id=key,
                workspace_id=context.workspace_id,
                project_id=project,
                task_id=task_id,
                request_id=context.request_id or key,
                occurred_at=datetime.now(UTC),
                payload={
                    "action": STOP_WAITING,
                    "actor_id": context.actor_id,
                    "result": "review_requested",
                    "before": {"status": stage},
                    "after": {"waitingStopped": True},
                },
            )
            try:
                async with session.begin_nested():
                    session.add(marker)
                    await session.flush()
            except IntegrityError:
                if await session.get(AuditRow, key) is None:
                    raise
            return task
