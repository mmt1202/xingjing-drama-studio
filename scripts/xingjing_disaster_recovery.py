from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(component: str, path: Path, root: Path) -> dict[str, object]:
    return {
        "component": component,
        "location": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _manifest_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _run(command: list[str]) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise SystemExit(f"required executable not found: {command[0]}")
    subprocess.run([executable, *command[1:]], check=True)


def backup(args: argparse.Namespace) -> None:
    output_root = Path(args.output).expanduser().resolve()
    backup_id = args.backup_id or f"xj-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    destination = output_root / backup_id
    destination.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, object]] = []

    database_dump = destination / "postgres.dump"
    _run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(database_dump), args.database_url])
    artifacts.append(_artifact("postgresql", database_dump, destination))

    for component, source_text in (("object-storage", args.object_storage), ("configuration", args.configuration)):
        if not source_text:
            continue
        source = Path(source_text).expanduser().resolve()
        if not source.exists():
            raise SystemExit(f"{component} source does not exist: {source}")
        archive = destination / f"{component}.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(source, arcname=source.name, recursive=True)
        artifacts.append(_artifact(component, archive, destination))

    payload: dict[str, object] = {
        "backup_id": backup_id,
        "created_at": datetime.now(UTC).isoformat(),
        "artifacts": sorted(artifacts, key=lambda item: (str(item["component"]), str(item["location"]))),
        "credential_references": [],
    }
    manifest = payload | {"digest": _manifest_digest(payload)}
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(destination)


def _load_verified_manifest(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("backup manifest is unreadable") from error
    if not isinstance(manifest, dict):
        raise SystemExit("backup manifest must be an object")
    digest = manifest.pop("digest", None)
    if not isinstance(digest, str) or digest != _manifest_digest(manifest):
        raise SystemExit("backup manifest digest mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise SystemExit("backup manifest has no artifacts")
    for raw in artifacts:
        if not isinstance(raw, dict):
            raise SystemExit("invalid backup artifact")
        location, expected_size, expected_digest = raw.get("location"), raw.get("size_bytes"), raw.get("sha256")
        if not isinstance(location, str) or not isinstance(expected_size, int) or not isinstance(expected_digest, str):
            raise SystemExit("invalid backup artifact metadata")
        path = (backup_dir / location).resolve()
        if not path.is_relative_to(backup_dir) or not path.is_file():
            raise SystemExit(f"backup artifact missing or unsafe: {location}")
        if path.stat().st_size != expected_size or _sha256(path) != expected_digest:
            raise SystemExit(f"backup artifact verification failed: {location}")
    manifest["digest"] = digest
    return manifest


def verify(args: argparse.Namespace) -> None:
    backup_dir = Path(args.backup).expanduser().resolve()
    manifest = _load_verified_manifest(backup_dir)
    required = {item.strip() for item in args.require.split(",") if item.strip()}
    available = {str(item["component"]) for item in manifest["artifacts"]}
    missing = sorted(required - available)
    if missing:
        raise SystemExit(f"backup is incomplete; missing: {', '.join(missing)}")
    print(f"verified {manifest['backup_id']}: {', '.join(sorted(available))}")


def restore(args: argparse.Namespace) -> None:
    backup_dir = Path(args.backup).expanduser().resolve()
    manifest = _load_verified_manifest(backup_dir)
    backup_id = str(manifest.get("backup_id", ""))
    if args.confirm_backup_id != backup_id:
        raise SystemExit("restore confirmation does not match manifest backup_id")
    artifacts = {str(item["component"]): backup_dir / str(item["location"]) for item in manifest["artifacts"]}
    dump = artifacts.get("postgresql")
    if dump is None:
        raise SystemExit("postgresql artifact is required for restore")
    _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--dbname", args.database_url, str(dump)])
    if args.files_target:
        target = Path(args.files_target).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            raise SystemExit("files restore target must be empty")
        for component in ("object-storage", "configuration"):
            archive = artifacts.get(component)
            if archive is not None:
                with tarfile.open(archive, "r:gz") as stream:
                    stream.extractall(target, filter="data")
    print(f"restored {backup_id}; run application readiness and business reconciliation before traffic cutover")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Create, verify, and restore Xingjing disaster-recovery artifacts.")
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--database-url", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--backup-id")
    create.add_argument("--object-storage")
    create.add_argument("--configuration")
    create.set_defaults(handler=backup)
    check = commands.add_parser("verify")
    check.add_argument("--backup", required=True)
    check.add_argument("--require", default="postgresql,object-storage")
    check.set_defaults(handler=verify)
    recover = commands.add_parser("restore")
    recover.add_argument("--backup", required=True)
    recover.add_argument("--database-url", required=True)
    recover.add_argument("--confirm-backup-id", required=True)
    recover.add_argument("--files-target")
    recover.set_defaults(handler=restore)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.handler(arguments)
