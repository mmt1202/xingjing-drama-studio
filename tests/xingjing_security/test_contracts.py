from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.xingjing_security import (
    AuditEvent,
    BackupArtifact,
    BackupManifest,
    ConfigEntry,
    ConfigSource,
    DegradationPlanner,
    Dependency,
    DependencyState,
    HmacRequestSigner,
    InMemoryReplayStore,
    KeyDescriptor,
    KeyState,
    RecoveryState,
    RecoveryWorkflow,
    RequestContext,
    RequestSecurityPolicy,
    SecretReference,
    SecurityViolation,
    UploadPolicy,
    validate_configuration,
)

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def test_request_policy_rejects_insecure_mutation_and_requires_request_id() -> None:
    policy = RequestSecurityPolicy()

    with pytest.raises(SecurityViolation, match="TLS_REQUIRED"):
        policy.validate(RequestContext(method="POST", path="/api/v1/tasks", is_tls=False, request_id="r-1"))
    with pytest.raises(SecurityViolation, match="REQUEST_ID_REQUIRED"):
        policy.validate(RequestContext(method="GET", path="/api/v1/tasks", is_tls=True, request_id=""))


def test_rotating_signature_accepts_retiring_key_once_inside_replay_window() -> None:
    keys = {
        "old": KeyDescriptor("old", SecretReference("kms://api/old"), KeyState.RETIRING, NOW + timedelta(minutes=5)),
        "new": KeyDescriptor("new", SecretReference("kms://api/new"), KeyState.ACTIVE),
    }
    secrets = {"kms://api/old": b"old-secret", "kms://api/new": b"new-secret"}
    signer = HmacRequestSigner(
        keys,
        lambda ref: secrets[ref.uri],
        InMemoryReplayStore(),
        replay_window=timedelta(minutes=5),
    )
    signature = signer.sign("old", "POST", "/api/v1/jobs", b"{}", NOW, "nonce-1")

    signer.verify("old", "POST", "/api/v1/jobs", b"{}", NOW, "nonce-1", signature, now=NOW)
    with pytest.raises(SecurityViolation, match="REPLAY_DETECTED"):
        signer.verify("old", "POST", "/api/v1/jobs", b"{}", NOW, "nonce-1", signature, now=NOW)


def test_signature_rejects_expired_timestamp_and_expired_retiring_key() -> None:
    descriptor = KeyDescriptor("old", SecretReference("kms://api/old"), KeyState.RETIRING, NOW)
    signer = HmacRequestSigner(
        {"old": descriptor}, lambda _: b"runtime-only", InMemoryReplayStore(), replay_window=timedelta(minutes=5)
    )
    signature = signer.sign("old", "GET", "/api/v1/jobs", b"", NOW, "n")

    with pytest.raises(SecurityViolation, match="SIGNING_KEY_EXPIRED"):
        signer.verify("old", "GET", "/api/v1/jobs", b"", NOW, "n", signature, now=NOW + timedelta(seconds=1))


def test_upload_policy_blocks_oversize_archive_bomb_and_path_escape(tmp_path: Path) -> None:
    policy = UploadPolicy(max_bytes=10, allowed_media_types=frozenset({"image/png"}), max_compression_ratio=20)

    with pytest.raises(SecurityViolation, match="UPLOAD_TOO_LARGE"):
        policy.validate(size=11, media_type="image/png", filename="safe.png")
    with pytest.raises(SecurityViolation, match="ARCHIVE_BOMB_SUSPECTED"):
        policy.validate(size=5, media_type="image/png", filename="safe.png", expanded_size=101)
    with pytest.raises(SecurityViolation, match="UPLOAD_MEDIA_TYPE_MISMATCH"):
        policy.validate(size=5, media_type="image/png", detected_media_type="text/html", filename="safe.png")
    with pytest.raises(SecurityViolation, match="UNSAFE_UPLOAD_PATH"):
        policy.resolve_destination(tmp_path, "../escape.png")


def test_configuration_allows_secret_references_but_rejects_plaintext_secret() -> None:
    entries = [
        ConfigEntry("region", "cn-east-1", ConfigSource.FILE),
        ConfigEntry("database_password", SecretReference("vault://prod/database"), ConfigSource.SECRET_STORE),
    ]
    assert validate_configuration(entries)["database_password"] == SecretReference("vault://prod/database")

    with pytest.raises(SecurityViolation, match="PLAINTEXT_SECRET_FORBIDDEN"):
        validate_configuration([ConfigEntry("api_key", "sk-plaintext", ConfigSource.ENVIRONMENT)])


def test_backup_manifest_digest_is_stable_and_contains_references_not_secrets() -> None:
    artifacts = (
        BackupArtifact("postgres", "db/base.dump", 12, "a" * 64),
        BackupArtifact("objects", "objects/index.json", 5, "b" * 64),
        BackupArtifact("config", "config/export.json", 7, "c" * 64),
    )
    manifest = BackupManifest.create("backup-1", NOW, artifacts, (SecretReference("kms://backup/key"),))

    assert manifest.verify_digest()
    assert manifest.is_complete({"postgres", "objects", "config"})
    assert "secret" not in manifest.to_json().lower()


def test_recovery_cannot_claim_success_without_verified_drill() -> None:
    recovery = RecoveryWorkflow("restore-1")
    recovery.advance(RecoveryState.VALIDATING, "manifest digest accepted", at=NOW)
    recovery.advance(RecoveryState.READY, "restore plan approved", at=NOW)
    recovery.advance(RecoveryState.RESTORING, "restore port completed", at=NOW)
    recovery.advance(RecoveryState.VERIFYING, "integrity checks started", at=NOW)
    recovery.advance(RecoveryState.AWAITING_DRILL_CONFIRMATION, "checks passed", at=NOW)

    with pytest.raises(SecurityViolation, match="DRILL_EVIDENCE_REQUIRED"):
        recovery.advance(RecoveryState.RECOVERED, "looks good", at=NOW)
    recovery.confirm_drill("drill-2026-07-15", at=NOW)
    recovery.advance(RecoveryState.RECOVERED, "operator-confirmed drill", at=NOW)
    assert recovery.state is RecoveryState.RECOVERED


def test_degradation_decision_is_fail_closed_for_database_and_read_only_for_queue() -> None:
    planner = DegradationPlanner()

    assert planner.decide({Dependency.DATABASE: DependencyState.DOWN}).mode == "unavailable"
    queue_decision = planner.decide({Dependency.QUEUE: DependencyState.DOWN})
    assert queue_decision.mode == "read_only"
    assert "generation.submit" in queue_decision.blocked_capabilities


def test_audit_event_redacts_sensitive_values_and_keeps_required_trace_fields() -> None:
    event = AuditEvent.create(
        request_id="req-1",
        actor_id="admin-1",
        action="api_key.rotate",
        object_type="ApiKey",
        object_id="key-1",
        result="succeeded",
        occurred_at=NOW,
        before={"status": "active", "token": "old-secret"},
        after={"status": "retiring", "metadata": {"password": "new-secret"}},
    )

    assert event.before == {"status": "active", "token": "[REDACTED]"}
    assert event.after == {"status": "retiring", "metadata": {"password": "[REDACTED]"}}
    assert event.request_id == "req-1"
