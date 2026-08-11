from datetime import UTC, datetime, timedelta

import pytest

from server.xingjing_open_platform.api_keys import ApiKeyService, SecretHasher
from server.xingjing_open_platform.memory import InMemoryOpenPlatformStore
from server.xingjing_open_platform.models import ApiKeyStatus, RequestContext

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def test_created_secret_is_returned_once_and_only_a_digest_is_persisted() -> None:
    store = InMemoryOpenPlatformStore()
    service = ApiKeyService(store, SecretHasher(pepper=b"test-pepper"), clock=lambda: NOW)

    created = service.create(
        context=RequestContext("operator-1", "workspace-a", "request-1"),
        client_id="client-1",
        scopes={"projects:read"},
        expires_at=NOW + timedelta(days=30),
    )

    assert created.secret.startswith("xj_live_")
    persisted = service.get("workspace-a", created.api_key.id)
    assert persisted.status is ApiKeyStatus.ACTIVE
    assert persisted.secret_digest != created.secret
    assert created.secret not in repr(persisted)
    assert service.get("workspace-a", created.api_key.id).display_secret is None


def test_authentication_enforces_workspace_scope_expiry_and_revocation() -> None:
    store = InMemoryOpenPlatformStore()
    service = ApiKeyService(store, SecretHasher(pepper=b"test-pepper"), clock=lambda: NOW)
    created = service.create(
        context=RequestContext("operator-1", "workspace-a", "request-1"),
        client_id="client-1",
        scopes={"projects:read"},
        expires_at=NOW + timedelta(minutes=1),
    )

    principal = service.authenticate(created.secret, workspace_id="workspace-a", required_scope="projects:read")
    assert (principal.client_id, principal.workspace_id) == ("client-1", "workspace-a")

    with pytest.raises(PermissionError):
        service.authenticate(created.secret, workspace_id="workspace-b", required_scope="projects:read")
    with pytest.raises(PermissionError):
        service.authenticate(created.secret, workspace_id="workspace-a", required_scope="projects:write")

    service.revoke(RequestContext("operator-1", "workspace-a", "request-2"), created.api_key.id)
    with pytest.raises(PermissionError):
        service.authenticate(created.secret, workspace_id="workspace-a", required_scope="projects:read")


def test_rotation_atomically_ends_old_credential_and_returns_new_secret_once() -> None:
    service = ApiKeyService(InMemoryOpenPlatformStore(), SecretHasher(pepper=b"test-pepper"), clock=lambda: NOW)
    original = service.create(
        context=RequestContext("operator-1", "workspace-a", "request-1"),
        client_id="client-1",
        scopes={"projects:read"},
        expires_at=NOW + timedelta(days=1),
    )

    replacement = service.rotate(RequestContext("operator-1", "workspace-a", "request-2"), original.api_key.id)

    assert service.get("workspace-a", original.api_key.id).status is ApiKeyStatus.ROTATED
    assert service.get("workspace-a", original.api_key.id).rotated_to_id == replacement.api_key.id
    with pytest.raises(PermissionError):
        service.authenticate(original.secret, workspace_id="workspace-a", required_scope="projects:read")
    assert (
        service.authenticate(replacement.secret, workspace_id="workspace-a", required_scope="projects:read").api_key_id
        == replacement.api_key.id
    )


def test_invalid_scope_and_expiry_are_rejected() -> None:
    service = ApiKeyService(InMemoryOpenPlatformStore(), SecretHasher(pepper=b"test-pepper"), clock=lambda: NOW)
    context = RequestContext("operator-1", "workspace-a", "request-1")

    with pytest.raises(ValueError):
        service.create(context=context, client_id="client-1", scopes=set(), expires_at=None)
    with pytest.raises(ValueError):
        service.create(
            context=context,
            client_id="client-1",
            scopes={"unknown:scope"},
            expires_at=NOW,
        )


def test_expired_key_is_rejected_without_persisting_plaintext_in_diagnostics() -> None:
    current = [NOW]
    service = ApiKeyService(InMemoryOpenPlatformStore(), SecretHasher(pepper=b"test-pepper"), clock=lambda: current[0])
    created = service.create(
        context=RequestContext("operator-1", "workspace-a", "request-1"),
        client_id="client-1",
        scopes={"projects:read"},
        expires_at=NOW + timedelta(seconds=1),
    )
    assert created.secret not in repr(created)
    current[0] = NOW + timedelta(seconds=2)

    with pytest.raises(PermissionError):
        service.authenticate(created.secret, workspace_id="workspace-a", required_scope="projects:read")


def test_key_lifecycle_emits_request_scoped_audit_without_secrets() -> None:
    store = InMemoryOpenPlatformStore()
    service = ApiKeyService(store, SecretHasher(pepper=b"test-pepper"), clock=lambda: NOW, audits=store)
    created = service.create(
        context=RequestContext("operator-1", "workspace-a", "request-create"),
        client_id="client-1",
        scopes={"projects:read"},
        expires_at=NOW + timedelta(days=1),
    )
    service.rotate(RequestContext("operator-1", "workspace-a", "request-rotate"), created.api_key.id)

    assert [(event.event_type, event.request_id) for event in store.audit_events] == [
        ("api_key.created", "request-create"),
        ("api_key.rotated", "request-rotate"),
    ]
    assert created.secret not in repr(store.audit_events)
