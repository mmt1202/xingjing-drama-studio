from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.xingjing_commercial import AccountingRequest
from server.xingjing_commercial.models import AccountingAction
from server.xingjing_commercial_persistence import (
    CommercialPersistenceBase,
    FailClosedAccountingPort,
    SqlAlchemyCommercialRepository,
    SqlAlchemyDeliveryArtifactPort,
)
from server.xingjing_editing.contracts import canonical_sha256
from server.xingjing_editing_persistence.models import (
    EditingPersistenceBase,
    FinalVideoSelectionRow,
    FinalVideoVersionRow,
    TimelineVersionRow,
)


@pytest.mark.parametrize(
    ("method_name", "action"),
    (
        ("freeze_settlement", "freeze"),
        ("resume_settlement", "resume"),
        ("pay_settlement", "pay"),
    ),
)
def test_unconfigured_accounting_port_fails_closed(method_name: str, action: str) -> None:
    request_time = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    request = AccountingRequest(
        action=cast(AccountingAction, action),
        operation_id=f"operation-{action}",
        owner_workspace_id="workspace-owner",
        payee_workspace_id="workspace-contractor",
        order_id="order-1",
        milestone_id="milestone-1",
        settlement_id="settlement-1",
        amount_minor=10_000,
        currency="CNY",
        requested_by="owner-1",
        request_id="request-1",
    )

    receipt = getattr(FailClosedAccountingPort(now=lambda: request_time), method_name)(request)

    assert receipt.operation_id == f"operation-{action}"
    assert receipt.receipt_id == f"unconfigured:operation-{action}"
    assert receipt.succeeded is False
    assert receipt.occurred_at == request_time
    assert receipt.failure_code == "ACCOUNTING_PORT_NOT_CONFIGURED"


def test_delivery_artifact_port_accepts_only_the_selected_version_with_matching_snapshot_digest() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    CommercialPersistenceBase.metadata.create_all(engine)
    EditingPersistenceBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    created_at = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    output_policy = {"format": "mp4", "width": 1920, "height": 1080}
    version_snapshot = {
        "version_id": "final-version-1",
        "timeline_version_id": "timeline-version-1",
        "output": {"content_sha256": "a" * 64},
    }
    with factory.begin() as session:
        session.add(TimelineVersionRow(
            tenant_id="tenant-1", workspace_id="workspace-contractor", timeline_id="timeline-1",
            version_id="timeline-version-1", project_id="project-1", revision=1, parent_version_id=None,
            snapshot={"output_policy": output_policy}, created_at=created_at,
        ))
        session.add(FinalVideoVersionRow(
            tenant_id="tenant-1", workspace_id="workspace-contractor", final_video_id="final-1",
            version_id="final-version-1", project_id="project-1", timeline_id="timeline-1",
            timeline_version_id="timeline-version-1", render_task_id="render-1", deduplication_key="b" * 64,
            snapshot=version_snapshot, created_at=created_at,
        ))
        session.add(FinalVideoSelectionRow(
            tenant_id="tenant-1", workspace_id="workspace-contractor", project_id="project-1",
            final_video_id="final-1", selected_version_id="final-version-1", revision=1,
            snapshot={"selected_version_id": "final-version-1"}, selected_at=created_at,
        ))
    expected_digest = canonical_sha256({
        "version_id": "final-version-1",
        "content_sha256": "a" * 64,
        "timeline_version_id": "timeline-version-1",
        "output_policy": output_policy,
    })
    verifier = SqlAlchemyDeliveryArtifactPort()
    repository = SqlAlchemyCommercialRepository(factory)

    with repository.atomic():
        assert verifier.verify_selected_artifact(
            workspace_id="workspace-contractor",
            artifact_version_id="final-version-1",
            artifact_digest=expected_digest,
        )
        assert not verifier.verify_selected_artifact(
            workspace_id="workspace-contractor",
            artifact_version_id="final-version-1",
            artifact_digest="sha256:forged",
        )
        assert not verifier.verify_selected_artifact(
            workspace_id="workspace-other",
            artifact_version_id="final-version-1",
            artifact_digest=expected_digest,
        )
    engine.dispose()
