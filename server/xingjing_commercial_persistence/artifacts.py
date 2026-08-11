"""Validate M14 deliveries against the selected immutable M08 final-video version."""

from __future__ import annotations

from sqlalchemy import select

from server.xingjing_editing.contracts import canonical_sha256
from server.xingjing_editing_persistence.models import (
    FinalVideoSelectionRow,
    FinalVideoVersionRow,
    TimelineVersionRow,
)

from .transaction_context import current_commercial_session


class SqlAlchemyDeliveryArtifactPort:
    """Reads the authoritative editing catalog inside the commercial transaction."""

    def verify_selected_artifact(
        self, *, workspace_id: str, artifact_version_id: str, artifact_digest: str
    ) -> bool:
        session = current_commercial_session()
        row = session.execute(
            select(FinalVideoVersionRow.snapshot, TimelineVersionRow.snapshot)
            .join(
                FinalVideoSelectionRow,
                (FinalVideoSelectionRow.tenant_id == FinalVideoVersionRow.tenant_id)
                & (FinalVideoSelectionRow.workspace_id == FinalVideoVersionRow.workspace_id)
                & (FinalVideoSelectionRow.final_video_id == FinalVideoVersionRow.final_video_id)
                & (FinalVideoSelectionRow.selected_version_id == FinalVideoVersionRow.version_id),
            )
            .join(
                TimelineVersionRow,
                (TimelineVersionRow.tenant_id == FinalVideoVersionRow.tenant_id)
                & (TimelineVersionRow.workspace_id == FinalVideoVersionRow.workspace_id)
                & (TimelineVersionRow.project_id == FinalVideoVersionRow.project_id)
                & (TimelineVersionRow.timeline_id == FinalVideoVersionRow.timeline_id)
                & (TimelineVersionRow.version_id == FinalVideoVersionRow.timeline_version_id),
            )
            .where(
                FinalVideoVersionRow.workspace_id == workspace_id,
                FinalVideoVersionRow.version_id == artifact_version_id,
            )
            .limit(1)
        ).one_or_none()
        if row is None:
            return False
        version_snapshot, timeline_snapshot = row
        output = version_snapshot.get("output")
        output_policy = timeline_snapshot.get("output_policy")
        if not isinstance(output, dict) or not isinstance(output_policy, dict):
            return False
        content_sha256 = output.get("content_sha256")
        timeline_version_id = version_snapshot.get("timeline_version_id")
        version_id = version_snapshot.get("version_id")
        if not all(isinstance(value, str) and value for value in (content_sha256, timeline_version_id, version_id)):
            return False
        expected = canonical_sha256(
            {
                "version_id": version_id,
                "content_sha256": content_sha256,
                "timeline_version_id": timeline_version_id,
                "output_policy": output_policy,
            }
        )
        return artifact_digest == expected
