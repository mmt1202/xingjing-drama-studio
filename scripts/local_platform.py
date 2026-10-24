"""Start the local Java/PostgreSQL/Python stack without preview identities or seed data."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".xingjing-data"
STATE = DATA / "local-platform.json"
PROFILE = ROOT / ".env.local"
LOCAL_HTTP = build_opener(ProxyHandler({}))
ASYNC_DOMAINS = ("PROJECT", "GENERATION", "AUDIO", "EDITING", "BILLING", "TASK", "MODEL", "COMPLIANCE", "PAYMENT")
SYNC_DOMAINS = (
    "CONTENT",
    "COMMERCIAL",
    "TEAM",
    "REVIEW",
    "ASSET",
    "STORYBOARD",
    "FINANCE",
    "ADMIN",
    "GOVERNANCE",
    "OPERATIONS",
    "OPEN_PLATFORM",
    "COVER",
    "VERSION",
)
STORAGE = {
    "XINGJING_ASSET_EVIDENCE_ROOT": "assets",
    "XINGJING_STORYBOARD_STORAGE_ROOT": "storyboards",
    "XINGJING_M06_ARTIFACT_ROOT": "generation",
    "XINGJING_M07_ARTIFACT_ROOT": "audio",
    "XINGJING_EDITING_OBJECT_STORAGE_ROOT": "editing",
    "XINGJING_COMPLIANCE_EXPORT_ROOT": "exports",
    "XINGJING_REVIEW_SCREENSHOT_ROOT": "review-screenshots",
}
SECRETS = (
    "AUTH_PASSWORD",
    "AUTH_TOKEN_SECRET",
    "XINGJING_REVIEW_HMAC_SECRET",
    "XINGJING_TASK_WORKER_TOKEN",
    "XINGJING_MODEL_CONTROL_TOKEN",
    "XINGJING_IDENTITY_NOTIFICATION_TOKEN",
    "XINGJING_OPEN_PLATFORM_SECRET_PEPPER",
    "XINGJING_M06_PROVIDER_CALLBACK_SECRET",
    "XINGJING_M06_BILLING_CALLBACK_SECRET",
    "XINGJING_M06_PROVIDER_RECOVERY_SECRET",
    "XINGJING_M07_PROVIDER_CALLBACK_SECRET",
    "XINGJING_EDITING_CALLBACK_SECRET",
    "XINGJING_BILLING_CALLBACK_SECRET",
)


class StartupError(RuntimeError):
    pass


def instant() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_state() -> dict:
    if not STATE.exists():
        return {"services": {}}
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if state.get("repo") != str(ROOT) or not isinstance(state.get("services"), dict):
            raise ValueError("invalid ownership state")
        return state
    except (ValueError, OSError) as error:
        raise StartupError("Local process state is invalid; no process was stopped.") from error


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError as error:
        raise StartupError("Run this command with: uv run python scripts/local_platform.py") from error
    return {key: value or "" for key, value in dotenv_values(path, encoding="utf-8").items()}


def configure() -> tuple[dict[str, str], dict[str, str]]:
    from sqlalchemy.engine import URL, make_url

    profile, base = read_env(PROFILE), read_env(ROOT / ".env")
    compose = read_env(ROOT / "infra" / "compose" / ".env")
    if not compose:
        raise StartupError("infra/compose/.env is required; configure its PostgreSQL/container credentials first.")
    config = {**profile, **base, **os.environ}
    for key, value in compose.items():
        if key in config and config[key] != value:
            raise StartupError(
                f"{key} conflicts with infra/compose/.env; local infrastructure credentials were not overwritten."
            )
    additions: dict[str, str] = {}

    def default(key: str, value: str) -> str:
        if key not in config:
            config[key] = value
            additions[key] = value
        if not config[key].strip():
            raise StartupError(f"{key} is explicitly empty; fill it or remove that assignment before startup.")
        return config[key]

    if "DATABASE_URL" not in config:
        if not compose.get("POSTGRES_USER") or not compose.get("POSTGRES_PASSWORD"):
            raise StartupError("POSTGRES_USER and POSTGRES_PASSWORD must be configured in infra/compose/.env.")
        configured = URL.create(
            "postgresql+asyncpg",
            username=compose["POSTGRES_USER"],
            password=compose["POSTGRES_PASSWORD"],
            host="127.0.0.1",
            port=15432,
            database=config.get("XINGJING_LOCAL_DATABASE", "xingjing_platform"),
        ).render_as_string(hide_password=False)
        default("DATABASE_URL", configured)
    try:
        database = make_url(default("DATABASE_URL", ""))
        if database.drivername != "postgresql+asyncpg" or not database.host or not database.database:
            raise ValueError("PostgreSQL asyncpg is required")
        if database.host.casefold() not in {"127.0.0.1", "localhost", "::1"}:
            raise StartupError("Local startup only supports a loopback PostgreSQL host; no migration was attempted.")
        if (
            (database.port or 5432) != 15432
            or database.username != compose.get("POSTGRES_USER")
            or database.password != compose.get("POSTGRES_PASSWORD")
            or database.query
        ):
            raise StartupError(
                "Local startup only supports the PostgreSQL container from infra/compose/.env on port 15432, "
                "with its configured credentials and no URL query overrides. No migration was attempted."
            )
        if len(database.database.encode("utf-8")) > 63:
            raise StartupError("The local PostgreSQL database name must fit its 63-byte identifier limit.")
        target = (database.host.casefold(), database.port or 5432, database.database, dict(database.query))
        for driver, domains in (("postgresql+asyncpg", ASYNC_DOMAINS), ("postgresql+psycopg", SYNC_DOMAINS)):
            for domain in domains:
                key = f"XINGJING_{domain}_DATABASE_URL"
                value = default(key, database.set(drivername=driver).render_as_string(hide_password=False))
                parsed = make_url(value)
                if (
                    parsed.drivername != driver
                    or (
                        parsed.host.casefold() if parsed.host else "",
                        parsed.port or 5432,
                        parsed.database,
                        dict(parsed.query),
                    )
                    != target
                ):
                    raise StartupError(f"{key} must use {driver} against the same physical PostgreSQL database.")
    except StartupError:
        raise
    except Exception as error:
        raise StartupError("DATABASE_URL or a domain database URL is invalid; credentials were not printed.") from error
    default("PLATFORM_PORT", "8080")
    default("PLATFORM_IDENTITY_URL", "http://127.0.0.1:" + config["PLATFORM_PORT"])
    identity = urlsplit(config["PLATFORM_IDENTITY_URL"])
    try:
        identity_port = identity.port or 80
        port = int(config["PLATFORM_PORT"])
    except ValueError as error:
        raise StartupError("PLATFORM_PORT or PLATFORM_IDENTITY_URL has an invalid port.") from error
    if (
        identity.scheme != "http"
        or identity.hostname not in {"127.0.0.1", "localhost", "::1"}
        or identity.username
        or identity.password
        or identity.query
        or identity.fragment
        or identity.path not in {"", "/"}
        or identity_port != port
        or not 1 <= port <= 65535
    ):
        raise StartupError("PLATFORM_IDENTITY_URL must identify the local Java service on PLATFORM_PORT.")
    default("XINGJING_DYNAMIC_PERMISSIONS_REQUIRED", "true")
    default("ARCREEL_DATA_DIR", str(DATA / "projects"))
    default("XINGJING_JAVA_TMP_DIR", str(DATA / "java-tmp"))
    for key, directory in STORAGE.items():
        default(key, str(DATA / "storage" / directory))
    for key in ("XINGJING_M06_PROVIDER_PROJECT_ROOT", "XINGJING_M07_PROVIDER_PROJECT_ROOT"):
        default(key, config["ARCREEL_DATA_DIR"])
    for key in SECRETS:
        default(key, secrets.token_urlsafe(48))
    default("XINGJING_CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    for key in (*STORAGE, "ARCREEL_DATA_DIR", "XINGJING_JAVA_TMP_DIR"):
        path = Path(config[key]).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
    if additions:
        text = PROFILE.read_text(encoding="utf-8") if PROFILE.exists() else ""
        text += ("\n" if text and not text.endswith("\n") else "") + "\n# Local platform startup defaults\n"
        text += "".join(f"{key}={json.dumps(value, ensure_ascii=False)}\n" for key, value in additions.items())
        PROFILE.write_text(text, encoding="utf-8")
        if os.name == "posix":
            PROFILE.chmod(0o600)
    python_env = {**config, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    host = database.host
    if ":" in host:
        host = "[" + host + "]"
    jdbc = "jdbc:postgresql://" + host + ":" + str(database.port or 5432) + "/" + database.database
    jdbc += "?" + urlencode({"currentSchema": "identity,public"})
    java_env = {
        **python_env,
        "DATABASE_URL": jdbc,
        "DATABASE_USER": database.username or "",
        "DATABASE_PASSWORD": database.password or "",
    }
    return python_env, java_env


def java_environment(environment: dict[str, str]) -> tuple[Path, dict[str, str]]:
    candidates = [environment.get("JAVA_HOME"), "C:/Program Files/Android/Android Studio/jbr"]
    located = shutil.which("java")
    if located:
        candidates.append(str(Path(located).parent.parent))
    java = None
    for candidate in candidates:
        if not candidate:
            continue
        executable = Path(candidate) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if not executable.is_file():
            continue
        result = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        version = (result.stderr + result.stdout).decode("utf-8", errors="replace")
        if re.search(r'version\s+"21[.\-+\"]', version):
            java = executable.resolve()
            break
    if java is None:
        raise StartupError("Java 21 is required. Configure JAVA_HOME or install the supported JBR 21 runtime.")
    child = {**environment, "JAVA_HOME": str(java.parent.parent)}
    temporary = Path(child["XINGJING_JAVA_TMP_DIR"]).expanduser()
    temporary = (ROOT / temporary).resolve() if not temporary.is_absolute() else temporary.resolve()
    temporary.mkdir(parents=True, exist_ok=True)
    child["TEMP"] = child["TMP"] = str(temporary)
    options = child.get("JAVA_TOOL_OPTIONS", "")
    for name in ("java.io.tmpdir", "jdk.net.unixdomain.tmpdir"):
        existing = re.search(r"(?:^|\s)-D" + re.escape(name) + r"=(?:\"([^\"]*)\"|'([^']*)'|(\S+))", options)
        if existing:
            value = next(part for part in existing.groups() if part is not None)
            selected = Path(value)
            selected = (ROOT / selected).resolve() if not selected.is_absolute() else selected.resolve()
            if selected != temporary:
                raise StartupError(
                    f"JAVA_TOOL_OPTIONS {name} conflicts with XINGJING_JAVA_TMP_DIR; no option was overwritten."
                )
        else:
            options += f' -D{name}="{temporary.as_posix()}"'
    child["JAVA_TOOL_OPTIONS"] = options.strip()
    return java, child


def process_info(pid: int) -> dict | None:
    if pid < 1:
        return None
    if os.name == "nt":
        command = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' -ErrorAction Stop; "
            "if($p){[pscustomobject]@{pid=$p.ProcessId;created=$p.CreationDate.ToUniversalTime().ToString('o');"
            "command=$p.CommandLine;executable=$p.ExecutablePath}|ConvertTo-Json -Compress}"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            value = json.loads(result.stdout.decode("utf-8-sig"))
        except (ValueError, UnicodeError, OSError, subprocess.TimeoutExpired):
            return None
    else:
        try:
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode()
            created = (Path("/proc") / str(pid) / "stat").read_text().split(")", 1)[1].split()[19]
            value = {
                "pid": pid,
                "created": created,
                "command": command,
                "executable": str((Path("/proc") / str(pid) / "exe").resolve()),
            }
        except OSError:
            return None
    if not value.get("command") or not value.get("executable"):
        return None
    value["commandDigest"] = hashlib.sha256(value.pop("command").encode()).hexdigest()
    return value


def owned(record: dict) -> bool:
    if record.get("stoppedAt"):
        return False
    current = process_info(int(record.get("pid", 0)))
    return current is not None and all(
        current.get(key) == record.get(key) for key in ("pid", "created", "commandDigest", "executable")
    )


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def healthy(url: str) -> bool:
    try:
        with LOCAL_HTTP.open(url, timeout=2) as response:
            if response.status != 200:
                return False
            if url.endswith("/api/v1/session/context"):
                return False
            if "/health/ready" in url:
                body = json.loads(response.read(65536))
                return body.get("status") == "healthy"
            return True
    except HTTPError as error:
        if url.endswith("/api/v1/session/context") and error.code == 401:
            try:
                body = json.loads(error.read(65536))
                return body.get("error", {}).get("code") == "UNAUTHENTICATED" and bool(
                    body.get("meta", {}).get("requestId")
                )
            except (OSError, ValueError, AttributeError):
                return False
        return False
    except (OSError, ValueError):
        return False


def verify_database(environment: dict[str, str], *, migrated: bool = False) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    engine = None
    try:
        url = make_url(environment["DATABASE_URL"]).set(drivername="postgresql+psycopg")
        engine = create_engine(url, connect_args={"connect_timeout": 10})
        with engine.connect() as connection:
            if connection.execute(text("SELECT current_database()")).scalar_one() != url.database:
                raise StartupError("PostgreSQL connected to an unexpected database.")
            if not migrated:
                if (
                    connection.execute(text("SELECT to_regclass('identity.flyway_schema_history')")).scalar_one()
                    is None
                ):
                    raise StartupError("Java is reachable but its Flyway migration history is missing.")
                completed, failed = connection.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE success), count(*) FILTER (WHERE NOT success) "
                        "FROM identity.flyway_schema_history"
                    )
                ).one()
                if not completed or failed:
                    raise StartupError("Java Flyway migrations have not completed successfully.")
                directory = ROOT / "platform-services/platform-app/src/main/resources/db/migration"
                expected = {path.name.split("__", 1)[0][1:].replace("_", ".") for path in directory.glob("V*__*.sql")}
                actual = set(
                    connection.execute(
                        text("SELECT version FROM identity.flyway_schema_history WHERE success AND version IS NOT NULL")
                    ).scalars()
                )
                if not expected or not expected.issubset(actual):
                    raise StartupError("Java Flyway migration history is missing checked-in versions.")
            else:
                from alembic.config import Config
                from alembic.script import ScriptDirectory

                expected = set(ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads())
                actual = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
                if actual != expected:
                    raise StartupError("Python Alembic migration history does not match the checked-in heads.")
    except StartupError:
        raise
    except Exception as error:
        raise StartupError("PostgreSQL/schema verification failed; database credentials were not printed.") from error
    finally:
        if engine is not None:
            engine.dispose()


def launch(
    state: dict,
    name: str,
    command: list[str],
    environment: dict[str, str],
    *,
    cwd: Path,
    port: int | None = None,
    health: str | None = None,
    input_path: Path | None = None,
    output_path: Path | None = None,
) -> subprocess.Popen:
    log = DATA / "logs" / f"{name}-{state['runId']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    with contextlib.ExitStack() as stack:
        output = stack.enter_context(log.open("ab", buffering=0))
        destination = stack.enter_context(output_path.open("xb", buffering=0)) if output_path else output
        source = stack.enter_context(input_path.open("rb")) if input_path else subprocess.DEVNULL
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=destination,
            stderr=output,
            stdin=source,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
    info = process_info(process.pid)
    if info is None:
        if process.poll() is not None:
            state["services"][name] = {
                "pid": process.pid,
                "stoppedAt": instant(),
                "log": str(log),
                "port": port,
                "health": health,
            }
            atomic_json(STATE, state)
            return process
        process.terminate()
        raise StartupError(f"Could not establish ownership of {name}; inspect {log}.")
    state["services"][name] = {**info, "log": str(log), "port": port, "health": health}
    atomic_json(STATE, state)
    return process


def wait_process(state: dict, name: str, process: subprocess.Popen, *, timeout: int) -> None:
    print(f"{name}: starting; log={state['services'][name]['log']}", flush=True)
    deadline = time.monotonic() + timeout
    health = state["services"][name].get("health")
    while time.monotonic() < deadline:
        code = process.poll()
        if code is None and health and healthy(health):
            print(f"{name}: ready", flush=True)
            return
        if code is not None:
            if code != 0 or health:
                raise StartupError(f"{name} exited before readiness; inspect {state['services'][name]['log']}.")
            state["services"][name]["stoppedAt"] = instant()
            atomic_json(STATE, state)
            return
        time.sleep(0.5)
    raise StartupError(f"{name} did not become ready; inspect {state['services'][name]['log']}.")


def ensure_database(state: dict, environment: dict[str, str], compose_command: list[str]) -> None:
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    compose = read_env(ROOT / "infra" / "compose" / ".env")
    database = make_url(environment["DATABASE_URL"])
    if not compose.get("POSTGRES_DB"):
        raise StartupError("POSTGRES_DB must identify the maintenance database in infra/compose/.env.")
    connection_options = {
        "host": database.host,
        "port": database.port or 5432,
        "user": database.username,
        "password": database.password,
        "connect_timeout": 10,
    }
    try:
        with psycopg.connect(**connection_options, dbname=compose["POSTGRES_DB"], autocommit=True) as connection:
            exists = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database.database,)).fetchone()
            if not exists:
                try:
                    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database.database)))
                except psycopg.errors.DuplicateDatabase:
                    pass
        with psycopg.connect(**connection_options, dbname=database.database) as connection:
            row = connection.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT LIKE 'pg_%' "
                "AND n.nspname <> 'information_schema')"
            ).fetchone()
            has_tables = bool(row and row[0])
    except Exception as error:
        raise StartupError(
            "Could not inspect or create the local PostgreSQL database; no migration was attempted."
        ) from error
    state["databaseCheckedAt"] = instant()
    atomic_json(STATE, state)
    if not has_tables:
        print("database: ready; no existing tables require a backup", flush=True)
        return
    backups = DATA / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    temporary = backups / f"postgres-{state['runId']}.dump.partial"
    backup = temporary.with_suffix("")
    dump = [
        *compose_command,
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "--username",
        compose["POSTGRES_USER"],
        "--dbname",
        database.database,
        "--format=custom",
        "--no-password",
    ]
    process = launch(state, "database-backup", dump, environment, cwd=ROOT, output_path=temporary)
    wait_process(state, "database-backup", process, timeout=300)
    with temporary.open("rb") as handle:
        if handle.read(5) != b"PGDMP":
            raise StartupError("PostgreSQL backup has an invalid archive header; no migration was attempted.")
    validate = [*compose_command, "exec", "-T", "postgres", "pg_restore", "--list"]
    process = launch(state, "database-backup-check", validate, environment, cwd=ROOT, input_path=temporary)
    wait_process(state, "database-backup-check", process, timeout=60)
    temporary.rename(backup)
    state["backup"] = str(backup)
    atomic_json(STATE, state)
    print(f"database: existing tables backed up and archive checked; file={backup}", flush=True)


def stop_owned(state: dict) -> list[str]:
    stopped = []
    for name, record in reversed(list(state.get("services", {}).items())):
        if not owned(record):
            continue
        pid = record["pid"]
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode and process_info(pid) is not None:
                raise StartupError(f"Could not stop the owned {name} process (PID {pid}).")
        else:
            os.killpg(pid, signal.SIGTERM)
        record["stoppedAt"] = instant()
        stopped.append(name)
        atomic_json(STATE, state)
    return stopped


@contextlib.contextmanager
def launcher_lock():
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "local-platform.lock").open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise StartupError("Another local platform start/stop operation is running.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def start(no_frontend: bool) -> None:
    previous = read_state()
    live = [name for name, record in previous["services"].items() if owned(record)]
    if live:
        raise StartupError("Managed processes are already running: " + ", ".join(live) + ". Use status or stop first.")
    python_env, java_env = configure()
    java, java_env = java_environment(java_env)
    port = int(java_env["PLATFORM_PORT"])
    ports = [("identity", port), ("backend", 1241)]
    if not no_frontend:
        ports.append(("frontend", 5173))
    for name, number in ports:
        if listening(number):
            suffix = " Use --no-frontend to leave an existing frontend untouched." if name == "frontend" else ""
            raise StartupError(
                f"Port {number} is already occupied by an unmanaged process; nothing was stopped.{suffix}"
            )
    docker = shutil.which("docker")
    node = shutil.which("node")
    vite = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    wrapper = ROOT / "gradle" / "wrapper" / "gradle-wrapper.jar"
    if not docker or not wrapper.is_file():
        raise StartupError("Docker and the checked-in Gradle wrapper are required.")
    if not no_frontend and (not node or not vite.is_file()):
        raise StartupError("Node.js and frontend dependencies are required; run pnpm install in frontend.")
    state = {"version": 1, "repo": str(ROOT), "runId": secrets.token_hex(8), "startedAt": instant(), "services": {}}
    atomic_json(STATE, state)
    try:
        compose_command = [
            docker,
            "compose",
            "--env-file",
            str(ROOT / "infra/compose/.env"),
            "-f",
            str(ROOT / "infra/compose/dev.compose.yml"),
        ]
        compose = [
            *compose_command,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "120",
        ]
        wait_process(
            state, "infrastructure", launch(state, "infrastructure", compose, python_env, cwd=ROOT), timeout=180
        )
        ensure_database(state, python_env, compose_command)
        build = [
            str(java),
            "-classpath",
            str(wrapper),
            "org.gradle.wrapper.GradleWrapperMain",
            ":platform-services:platform-app:bootJar",
            "--no-daemon",
            "--console=plain",
        ]
        wait_process(state, "java-build", launch(state, "java-build", build, java_env, cwd=ROOT), timeout=900)
        jars = [
            path
            for path in (ROOT / "platform-services/platform-app/build/libs").glob("*.jar")
            if not path.name.endswith("-plain.jar")
        ]
        if not jars:
            raise StartupError("The Java bootJar build did not produce an executable jar.")
        jar = max(jars, key=lambda path: path.stat().st_mtime_ns)
        identity_url = python_env["PLATFORM_IDENTITY_URL"].rstrip("/") + "/api/v1/session/context"
        identity = launch(
            state, "identity", [str(java), "-jar", str(jar)], java_env, cwd=ROOT, port=port, health=identity_url
        )
        wait_process(state, "identity", identity, timeout=180)
        verify_database(python_env)
        migration = launch(
            state, "python-migrations", [sys.executable, "-m", "alembic", "upgrade", "head"], python_env, cwd=ROOT
        )
        wait_process(state, "python-migrations", migration, timeout=300)
        verify_database(python_env, migrated=True)
        backend = launch(
            state,
            "backend",
            [sys.executable, "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", "1241"],
            python_env,
            cwd=ROOT,
            port=1241,
            health="http://127.0.0.1:1241/health/ready",
        )
        wait_process(state, "backend", backend, timeout=180)
        if not no_frontend:
            frontend = launch(
                state,
                "frontend",
                [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
                python_env,
                cwd=ROOT / "frontend",
                port=5173,
                health="http://127.0.0.1:5173/",
            )
            wait_process(state, "frontend", frontend, timeout=60)
        state["readyAt"] = instant()
        atomic_json(STATE, state)
        print("Ready: backend http://127.0.0.1:1241" + ("; frontend http://127.0.0.1:5173" if not no_frontend else ""))
        print("No preview identities, sample business data, model credentials or payment gateway were installed.")
    except BaseException:
        try:
            stop_owned(state)
        except (StartupError, OSError, subprocess.TimeoutExpired) as error:
            print(f"Startup cleanup: {error}", file=sys.stderr)
        raise


def status() -> None:
    state = read_state()
    entries = []
    for name, record in state["services"].items():
        live = owned(record)
        entries.append(
            {
                "service": name,
                "pid": record.get("pid"),
                "ownedAndRunning": live,
                "ready": healthy(record["health"]) if live and record.get("health") else None,
                "port": record.get("port"),
                "log": record.get("log"),
            }
        )
    ports = [{"port": port, "listening": listening(port)} for port in (8080, 1241, 5173)]
    print(
        json.dumps(
            {"profileExists": PROFILE.exists(), "services": entries, "ports": ports}, ensure_ascii=False, indent=2
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument(
        "--no-frontend", action="store_true", help="Start the backend stack without starting or stopping Vite."
    )
    arguments = parser.parse_args()
    try:
        if arguments.action == "status":
            status()
        else:
            with launcher_lock():
                if arguments.action == "start":
                    start(arguments.no_frontend)
                else:
                    print(json.dumps({"stopped": stop_owned(read_state())}, ensure_ascii=False))
        return 0
    except StartupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
