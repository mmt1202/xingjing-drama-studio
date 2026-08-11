from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from server.xingjing_assets.contracts import (
    Asset,
    AssetKind,
    AssetSource,
    AssetVersion,
    FrozenScriptSnapshotRef,
)
from server.xingjing_assets.errors import VersionConflict
from server.xingjing_assets_persistence.repository import (
    AssetAuditEvent,
    AssetScope,
    Base,
    SqlAlchemyAssetRepository,
)

NOW = datetime(2026, 7, 16, tzinfo=UTC)
DIGEST = hashlib.sha256(b"fixture").hexdigest()


@pytest.fixture
def repository() -> SqlAlchemyAssetRepository:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return SqlAlchemyAssetRepository(sessionmaker(engine, expire_on_commit=False))


def scope(tenant_id: str = "tenant-a", workspace_id: str = "workspace-a") -> AssetScope:
    return AssetScope(tenant_id=tenant_id, workspace_id=workspace_id)


def asset(
    *, version_id: str = "version-1", revision: int = 1, versions: tuple[AssetVersion, ...] | None = None
) -> Asset:
    initial = AssetVersion(version_id, 1, {"prompt": "first"}, DIGEST, "initial", "2026-07-16T00:00:00Z")
    return Asset(
        "asset-1",
        "workspace-a",
        "project-a",
        AssetKind.CHARACTER,
        "主角",
        AssetSource("generated", "job-1", "sources/job-1.json", DIGEST),
        FrozenScriptSnapshotRef("snapshot-1", "workspace-a", "project-a", "script-1", DIGEST, "2026-07-16T00:00:00Z"),
        version_id,
        revision,
        versions or (initial,),
    )


def audit(action: str = "asset.created") -> AssetAuditEvent:
    return AssetAuditEvent(
        "request-1", "actor-1", action, "asset-1", "success", NOW, {"before": None}, {"after": "asset-1"}
    )


def test_commit_round_trips_an_immutable_version_and_writes_audit(repository: SqlAlchemyAssetRepository) -> None:
    saved, replayed = repository.commit(
        scope(), asset(), expected_revision=None, idempotency_key="create-1", fingerprint="fp-1", audit=audit()
    )

    assert replayed is False
    assert saved == asset()
    assert repository.get(scope(), "asset-1") == asset()
    assert repository.list_audit(scope(), asset_id="asset-1") == (audit(),)


def test_scope_hides_cross_tenant_assets(repository: SqlAlchemyAssetRepository) -> None:
    repository.commit(
        scope(), asset(), expected_revision=None, idempotency_key="create-1", fingerprint="fp-1", audit=audit()
    )

    assert repository.get(scope("tenant-b"), "asset-1") is None
    assert repository.list_audit(scope("tenant-b"), asset_id="asset-1") == ()


def test_stale_cas_does_not_overwrite_or_append_a_version(repository: SqlAlchemyAssetRepository) -> None:
    original = asset()
    repository.commit(
        scope(), original, expected_revision=None, idempotency_key="create-1", fingerprint="fp-1", audit=audit()
    )
    next_version = AssetVersion(
        "version-2", 2, {"prompt": "second"}, DIGEST, "revise", "2026-07-16T00:01:00Z", "version-1"
    )
    winner = asset(version_id="version-2", revision=2, versions=(original.versions[0], next_version))
    repository.commit(
        scope(),
        winner,
        expected_revision=1,
        idempotency_key="revise-1",
        fingerprint="fp-2",
        audit=audit("asset.revised"),
    )

    with pytest.raises(VersionConflict):
        repository.commit(
            scope(),
            winner,
            expected_revision=1,
            idempotency_key="revise-stale",
            fingerprint="fp-3",
            audit=audit("asset.revised"),
        )

    assert repository.get(scope(), "asset-1") == winner


def test_same_idempotency_key_replays_but_rejects_a_changed_fingerprint(repository: SqlAlchemyAssetRepository) -> None:
    first = repository.commit(
        scope(), asset(), expected_revision=None, idempotency_key="create-1", fingerprint="fp-1", audit=audit()
    )
    replay = repository.commit(
        scope(), asset(), expected_revision=None, idempotency_key="create-1", fingerprint="fp-1", audit=audit()
    )

    assert first == (asset(), False)
    assert replay == (asset(), True)
    with pytest.raises(VersionConflict, match="IDEMPOTENCY_CONFLICT"):
        repository.commit(
            scope(), asset(), expected_revision=None, idempotency_key="create-1", fingerprint="different", audit=audit()
        )


def test_schema_compiles_for_postgresql_without_sqlite_only_features() -> None:
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in Base.metadata.sorted_tables
    )

    assert "CREATE TABLE xingjing_asset_records" in ddl
    assert "CREATE TABLE xingjing_asset_versions" in ddl
    assert "CREATE TABLE xingjing_asset_audit_events" in ddl
    assert "AUTOINCREMENT" not in ddl
