from __future__ import annotations

from datetime import timedelta

from server.xingjing_commercial import Actor

from .support import NOW, command_headers, make_harness


def test_admin_can_publish_a_real_order_through_the_http_surface() -> None:
    admin = Actor.admin(
        "commercial-admin",
        {"admin.commercial.view", "admin.commercial.manage"},
        {"workspace-owner"},
    )
    harness = make_harness(admin)

    response = harness.client.post(
        "/api/v1/admin/commercial-orders",
        json={
            "owner_workspace_id": "workspace-owner",
            "title": "品牌短剧",
            "requirements": "交付可验收的正式成片",
            "budget_minor": 20_000,
            "currency": "CNY",
            "milestones": [
                {
                    "title": "成片交付",
                    "amount_minor": 20_000,
                    "acceptance_criteria": "客户书面确认",
                    "due_at": (NOW + timedelta(days=7)).isoformat(),
                }
            ],
        },
        headers={"Idempotency-Key": "publish-http", "X-Request-ID": "publish-http"},
    )

    assert response.status_code == 200
    assert response.headers["etag"] == '"1"'
    assert response.json()["owner_workspace_id"] == "workspace-owner"
    assert response.json()["milestones"][0]["acceptance_criteria"] == "客户书面确认"


def test_list_detail_and_milestones_filter_tenant_scope_without_leaking_objects() -> None:
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    harness = make_harness(owner)
    order = harness.publish(owner)

    listed = harness.client.get("/api/v1/commercial-orders")
    detail = harness.client.get(f"/api/v1/commercial-orders/{order.id}")
    milestones = harness.client.get(f"/api/v1/commercial-orders/{order.id}/milestones")

    assert listed.status_code == 200
    assert listed.json() == {"items": [order.to_dict()], "limit": 50, "offset": 0, "total": 1}
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"1"'
    assert milestones.json() == {"items": [order.milestones[0].to_dict()], "total": 1}

    harness.use_actor(Actor.member("intruder", "workspace-other", {"commercial.view"}))
    hidden_list = harness.client.get("/api/v1/commercial-orders")
    hidden_detail = harness.client.get(f"/api/v1/commercial-orders/{order.id}")
    assert hidden_list.json()["items"] == []
    assert hidden_detail.status_code == 404
    assert order.title not in hidden_detail.text

    harness.use_actor(Actor.member("no-view", "workspace-owner", set()))
    assert harness.client.get("/api/v1/commercial-orders").status_code == 403

    harness.use_actor(Actor.admin("admin", {"admin.commercial.view"}, {"workspace-owner"}))
    admin_list = harness.client.get("/api/v1/admin/commercial-orders")
    assert admin_list.status_code == 200
    assert admin_list.json()["items"][0]["id"] == order.id


def test_quote_contract_idempotency_amount_confirmation_and_if_match_conflicts() -> None:
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    contractor = Actor.member("contractor", "workspace-contractor", {"commercial.manage", "commercial.view"})
    harness = make_harness(owner)
    order = harness.publish(owner)
    harness.use_actor(contractor)
    quote_body = {
        "amount_minor": 9_000,
        "currency": "CNY",
        "proposal": "七日内交付",
        "valid_until": (NOW + timedelta(days=2)).isoformat(),
    }

    quoted = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes",
        json=quote_body,
        headers=command_headers(order.version, "quote-1"),
    )
    replay = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes",
        json=quote_body,
        headers=command_headers(order.version, "quote-1"),
    )
    conflict = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes",
        json={**quote_body, "proposal": "另一份方案"},
        headers=command_headers(order.version, "quote-1"),
    )

    assert quoted.status_code == 200
    assert quoted.headers["etag"] == '"2"'
    assert replay.json() == quoted.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    quote_id = quoted.json()["quotes"][0]["id"]
    harness.use_actor(owner)
    stale = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes/{quote_id}/accept",
        headers=command_headers(1, "accept-stale"),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"

    awarded = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes/{quote_id}/accept",
        headers=command_headers(2, "accept"),
    )
    wrong_amount = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/contracts",
        json={
            "content_ref": "object://contracts/v1.pdf",
            "content_digest": "sha256:contract",
            "amount_minor": 8_000,
        },
        headers=command_headers(awarded.json()["version"], "contract-wrong"),
    )
    contracted = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/contracts",
        json={
            "content_ref": "object://contracts/v1.pdf",
            "content_digest": "sha256:contract",
            "amount_minor": 9_000,
        },
        headers=command_headers(awarded.json()["version"], "contract"),
    )

    assert awarded.status_code == 200
    assert wrong_amount.status_code == 400
    assert wrong_amount.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert contracted.status_code == 200
    assert contracted.json()["contract_versions"][0]["amount_minor"] == 9_000


def test_delivery_return_and_acceptance_complete_the_http_to_domain_loop() -> None:
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    contractor = Actor.member("contractor", "workspace-contractor", {"commercial.manage", "commercial.view"})
    harness = make_harness(owner)
    contracted = harness.contracted(owner, contractor)
    milestone_id = contracted.milestones[0].id
    harness.use_actor(contractor)

    delivered_v1 = harness.client.post(
        f"/api/v1/commercial-orders/{contracted.id}/milestones/{milestone_id}/deliveries",
        json={"artifact_version_id": "video-v1", "artifact_digest": "sha256:v1", "note": "首版"},
        headers=command_headers(contracted.version, "delivery-v1"),
    )
    harness.use_actor(owner)
    returned = harness.client.post(
        f"/api/v1/commercial-orders/{contracted.id}/deliveries/{delivered_v1.json()['deliveries'][0]['id']}/return",
        json={"reason": "片尾标识不符合合同"},
        headers=command_headers(delivered_v1.json()["version"], "return-v1"),
    )
    harness.use_actor(contractor)
    delivered_v2 = harness.client.post(
        f"/api/v1/commercial-orders/{contracted.id}/milestones/{milestone_id}/deliveries",
        json={"artifact_version_id": "video-v2", "artifact_digest": "sha256:v2", "note": "修订版"},
        headers=command_headers(returned.json()["version"], "delivery-v2"),
    )
    harness.use_actor(owner)
    accepted = harness.client.post(
        f"/api/v1/commercial-orders/{contracted.id}/deliveries/{delivered_v2.json()['deliveries'][1]['id']}/accept",
        json={"evidence_ref": "object://acceptance/signature.json"},
        headers=command_headers(delivered_v2.json()["version"], "accept-v2"),
    )

    assert delivered_v1.status_code == 200
    assert returned.json()["deliveries"][0]["status"] == "returned"
    assert accepted.status_code == 200
    assert accepted.json()["milestones"][0]["status"] == "accepted"
    assert accepted.json()["settlements"][0]["status"] == "ready"
    assert harness.service.get_order(owner, owner.workspace_id, contracted.id).version == accepted.json()["version"]


def test_dispute_blocks_payment_and_accounting_success_gates_all_settlement_changes() -> None:
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    contractor = Actor.member("contractor", "workspace-contractor", {"commercial.manage", "commercial.view"})
    harness = make_harness(owner)
    accepted = harness.accepted(owner, contractor)
    settlement = accepted.settlements[0]
    milestone_id = accepted.milestones[0].id

    wrong_amount = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/settlements/{settlement.id}/pay",
        json={"amount_minor": 9_999, "currency": "CNY"},
        headers=command_headers(accepted.version, "wrong-amount"),
    )
    assert wrong_amount.status_code == 409
    assert wrong_amount.json()["detail"]["code"] == "AMOUNT_CONFIRMATION_MISMATCH"
    assert harness.accounting.calls == []

    harness.use_actor(contractor)
    disputed = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/milestones/{milestone_id}/disputes",
        json={"kind": "acceptance", "reason": "签收证据主体不一致"},
        headers=command_headers(accepted.version, "dispute"),
    )
    harness.use_actor(owner)
    blocked = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/settlements/{settlement.id}/pay",
        json={"amount_minor": 10_000, "currency": "CNY"},
        headers=command_headers(disputed.json()["version"], "blocked-pay"),
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "SETTLEMENT_BLOCKED_BY_DISPUTE"
    assert harness.accounting.effective_operations("pay") == ()

    harness.accounting.reject_next("freeze")
    rejected_freeze = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/settlements/{settlement.id}/freeze",
        json={"amount_minor": 10_000, "currency": "CNY"},
        headers=command_headers(disputed.json()["version"], "freeze"),
    )
    frozen = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/settlements/{settlement.id}/freeze",
        json={"amount_minor": 10_000, "currency": "CNY"},
        headers=command_headers(disputed.json()["version"], "freeze"),
    )
    assert rejected_freeze.status_code == 502
    assert rejected_freeze.json()["detail"]["code"] == "ACCOUNTING_REJECTED"
    assert frozen.json()["settlements"][0]["status"] == "frozen"

    admin = Actor.admin(
        "finance-admin",
        {"admin.commercial.manage", "admin.commercial.view"},
        {"workspace-owner"},
    )
    harness.use_actor(admin)
    dispute_id = frozen.json()["disputes"][0]["id"]
    resolved = harness.client.post(
        f"/api/v1/admin/commercial-orders/{accepted.id}/disputes/{dispute_id}/resolve",
        json={"resolution": "证据已补正"},
        headers=command_headers(frozen.json()["version"], "resolve"),
    )
    harness.use_actor(owner)
    resumed = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/settlements/{settlement.id}/resume",
        json={"amount_minor": 10_000, "currency": "CNY"},
        headers=command_headers(resolved.json()["version"], "resume"),
    )

    harness.accounting.reject_next("pay")
    rejected_pay = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/settlements/{settlement.id}/pay",
        json={"amount_minor": 10_000, "currency": "CNY"},
        headers=command_headers(resumed.json()["version"], "pay"),
    )
    paid = harness.client.post(
        f"/api/v1/commercial-orders/{accepted.id}/settlements/{settlement.id}/pay",
        json={"amount_minor": 10_000, "currency": "CNY"},
        headers=command_headers(resumed.json()["version"], "pay"),
    )

    assert resumed.json()["settlements"][0]["status"] == "ready"
    assert rejected_pay.status_code == 502
    assert paid.status_code == 200
    assert paid.json()["settlements"][0]["status"] == "paid"
    assert len(harness.accounting.effective_operations("pay")) == 1


def test_write_endpoints_require_idempotency_key_and_valid_if_match() -> None:
    owner = Actor.member("owner", "workspace-owner", {"commercial.manage", "commercial.view"})
    contractor = Actor.member("contractor", "workspace-contractor", {"commercial.manage", "commercial.view"})
    harness = make_harness(contractor)
    order = harness.publish(owner)
    body = {
        "amount_minor": 9_000,
        "currency": "CNY",
        "proposal": "方案",
        "valid_until": (NOW + timedelta(days=2)).isoformat(),
    }

    missing_key = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes",
        json=body,
        headers={"If-Match": '"1"'},
    )
    missing_match = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes",
        json=body,
        headers={"Idempotency-Key": "quote"},
    )
    invalid_match = harness.client.post(
        f"/api/v1/commercial-orders/{order.id}/quotes",
        json=body,
        headers={"Idempotency-Key": "quote", "If-Match": "not-an-etag"},
    )

    assert missing_key.status_code == 400
    assert missing_key.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert missing_match.status_code == 428
    assert missing_match.json()["detail"]["code"] == "IF_MATCH_REQUIRED"
    assert invalid_match.status_code == 400
    assert invalid_match.json()["detail"]["code"] == "INVALID_IF_MATCH"
