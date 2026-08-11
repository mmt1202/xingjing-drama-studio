from datetime import UTC, datetime

import pytest

from server.xingjing_operations import (
    Actor,
    ApprovalRequired,
    InMemoryConfigurationRepository,
    OperationsConfigurationService,
    SelfApprovalForbidden,
)

NOW = datetime(2026, 7, 15, tzinfo=UTC)
AUTHOR = Actor("tenant-a", "author-1", "req-config")
APPROVER = Actor("tenant-a", "approver-1", "req-approve")


def test_configuration_publish_requires_four_eyes_and_supports_rollback_as_new_version():
    service = OperationsConfigurationService(InMemoryConfigurationRepository(), clock=lambda: NOW)
    draft = service.create_draft(AUTHOR, "cfg-1", "homepage", {"banner": "summer"})

    with pytest.raises(SelfApprovalForbidden):
        service.approve(AUTHOR, "cfg-2", draft.id, draft.version)
    with pytest.raises(ApprovalRequired):
        service.publish(AUTHOR, "cfg-3", draft.id, draft.version)

    approved = service.approve(APPROVER, "cfg-4", draft.id, draft.version)
    published = service.publish(AUTHOR, "cfg-5", draft.id, approved.version)
    assert published.status == "published"

    second = service.create_draft(AUTHOR, "cfg-6", "homepage", {"banner": "autumn"})
    second = service.approve(APPROVER, "cfg-7", second.id, second.version)
    second = service.publish(AUTHOR, "cfg-8", second.id, second.version)

    rollback = service.rollback(AUTHOR, "cfg-9", "homepage", second.config_version, published.config_version)
    assert rollback.status == "draft"
    assert rollback.config_version == 3
    assert rollback.payload == {"banner": "summer"}
    assert rollback.rollback_of == 1
