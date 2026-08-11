"""Provider callback application for M06 tasks and verified generated assets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from server.xingjing_generation.lifecycle import (
    CallbackDisposition,
    CallbackResult,
    ProviderCallback,
    apply_provider_callback,
)
from server.xingjing_generation_persistence import (
    CostEvidence,
    GeneratedAsset,
    SqlAlchemyGenerationTaskRepository,
    TaskScopeNotFound,
)


@dataclass(frozen=True)
class ProviderGeneratedOutput:
    """Output already stored by a trusted provider adapter in controlled storage."""

    asset: GeneratedAsset


class GenerationProviderCallbackService:
    """Applies a provider callback without accepting unverified output identifiers."""

    def __init__(self, repository: SqlAlchemyGenerationTaskRepository) -> None:
        self._repository = repository

    async def apply(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        callback: ProviderCallback,
        outputs: tuple[ProviderGeneratedOutput, ...] = (),
        cost: CostEvidence | None = None,
        billing_event_id: str | None = None,
        received_at: datetime,
    ) -> CallbackResult:
        current = await self._repository.get(workspace_id, project_id, task_id)
        if current is None:
            raise TaskScopeNotFound()
        result = apply_provider_callback(current, callback, at=received_at)
        if result.disposition is not CallbackDisposition.APPLIED:
            return result
        if callback.succeeded:
            assets = tuple(item.asset for item in outputs)
            await self._repository.complete_with_generated_assets(
                result.task,
                expected_version=current.version,
                assets=assets,
                cost=cost,
                billing_event_id=billing_event_id,
            )
        else:
            await self._repository.save_with_billing_release(
                result.task,
                expected_version=current.version,
                event_id=billing_event_id or callback.event_id,
                cost=cost,
            )
        return result
