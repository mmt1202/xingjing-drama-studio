"""Keep file-confirmed episodes unavailable to competing edits until SQL is recovered."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.episode_editor import EpisodeEditError
from server.xingjing_identity_context import TrustedWorkspaceContext

from .models import PlanConfirmationRow


async def ensure_plans_complete(
    session: AsyncSession,
    context: TrustedWorkspaceContext,
    project_id: str,
    project: dict,
    *,
    allow_plan_id: str | None = None,
) -> None:
    receipts = set(project.get("episode_plan_receipts", {})) - {allow_plan_id}
    if not receipts:
        return
    completed = set(
        await session.scalars(
            select(PlanConfirmationRow.plan_id).where(
                PlanConfirmationRow.tenant_id == context.tenant_id,
                PlanConfirmationRow.workspace_id == context.workspace_id,
                PlanConfirmationRow.project_id == project_id,
                PlanConfirmationRow.plan_id.in_(receipts),
            )
        )
    )
    if receipts - completed:
        raise EpisodeEditError("SOURCE_PLAN_CONFIRMATION_PENDING", 409)
