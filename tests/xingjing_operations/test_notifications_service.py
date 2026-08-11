from datetime import UTC, datetime

from server.xingjing_operations import (
    Actor,
    DeliveryReceipt,
    InMemoryNotificationRepository,
    NotificationService,
)
from server.xingjing_operations.runtime import _notification_records

NOW = datetime(2026, 7, 15, tzinfo=UTC)
ACTOR = Actor("tenant-a", "operator-1", "req-notify")


def test_template_versions_are_immutable_and_rule_pins_published_version():
    service = NotificationService(InMemoryNotificationRepository(), clock=lambda: NOW)
    v1 = service.create_template(ACTOR, "m-1", "task_failed", "email", "Task {{task_id}} failed")
    published = service.publish_template(ACTOR, "m-2", v1.id, v1.version)
    v2 = service.revise_template(ACTOR, "m-3", v1.id, published.version, "Task {{task_id}} failed: {{reason}}")
    rule = service.create_rule(ACTOR, "r-1", "task.failed", v1.id, published.template_version, ("owner",), enabled=True)

    assert v2.template_version == 2
    assert rule.template_version == 1
    assert service.get_template(ACTOR, v1.id, 1).body == "Task {{task_id}} failed"


def test_delivery_is_idempotent_and_only_provider_receipt_can_mark_delivered():
    calls = []

    class Provider:
        def send(self, message):
            calls.append(message)
            return DeliveryReceipt("accepted", "provider-42")

    service = NotificationService(InMemoryNotificationRepository(), providers={"email": Provider()}, clock=lambda: NOW)
    template = service.create_template(ACTOR, "m-1", "task_failed", "email", "Task {{task_id}} failed")
    template = service.publish_template(ACTOR, "m-2", template.id, template.version)
    rule = service.create_rule(ACTOR, "r-1", "task.failed", template.id, 1, ("owner",), True)

    first = service.deliver(ACTOR, "delivery-1", rule.id, {"task_id": "task-9"}, "user-7")
    replay = service.deliver(ACTOR, "delivery-1", rule.id, {"task_id": "task-9"}, "user-7")

    assert first.status == "accepted"
    assert first.provider_reference == "provider-42"
    assert replay == first
    assert len(calls) == 1


def test_missing_provider_records_pending_not_delivered():
    service = NotificationService(InMemoryNotificationRepository(), clock=lambda: NOW)
    template = service.create_template(ACTOR, "m-1", "task_failed", "sms", "Task {{task_id}} failed")
    template = service.publish_template(ACTOR, "m-2", template.id, template.version)
    rule = service.create_rule(ACTOR, "r-1", "task.failed", template.id, 1, ("owner",), True)

    delivery = service.deliver(ACTOR, "delivery-1", rule.id, {"task_id": "task-9"}, "user-7")

    assert delivery.status == "pending"
    assert delivery.provider_reference is None


def test_admin_broadcast_creates_scoped_deduplicated_inbox_records():
    records = _notification_records(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        broadcast_id="broadcast-1",
        recipients=["user-1", "user-2"],
        category="operations",
        title="维护通知",
        body="今晚 23:00 维护",
        created_at=NOW,
    )

    assert [record["recipient_id"] for record in records] == ["user-1", "user-2"]
    assert {record["dedupe_key"] for record in records} == {
        "admin:broadcast-1:user-1",
        "admin:broadcast-1:user-2",
    }
    assert all(record["tenant_id"] == "tenant-a" for record in records)
