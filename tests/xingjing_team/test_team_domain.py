from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_team import (
    Actor,
    EnterpriseProfileUpdate,
    Forbidden,
    IdempotencyConflict,
    InvitationState,
    InvitationStateError,
    RoleUpdate,
    SeatLimitReached,
    SqliteTeamRepository,
    TeamService,
    VersionConflict,
)

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
OWNER = Actor("owner-1", "owner@example.com")
ADMIN = Actor("admin-1", "admin@example.com")


@pytest.fixture
def service(tmp_path):
    repository = SqliteTeamRepository(tmp_path / "team.sqlite3")
    repository.create_workspace("ws-1", seat_limit=2, owner=OWNER)
    return TeamService(repository, clock=lambda: NOW)


def test_invitation_can_be_accepted_once_and_creates_a_seated_member(service):
    invitation = service.invite_member(
        "ws-1",
        OWNER,
        email="new@example.com",
        role_id="member",
        expires_at=NOW + timedelta(days=7),
        request_id="req-invite",
        idempotency_key="invite-1",
    )

    accepted = service.accept_invitation(
        invitation.id,
        Actor("user-2", "new@example.com"),
        request_id="req-accept",
        idempotency_key="accept-1",
    )

    assert accepted.state is InvitationState.ACCEPTED
    assert accepted.accepted_by == "user-2"
    assert service.seat_usage("ws-1", OWNER).occupied == 2
    assert (
        service.accept_invitation(
            invitation.id,
            Actor("user-2", "new@example.com"),
            request_id="req-accept-replay",
            idempotency_key="accept-1",
        )
        == accepted
    )
    with pytest.raises(InvitationStateError):
        service.accept_invitation(
            invitation.id,
            Actor("user-3", "new@example.com"),
            request_id="req-again",
            idempotency_key="accept-2",
        )


def test_pending_invitation_expires_and_cannot_be_accepted(service):
    invitation = service.invite_member(
        "ws-1",
        OWNER,
        email="late@example.com",
        role_id="member",
        expires_at=NOW,
        request_id="req-invite",
        idempotency_key="invite-expired",
    )

    current = service.get_invitation(invitation.id, OWNER)

    assert current.state is InvitationState.EXPIRED
    with pytest.raises(InvitationStateError):
        service.accept_invitation(
            invitation.id,
            Actor("user-late", "late@example.com"),
            request_id="req-late",
            idempotency_key="accept-late",
        )


def test_pending_invitation_can_be_revoked_idempotently(service):
    invitation = service.invite_member(
        "ws-1",
        OWNER,
        email="revoked@example.com",
        role_id="member",
        expires_at=NOW + timedelta(days=1),
        request_id="req-invite",
        idempotency_key="invite-revoke",
    )

    first = service.revoke_invitation(invitation.id, OWNER, request_id="req-revoke", idempotency_key="revoke-1")
    replay = service.revoke_invitation(invitation.id, OWNER, request_id="req-replay", idempotency_key="revoke-1")

    assert first.state is InvitationState.REVOKED
    assert replay == first
    assert len(service.audit_events("ws-1", OWNER, request_id="req-revoke")) == 1


def test_idempotency_key_cannot_be_reused_for_a_different_command(service):
    service.invite_member(
        "ws-1",
        OWNER,
        email="first@example.com",
        role_id="member",
        expires_at=NOW + timedelta(days=1),
        request_id="req-first",
        idempotency_key="same-key",
    )

    with pytest.raises(IdempotencyConflict):
        service.invite_member(
            "ws-1",
            OWNER,
            email="second@example.com",
            role_id="member",
            expires_at=NOW + timedelta(days=1),
            request_id="req-second",
            idempotency_key="same-key",
        )


def test_idempotency_key_cannot_be_reused_by_a_different_actor(service):
    service.add_member_for_provisioning("ws-1", ADMIN.id, ADMIN.email, "admin")
    invitation = service.invite_member(
        "ws-1",
        OWNER,
        email="same@example.com",
        role_id="member",
        expires_at=NOW + timedelta(days=1),
        request_id="req-owner",
        idempotency_key="actor-key",
    )

    assert (
        service.invite_member(
            "ws-1",
            OWNER,
            email="same@example.com",
            role_id="member",
            expires_at=NOW + timedelta(days=1),
            request_id="req-owner-replay",
            idempotency_key="actor-key",
        )
        == invitation
    )
    with pytest.raises(IdempotencyConflict):
        service.invite_member(
            "ws-1",
            ADMIN,
            email="same@example.com",
            role_id="member",
            expires_at=NOW + timedelta(days=1),
            request_id="req-admin",
            idempotency_key="actor-key",
        )


def test_concurrent_accepts_cannot_oversubscribe_seats(tmp_path):
    repository = SqliteTeamRepository(tmp_path / "concurrent.sqlite3")
    repository.create_workspace("ws-1", seat_limit=2, owner=OWNER)
    service = TeamService(repository, clock=lambda: NOW)
    invitations = [
        service.invite_member(
            "ws-1",
            OWNER,
            email=f"user-{index}@example.com",
            role_id="member",
            expires_at=NOW + timedelta(days=1),
            request_id=f"req-invite-{index}",
            idempotency_key=f"invite-{index}",
        )
        for index in range(2)
    ]

    def accept(index):
        return service.accept_invitation(
            invitations[index].id,
            Actor(f"user-{index}", f"user-{index}@example.com"),
            request_id=f"req-accept-{index}",
            idempotency_key=f"accept-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.exception() for future in [pool.submit(accept, 0), pool.submit(accept, 1)]]

    assert sum(error is None for error in outcomes) == 1
    assert sum(isinstance(error, SeatLimitReached) for error in outcomes) == 1
    assert service.seat_usage("ws-1", OWNER).occupied == 2


def test_permission_change_is_visible_on_the_next_authorization_check(service):
    role = service.update_role(
        "ws-1",
        OWNER,
        "editor",
        RoleUpdate(name="编辑", permissions=frozenset({"project.view"})),
        expected_version=0,
        request_id="req-role-1",
        idempotency_key="role-1",
    )
    service.add_member_for_provisioning("ws-1", "editor-1", "editor@example.com", role.id)

    assert service.authorize("ws-1", "editor-1", "project.view").permission_version == 2

    updated = service.update_role(
        "ws-1",
        OWNER,
        role.id,
        RoleUpdate(name="编辑", permissions=frozenset()),
        expected_version=role.version,
        request_id="req-role-2",
        idempotency_key="role-2",
    )

    assert updated.version == 2
    with pytest.raises(Forbidden):
        service.authorize("ws-1", "editor-1", "project.view")
    with pytest.raises(VersionConflict):
        service.update_role(
            "ws-1",
            OWNER,
            role.id,
            RoleUpdate(name="旧写入", permissions=frozenset({"project.view"})),
            expected_version=role.version,
            request_id="req-stale",
            idempotency_key="role-stale",
        )


def test_enterprise_profile_uses_permission_versioning_idempotency_and_audit(service):
    first = service.update_enterprise_profile(
        "ws-1",
        OWNER,
        EnterpriseProfileUpdate(
            legal_name="星镜科技",
            invoice_title="星镜科技有限公司",
            data_retention_days=365,
            security_policy={"mfa_required": True},
            dedicated_deployment={"requested": False},
        ),
        expected_version=0,
        request_id="req-enterprise",
        idempotency_key="enterprise-1",
    )
    replay = service.update_enterprise_profile(
        "ws-1",
        OWNER,
        EnterpriseProfileUpdate(
            legal_name="星镜科技",
            invoice_title="星镜科技有限公司",
            data_retention_days=365,
            security_policy={"mfa_required": True},
            dedicated_deployment={"requested": False},
        ),
        expected_version=0,
        request_id="req-replay",
        idempotency_key="enterprise-1",
    )

    assert replay == first
    assert first.version == 1
    assert service.enterprise_profile("ws-1", OWNER) == first
    assert len(service.audit_events("ws-1", OWNER, request_id="req-enterprise")) == 1
    with pytest.raises(VersionConflict):
        service.update_enterprise_profile(
            "ws-1",
            OWNER,
            EnterpriseProfileUpdate(legal_name="冲突企业"),
            expected_version=0,
            request_id="req-conflict",
            idempotency_key="enterprise-2",
        )


def test_server_side_permissions_protect_reads_and_writes(service):
    outsider = Actor("outsider", "outsider@example.com")

    with pytest.raises(Forbidden):
        service.seat_usage("ws-1", outsider)
    with pytest.raises(Forbidden):
        service.update_enterprise_profile(
            "ws-1",
            outsider,
            EnterpriseProfileUpdate(legal_name="越权"),
            expected_version=0,
            request_id="req-forbidden",
            idempotency_key="forbidden-1",
        )
