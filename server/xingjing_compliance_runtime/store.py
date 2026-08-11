from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from server.xingjing_compliance.models import (
    AuditContext,
    ExportAuthorityState,
    ExportManifestContext,
    FormalExportRequest,
    ManualReviewStatus,
    ReviewConclusion,
)

from .contracts import DeliveryRecord, ExportArtifact, ExportAuthorityEvidence
from .errors import AuthorityDataMissing


class SqliteComplianceAuthorityStore:
    """本地 SQLite 持久化适配器，测试和独立运行时均可使用。"""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    async def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS compliance_policies (
                    policy_id TEXT NOT NULL, version TEXT NOT NULL, effective_at TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version)
                );
                CREATE TABLE IF NOT EXISTS compliance_authorizations (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, project_version TEXT NOT NULL,
                    authorization_id TEXT NOT NULL, valid INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, project_version, authorization_id)
                );
                CREATE TABLE IF NOT EXISTS compliance_reviews (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, project_version TEXT NOT NULL,
                    policy_id TEXT NOT NULL, policy_version TEXT NOT NULL, conclusion TEXT NOT NULL,
                    manual_review_status TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, project_version)
                );
                CREATE TABLE IF NOT EXISTS export_snapshots (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, target TEXT NOT NULL,
                    current_project_version TEXT NOT NULL, snapshot_version TEXT NOT NULL,
                    immutable INTEGER NOT NULL, export_permitted INTEGER NOT NULL,
                    aigc_marking_satisfied INTEGER NOT NULL, commercial_export_enabled INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, target)
                );
                CREATE TABLE IF NOT EXISTS billing_settlements (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, project_version TEXT NOT NULL,
                    settled INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, project_version)
                );
                CREATE TABLE IF NOT EXISTS formal_export_deliveries (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    project_version TEXT NOT NULL, policy_version TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, request_id)
                );
                """
            )

    async def save_policy(self, *, policy_id: str, version: str, effective_at: datetime) -> None:
        self._execute(
            "INSERT OR REPLACE INTO compliance_policies VALUES (?, ?, ?)",
            (policy_id, version, effective_at.isoformat()),
        )

    async def delete_policies(self) -> None:
        self._execute("DELETE FROM compliance_policies", ())

    async def save_authorization(
        self, *, tenant_id: str, project_id: str, project_version: str, authorization_id: str, valid: bool
    ) -> None:
        self._execute(
            "INSERT OR REPLACE INTO compliance_authorizations VALUES (?, ?, ?, ?, ?)",
            (tenant_id, project_id, project_version, authorization_id, valid),
        )

    async def save_review(
        self,
        *,
        tenant_id: str,
        project_id: str,
        project_version: str,
        policy_id: str,
        policy_version: str,
        conclusion: ReviewConclusion,
        manual_review_status: ManualReviewStatus,
    ) -> None:
        self._execute(
            "INSERT OR REPLACE INTO compliance_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tenant_id,
                project_id,
                project_version,
                policy_id,
                policy_version,
                conclusion.value,
                manual_review_status.value,
                datetime.now(UTC).isoformat(),
            ),
        )

    async def save_export_snapshot(
        self,
        *,
        tenant_id: str,
        project_id: str,
        current_project_version: str,
        snapshot_version: str,
        target: str,
        immutable: bool,
        export_permitted: bool,
        aigc_marking_satisfied: bool,
        commercial_export_enabled: bool,
    ) -> None:
        self._execute(
            "INSERT OR REPLACE INTO export_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tenant_id,
                project_id,
                target,
                current_project_version,
                snapshot_version,
                immutable,
                export_permitted,
                aigc_marking_satisfied,
                commercial_export_enabled,
            ),
        )

    async def save_billing_settlement(
        self, *, tenant_id: str, project_id: str, project_version: str, settled: bool
    ) -> None:
        self._execute(
            "INSERT OR REPLACE INTO billing_settlements VALUES (?, ?, ?, ?)",
            (tenant_id, project_id, project_version, settled),
        )

    async def load_export_evidence(self, request: FormalExportRequest, audit: AuditContext) -> ExportAuthorityEvidence:
        with self._connect() as connection:
            review = self._one(
                connection,
                "SELECT * FROM compliance_reviews WHERE tenant_id = ? AND project_id = ? AND project_version = ?",
                (audit.tenant_id, request.project_id, request.project_version),
                "review",
            )
            self._one(
                connection,
                "SELECT 1 FROM compliance_policies WHERE policy_id = ? AND version = ?",
                (review["policy_id"], review["policy_version"]),
                "policy",
            )
            snapshot = self._one(
                connection,
                "SELECT * FROM export_snapshots WHERE tenant_id = ? AND project_id = ? AND target = ?",
                (audit.tenant_id, request.project_id, request.target),
                "export snapshot",
            )
            billing = self._one(
                connection,
                "SELECT settled FROM billing_settlements WHERE tenant_id = ? AND project_id = ? AND project_version = ?",
                (audit.tenant_id, request.project_id, request.project_version),
                "billing settlement",
            )
            authorizations = connection.execute(
                "SELECT authorization_id FROM compliance_authorizations WHERE tenant_id = ? AND project_id = ? AND project_version = ? AND valid = 1",
                (audit.tenant_id, request.project_id, request.project_version),
            ).fetchall()
        return ExportAuthorityEvidence(
            state=ExportAuthorityState(
                tenant_id=audit.tenant_id,
                current_project_version=(
                    snapshot["current_project_version"]
                    if snapshot["current_project_version"] == snapshot["snapshot_version"]
                    else f"{snapshot['current_project_version']}!={snapshot['snapshot_version']}"
                ),
                snapshot_is_immutable=bool(snapshot["immutable"]),
                has_export_permission=bool(snapshot["export_permitted"]),
                compliance_conclusion=ReviewConclusion(review["conclusion"]),
                compliance_project_version=review["project_version"],
                manual_review_status=ManualReviewStatus(review["manual_review_status"]),
                authorizations_complete=bool(authorizations),
                billing_settled=bool(billing["settled"]),
                aigc_marking_satisfied=bool(snapshot["aigc_marking_satisfied"]),
                commercial_export_enabled=bool(snapshot["commercial_export_enabled"]),
            ),
            policy_id=review["policy_id"],
            policy_version=review["policy_version"],
            authorization_ids=tuple(row["authorization_id"] for row in authorizations),
            reviewed_at=datetime.fromisoformat(review["reviewed_at"]),
            review_version=1,
        )

    async def record_delivery(
        self,
        manifest: ExportManifestContext,
        evidence: ExportAuthorityEvidence,
        artifact: ExportArtifact | None = None,
    ) -> DeliveryRecord:
        payload = {
            "manifest": {
                "project_id": manifest.project_id,
                "project_version": manifest.project_version,
                "target": manifest.target,
                "tenant_id": manifest.tenant_id,
                "request_id": manifest.request_id,
            },
            "policy": {"id": evidence.policy_id, "version": evidence.policy_version},
            "authorizations": evidence.authorization_ids,
        }
        digest = (
            "sha256:"
            + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        )
        record = DeliveryRecord(
            manifest.tenant_id,
            manifest.project_id,
            manifest.project_version,
            manifest.request_id,
            evidence.policy_version,
            digest,
            manifest.authorized_at,
            target=manifest.target,
            status="succeeded",
            format=None if artifact is None else artifact.format,
            object_key=None if artifact is None else artifact.object_key,
            content_sha256=None if artifact is None else artifact.content_sha256,
            size_bytes=None if artifact is None else artifact.size_bytes,
            download_path=None if artifact is None else artifact.download_path,
        )
        self._execute(
            "INSERT OR IGNORE INTO formal_export_deliveries VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.tenant_id,
                record.project_id,
                record.request_id,
                record.project_version,
                record.policy_version,
                record.manifest_digest,
                record.created_at.isoformat(),
            ),
        )
        return record

    async def list_deliveries(self, *, tenant_id: str, project_id: str) -> tuple[DeliveryRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM formal_export_deliveries WHERE tenant_id = ? AND project_id = ? ORDER BY created_at, request_id",
                (tenant_id, project_id),
            ).fetchall()
        return tuple(
            DeliveryRecord(
                row["tenant_id"],
                row["project_id"],
                row["project_version"],
                row["request_id"],
                row["policy_version"],
                row["manifest_digest"],
                datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def _execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        with self._connect() as connection:
            connection.execute(statement, parameters)

    @staticmethod
    def _one(connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...], name: str) -> sqlite3.Row:
        row = connection.execute(statement, parameters).fetchone()
        if row is None:
            raise AuthorityDataMissing(f"authoritative {name} is missing")
        return row
