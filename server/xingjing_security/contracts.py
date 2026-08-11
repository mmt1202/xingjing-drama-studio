from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Protocol


class SecurityViolation(ValueError):
    """A stable, non-sensitive security contract rejection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RequestContext:
    method: str
    path: str
    is_tls: bool
    request_id: str
    content_length: int = 0
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class RequestSecurityPolicy:
    max_body_bytes: int = 16 * 1024 * 1024
    mutation_methods: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def validate(self, request: RequestContext) -> None:
        method = request.method.upper()
        if method in self.mutation_methods and not request.is_tls:
            raise SecurityViolation("TLS_REQUIRED")
        if not request.request_id.strip():
            raise SecurityViolation("REQUEST_ID_REQUIRED")
        if request.content_length < 0 or request.content_length > self.max_body_bytes:
            raise SecurityViolation("REQUEST_BODY_TOO_LARGE")
        if "\r" in request.path or "\n" in request.path:
            raise SecurityViolation("INVALID_REQUEST_PATH")


@dataclass(frozen=True, slots=True)
class SecretReference:
    uri: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"(?:kms|vault|secret)://[A-Za-z0-9][A-Za-z0-9._/-]*", self.uri):
            raise SecurityViolation("INVALID_SECRET_REFERENCE")


class KeyState(StrEnum):
    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class KeyDescriptor:
    key_id: str
    secret_reference: SecretReference
    state: KeyState
    accept_until: datetime | None = None


class ReplayStore(Protocol):
    def claim(self, key_id: str, nonce: str, expires_at: datetime, now: datetime) -> bool: ...


@dataclass(slots=True)
class InMemoryReplayStore:
    """Development adapter; production must provide a shared atomic store."""

    _claims: dict[tuple[str, str], datetime] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def claim(self, key_id: str, nonce: str, expires_at: datetime, now: datetime) -> bool:
        with self._lock:
            self._claims = {key: expiry for key, expiry in self._claims.items() if expiry >= now}
            claim_key = (key_id, nonce)
            if claim_key in self._claims:
                return False
            self._claims[claim_key] = expires_at
            return True


SecretResolver = Callable[[SecretReference], bytes]


class HmacRequestSigner:
    def __init__(
        self,
        keys: Mapping[str, KeyDescriptor],
        resolve_secret: SecretResolver,
        replay_store: ReplayStore,
        *,
        replay_window: timedelta,
    ) -> None:
        self._keys = dict(keys)
        self._resolve_secret = resolve_secret
        self._replay_store = replay_store
        self._replay_window = replay_window

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            raise SecurityViolation("TIMEZONE_REQUIRED")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _canonical(self, method: str, path: str, body: bytes, timestamp: datetime, nonce: str) -> bytes:
        body_digest = hashlib.sha256(body).hexdigest()
        return "\n".join((method.upper(), path, body_digest, self._timestamp(timestamp), nonce)).encode()

    def _descriptor(self, key_id: str, now: datetime | None = None) -> KeyDescriptor:
        descriptor = self._keys.get(key_id)
        if descriptor is None or descriptor.state is KeyState.REVOKED:
            raise SecurityViolation("SIGNING_KEY_REJECTED")
        if now is not None and descriptor.state is KeyState.RETIRING:
            if descriptor.accept_until is None or now > descriptor.accept_until:
                raise SecurityViolation("SIGNING_KEY_EXPIRED")
        return descriptor

    def sign(self, key_id: str, method: str, path: str, body: bytes, timestamp: datetime, nonce: str) -> str:
        descriptor = self._descriptor(key_id)
        secret = self._resolve_secret(descriptor.secret_reference)
        return hmac.new(secret, self._canonical(method, path, body, timestamp, nonce), hashlib.sha256).hexdigest()

    def verify(
        self,
        key_id: str,
        method: str,
        path: str,
        body: bytes,
        timestamp: datetime,
        nonce: str,
        signature: str,
        *,
        now: datetime,
    ) -> None:
        if timestamp.tzinfo is None or now.tzinfo is None:
            raise SecurityViolation("TIMEZONE_REQUIRED")
        if abs(now - timestamp) > self._replay_window:
            raise SecurityViolation("SIGNATURE_OUTSIDE_REPLAY_WINDOW")
        descriptor = self._descriptor(key_id, now)
        expected = hmac.new(
            self._resolve_secret(descriptor.secret_reference),
            self._canonical(method, path, body, timestamp, nonce),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise SecurityViolation("INVALID_SIGNATURE")
        if not nonce or not self._replay_store.claim(key_id, nonce, timestamp + self._replay_window, now):
            raise SecurityViolation("REPLAY_DETECTED")


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    max_bytes: int
    allowed_media_types: frozenset[str]
    max_compression_ratio: int = 100
    max_archive_entries: int = 10_000

    def validate(
        self,
        *,
        size: int,
        media_type: str,
        filename: str,
        detected_media_type: str | None = None,
        expanded_size: int | None = None,
        archive_entries: int | None = None,
    ) -> None:
        if size < 0 or size > self.max_bytes:
            raise SecurityViolation("UPLOAD_TOO_LARGE")
        if media_type.lower() not in self.allowed_media_types:
            raise SecurityViolation("UPLOAD_MEDIA_TYPE_REJECTED")
        if detected_media_type is not None and detected_media_type.lower() != media_type.lower():
            raise SecurityViolation("UPLOAD_MEDIA_TYPE_MISMATCH")
        self._safe_relative(filename)
        if expanded_size is not None and expanded_size > max(size, 1) * self.max_compression_ratio:
            raise SecurityViolation("ARCHIVE_BOMB_SUSPECTED")
        if archive_entries is not None and archive_entries > self.max_archive_entries:
            raise SecurityViolation("ARCHIVE_ENTRY_LIMIT_EXCEEDED")

    @staticmethod
    def _safe_relative(filename: str) -> PurePosixPath:
        normalized = filename.replace("\\", "/")
        candidate = PurePosixPath(normalized)
        if (
            not normalized
            or candidate.is_absolute()
            or ".." in candidate.parts
            or ":" in normalized
            or "\x00" in normalized
        ):
            raise SecurityViolation("UNSAFE_UPLOAD_PATH")
        return candidate

    def resolve_destination(self, root: Path, filename: str) -> Path:
        relative = self._safe_relative(filename)
        resolved_root = root.resolve()
        destination = (resolved_root / Path(*relative.parts)).resolve()
        if not destination.is_relative_to(resolved_root):
            raise SecurityViolation("UNSAFE_UPLOAD_PATH")
        return destination


class ConfigSource(StrEnum):
    DEFAULT = "default"
    FILE = "file"
    ENVIRONMENT = "environment"
    SECRET_STORE = "secret_store"


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    name: str
    value: str | int | bool | SecretReference
    source: ConfigSource


_SECRET_NAME = re.compile(r"(?:password|secret|token|api[_-]?key|private[_-]?key)", re.IGNORECASE)


def validate_configuration(entries: Sequence[ConfigEntry]) -> dict[str, str | int | bool | SecretReference]:
    result: dict[str, str | int | bool | SecretReference] = {}
    for entry in entries:
        if not entry.name or entry.name in result:
            raise SecurityViolation("INVALID_OR_DUPLICATE_CONFIG_KEY")
        if _SECRET_NAME.search(entry.name) and not isinstance(entry.value, SecretReference):
            raise SecurityViolation("PLAINTEXT_SECRET_FORBIDDEN")
        if isinstance(entry.value, SecretReference) and entry.source is not ConfigSource.SECRET_STORE:
            raise SecurityViolation("SECRET_SOURCE_MISMATCH")
        result[entry.name] = entry.value
    return result


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    component: str
    location: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if self.size_bytes < 0 or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise SecurityViolation("INVALID_BACKUP_ARTIFACT")


@dataclass(frozen=True, slots=True)
class BackupManifest:
    backup_id: str
    created_at: datetime
    artifacts: tuple[BackupArtifact, ...]
    secret_references: tuple[SecretReference, ...]
    digest: str

    @classmethod
    def create(
        cls,
        backup_id: str,
        created_at: datetime,
        artifacts: tuple[BackupArtifact, ...],
        secret_references: tuple[SecretReference, ...] = (),
    ) -> BackupManifest:
        draft = cls(backup_id, created_at, artifacts, secret_references, "")
        return cls(backup_id, created_at, artifacts, secret_references, draft._calculate_digest())

    def _payload(self) -> dict[str, object]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "artifacts": [
                {
                    "component": item.component,
                    "location": item.location,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in sorted(self.artifacts, key=lambda value: (value.component, value.location))
            ],
            "credential_references": sorted(reference.uri for reference in self.secret_references),
        }

    def _calculate_digest(self) -> str:
        encoded = json.dumps(self._payload(), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def verify_digest(self) -> bool:
        return hmac.compare_digest(self.digest, self._calculate_digest())

    def is_complete(self, required_components: set[str]) -> bool:
        return required_components <= {artifact.component for artifact in self.artifacts}

    def to_json(self) -> str:
        payload = self._payload() | {"digest": self.digest}
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


class RecoveryState(StrEnum):
    REQUESTED = "requested"
    VALIDATING = "validating"
    READY = "ready"
    RESTORING = "restoring"
    VERIFYING = "verifying"
    AWAITING_DRILL_CONFIRMATION = "awaiting_drill_confirmation"
    RECOVERED = "recovered"
    FAILED = "failed"


_RECOVERY_TRANSITIONS = {
    RecoveryState.REQUESTED: {RecoveryState.VALIDATING, RecoveryState.FAILED},
    RecoveryState.VALIDATING: {RecoveryState.READY, RecoveryState.FAILED},
    RecoveryState.READY: {RecoveryState.RESTORING, RecoveryState.FAILED},
    RecoveryState.RESTORING: {RecoveryState.VERIFYING, RecoveryState.FAILED},
    RecoveryState.VERIFYING: {RecoveryState.AWAITING_DRILL_CONFIRMATION, RecoveryState.FAILED},
    RecoveryState.AWAITING_DRILL_CONFIRMATION: {RecoveryState.RECOVERED, RecoveryState.FAILED},
    RecoveryState.RECOVERED: set(),
    RecoveryState.FAILED: set(),
}


@dataclass(frozen=True, slots=True)
class RecoveryTransition:
    state: RecoveryState
    evidence: str
    occurred_at: datetime


@dataclass(slots=True)
class RecoveryWorkflow:
    recovery_id: str
    state: RecoveryState = RecoveryState.REQUESTED
    history: list[RecoveryTransition] = field(default_factory=list)
    _drill_evidence: str | None = None

    def advance(self, target: RecoveryState, evidence: str, *, at: datetime) -> None:
        if target not in _RECOVERY_TRANSITIONS[self.state]:
            raise SecurityViolation("INVALID_RECOVERY_TRANSITION")
        if not evidence.strip():
            raise SecurityViolation("RECOVERY_EVIDENCE_REQUIRED")
        if target is RecoveryState.RECOVERED and not self._drill_evidence:
            raise SecurityViolation("DRILL_EVIDENCE_REQUIRED")
        self.state = target
        self.history.append(RecoveryTransition(target, evidence, at))

    def confirm_drill(self, evidence_id: str, *, at: datetime) -> None:
        if self.state is not RecoveryState.AWAITING_DRILL_CONFIRMATION or not evidence_id.strip():
            raise SecurityViolation("DRILL_EVIDENCE_REQUIRED")
        self._drill_evidence = evidence_id
        self.history.append(RecoveryTransition(self.state, f"drill:{evidence_id}", at))


class Dependency(StrEnum):
    DATABASE = "database"
    QUEUE = "queue"
    OBJECT_STORAGE = "object_storage"
    PROVIDER = "provider"
    REGION = "region"


class DependencyState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class DegradationDecision:
    mode: str
    blocked_capabilities: frozenset[str]
    reason_codes: tuple[str, ...]


class DegradationPlanner:
    def decide(self, states: Mapping[Dependency, DependencyState]) -> DegradationDecision:
        down = {dependency for dependency, state in states.items() if state is DependencyState.DOWN}
        if Dependency.DATABASE in down or Dependency.REGION in down:
            return DegradationDecision("unavailable", frozenset({"*"}), tuple(sorted(item.value for item in down)))
        blocked: set[str] = set()
        if Dependency.QUEUE in down:
            blocked.update({"generation.submit", "task.mutate"})
        if Dependency.OBJECT_STORAGE in down:
            blocked.update({"upload", "download", "generation.submit"})
        if Dependency.PROVIDER in down:
            blocked.add("generation.submit")
        mode = "read_only" if blocked else "normal"
        return DegradationDecision(mode, frozenset(blocked), tuple(sorted(item.value for item in down)))


_SENSITIVE_FIELD = re.compile(r"(?:password|secret|token|authorization|cookie|api[_-]?key)", re.IGNORECASE)


def _redact(values: Mapping[str, object] | None) -> dict[str, object]:
    if not values:
        return {}

    def clean(key: str, value: object) -> object:
        if _SENSITIVE_FIELD.search(key):
            return "[REDACTED]"
        if isinstance(value, Mapping):
            return {nested_key: clean(str(nested_key), nested_value) for nested_key, nested_value in value.items()}
        if isinstance(value, list):
            return [clean("", item) for item in value]
        return value

    return {key: clean(key, value) for key, value in values.items()}


@dataclass(frozen=True, slots=True)
class AuditEvent:
    request_id: str
    actor_id: str
    action: str
    object_type: str
    object_id: str
    result: str
    occurred_at: datetime
    before: Mapping[str, object]
    after: Mapping[str, object]

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        actor_id: str,
        action: str,
        object_type: str,
        object_id: str,
        result: str,
        occurred_at: datetime,
        before: Mapping[str, object] | None = None,
        after: Mapping[str, object] | None = None,
    ) -> AuditEvent:
        required = (request_id, actor_id, action, object_type, object_id, result)
        if any(not value.strip() for value in required):
            raise SecurityViolation("AUDIT_FIELD_REQUIRED")
        return cls(
            request_id,
            actor_id,
            action,
            object_type,
            object_id,
            result,
            occurred_at,
            _redact(before),
            _redact(after),
        )
