from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from server.xingjing_editing import AuditEvent, AuditOutcome
from server.xingjing_editing_persistence import SqlAlchemyAuditRecorder
from server.xingjing_editing_persistence.models import EditingAuditRow


@pytest.mark.asyncio
async def test_audit_recorder_persists_full_scoped_event(editing_session_factory) -> None:
    recorder = SqlAlchemyAuditRecorder(editing_session_factory)
    event = AuditEvent(
        event_id="audit-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        actor_id="editor-1",
        request_id="request-1",
        action="render.request",
        object_type="render_task",
        object_id="render-1",
        outcome=AuditOutcome.SUCCEEDED,
        occurred_at=datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
        before_sha256="a" * 64,
        after_sha256="b" * 64,
        details={"attempt": 1, "timeline_version_id": "timeline-v1"},
    )

    await recorder.record(event)

    async with editing_session_factory() as session:
        stored = (
            await session.execute(
                select(EditingAuditRow).where(
                    EditingAuditRow.tenant_id == "tenant-1",
                    EditingAuditRow.workspace_id == "workspace-1",
                    EditingAuditRow.event_id == "audit-1",
                )
            )
        ).scalar_one()

    assert stored.actor_id == "editor-1"
    assert stored.request_id == "request-1"
    assert stored.action == "render.request"
    assert stored.outcome == "succeeded"
    assert stored.before_sha256 == "a" * 64
    assert stored.after_sha256 == "b" * 64
    assert stored.details == {"attempt": 1, "timeline_version_id": "timeline-v1"}
