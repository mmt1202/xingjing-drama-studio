"""
视频项目管理 WebUI - FastAPI 主应用

启动方式:
    cd ArcReel
    uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

注意：必须用 --reload-dir 限定监视目录，否则 watchfiles 会扫描
node_modules / .venv / .git / .worktrees 等十几万个文件，单核 CPU 50%+。
"""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from lib import PROJECT_ROOT
from lib.agent_session_store import session_store_enabled
from lib.agent_session_store.import_local import migrate_local_transcripts_to_store
from lib.agent_session_store.store import DbSessionStore
from lib.app_data_dir import app_data_dir
from lib.config.env_keys import PROVIDER_SECRET_KEYS
from lib.db import async_session_factory, close_db, init_db
from lib.generation_worker import GenerationWorker
from lib.httpx_shared import shutdown_http_client, startup_http_client
from lib.logging_config import attach_file_handler, migrate_legacy_log_dir, setup_logging
from lib.path_safety import try_safe_join
from lib.project_migrations import cleanup_stale_backups, run_project_migrations
from lib.source_loader.migration import migrate_project_source_encoding
from server.auth import ensure_auth_password, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import (
    agent_chat,
    agent_config,
    api_keys,
    assets,
    assistant,
    characters,
    cost_estimation,
    custom_providers,
    end_frames,
    files,
    generate,
    grids,
    internal_identity_notifications,
    onboarding,
    products,
    project_events,
    projects,
    props,
    providers,
    reference_videos,
    scenes,
    script_review,
    shot_uploads,
    system,
    system_config,
    tasks,
    usage,
    versions,
)
from server.routers import auth as auth_router
from server.services.project_events import ProjectEventService
from server.xingjing_admin_business import (
    create_production_admin_business_runtime,
    create_unavailable_admin_business_router,
)
from server.xingjing_admin_finance import (
    create_production_admin_finance_runtime,
    create_unavailable_admin_finance_router,
)
from server.xingjing_admin_governance import (
    create_production_admin_governance_runtime,
    create_unavailable_admin_governance_router,
)
from server.xingjing_assets_runtime import AssetRuntimeConfigurationError, create_production_asset_runtime
from server.xingjing_audio_http import create_audio_router
from server.xingjing_audio_runtime import create_production_audio_runtime
from server.xingjing_billing_http import create_billing_router, create_unavailable_billing_router
from server.xingjing_billing_runtime import BillingRuntimeConfigurationError, create_production_billing_runtime
from server.xingjing_commercial_runtime import (
    CommercialRuntimeConfigurationError,
    create_production_commercial_runtime,
    create_unavailable_commercial_router,
)
from server.xingjing_compliance_http import create_production_compliance_router, create_unavailable_compliance_router
from server.xingjing_compliance_runtime import (
    ComplianceRuntimeUnavailable,
    create_production_compliance_runtime,
)
from server.xingjing_compliance_runtime import (
    RuntimeConfigurationError as ComplianceRuntimeConfigurationError,
)
from server.xingjing_content_runtime import create_production_content_runtime
from server.xingjing_editing_http import create_unavailable_editing_router
from server.xingjing_editing_runtime import EditingRuntimeConfigurationError, create_production_editing_runtime
from server.xingjing_generation_http import create_generation_router
from server.xingjing_generation_runtime import create_production_generation_runtime
from server.xingjing_marketplace_http import create_production_marketplace_runtime
from server.xingjing_model_control import create_model_control_runtime
from server.xingjing_open_platform import (
    create_production_open_platform_runtime,
    create_unavailable_open_platform_router,
)
from server.xingjing_operations import (
    create_production_operations_runtime,
    create_unavailable_operations_routers,
)
from server.xingjing_projects_runtime import create_production_projects_runtime
from server.xingjing_review_http import create_review_router, create_unavailable_review_router
from server.xingjing_review_runtime import ReviewRuntimeConfigurationError, create_production_review_runtime
from server.xingjing_security import PlatformSecurityMiddleware
from server.xingjing_storyboard_http import (
    create_storyboard_http_dependencies,
    create_storyboard_router,
    create_unavailable_storyboard_router,
)
from server.xingjing_storyboard_runtime import StoryboardRuntimeConfigurationError, create_production_storyboard_runtime
from server.xingjing_tasks_runtime import create_production_tasks_runtime
from server.xingjing_team_http import create_team_router, create_unavailable_team_router
from server.xingjing_team_runtime import TeamRuntimeConfigurationError, create_production_team_runtime


def assert_no_provider_secrets_in_environ() -> None:
    """父进程禁止持有任何 provider 密钥；违反即 fail-fast。

    Bash 沙箱子进程通过 fork 继承父 env，父进程必须把 provider secrets
    全部下线到 DB，由 SDK options.env 显式注入子进程。
    """
    leaked = sorted(k for k in PROVIDER_SECRET_KEYS if os.environ.get(k))
    if leaked:
        raise RuntimeError(
            f"SECURITY: 父进程 os.environ 含 provider 密钥: {leaked}. "
            "请到 WebUI 系统配置页填写，并从 env / .env 中移除对应条目。"
        )


_APPARMOR_USERNS_SYSCTL = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
_UNPRIV_USERNS_SYSCTL = Path("/proc/sys/kernel/unprivileged_userns_clone")
_MAX_USER_NS_SYSCTL = Path("/proc/sys/user/max_user_namespaces")


def _read_sysctl(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _diagnose_bwrap_failure() -> str:
    """根据 host sysctl 状态给出 bwrap 失败的精确修复路径。

    procfs 是宿主机共享的，容器内同样能读到 host sysctl 值，所以这套
    诊断在 docker 内外都能跑。优先级：Ubuntu 24.04 AppArmor 限制 >
    传统 unprivileged_userns_clone > max_user_namespaces > 兜底容器配置。
    """
    parts: list[str] = []

    apparmor_userns = _read_sysctl(_APPARMOR_USERNS_SYSCTL)
    if apparmor_userns == "1":
        parts.append(
            "Detected Ubuntu 24.04+ AppArmor restriction (root cause):\n"
            "  /proc/sys/kernel/apparmor_restrict_unprivileged_userns = 1\n"
            "  Blocks ALL unprivileged user namespaces. `apparmor:unconfined`\n"
            "  in docker compose does NOT bypass this — it is a global LSM\n"
            "  switch, not a per-process profile.\n"
            "  Fix on HOST (not inside the container):\n"
            "    sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0\n"
            '    echo "kernel.apparmor_restrict_unprivileged_userns=0" '
            "| sudo tee /etc/sysctl.d/60-arcreel-bwrap.conf"
        )

    userns_clone = _read_sysctl(_UNPRIV_USERNS_SYSCTL)
    if userns_clone == "0":
        parts.append(
            "Unprivileged user namespaces disabled on host:\n"
            "  /proc/sys/kernel/unprivileged_userns_clone = 0\n"
            "  Fix on HOST: sudo sysctl -w kernel.unprivileged_userns_clone=1"
        )

    max_userns = _read_sysctl(_MAX_USER_NS_SYSCTL)
    if max_userns == "0":
        parts.append(
            "User namespace count limit set to 0 on host:\n"
            "  /proc/sys/user/max_user_namespaces = 0\n"
            "  Fix on HOST: sudo sysctl -w user.max_user_namespaces=15000"
        )

    if not parts:
        parts.append(
            "Container likely missing security relaxation. docker compose:\n"
            "  security_opt:\n"
            "    - seccomp:unconfined\n"
            "    - apparmor:unconfined\n"
            "  cap_add:\n"
            "    - NET_ADMIN"
        )

    return "\n".join(parts)


def check_sandbox_available() -> bool:
    """启动期检测 sandbox 工具可用性。

    返回 ``True`` 表示沙箱可用且必须启用；返回 ``False`` 表示 SDK 不支持
    当前平台（目前仅 Windows — sandboxing.md §"Platform support"），server
    仍可启动但 sandbox 关闭，Bash 工具回退到
    ``AgentAccessPolicy.WINDOWS_BASH_PREFIX_WHITELIST`` 代码白名单。
    macOS / Linux 工具缺失仍硬失败（受支持平台禁止降级）。
    """
    system = platform.system()
    if system == "Darwin":
        if shutil.which("sandbox-exec") is None:
            raise RuntimeError(
                "SANDBOX_UNAVAILABLE on macOS\n"
                "  sandbox-exec: not found in PATH (should be system-installed)\n"
                "Required for ArcReel agent runtime."
            )
        return True
    if system == "Linux":
        # 官方 sandboxing.md 明确 Linux 需要 bubblewrap + socat 一起装
        # （bwrap 做进程/文件隔离，socat 做网络代理转发）。
        missing = [name for name in ("bwrap", "socat") if shutil.which(name) is None]
        if missing:
            raise RuntimeError(
                "SANDBOX_UNAVAILABLE on linux\n"
                f"  missing in PATH: {', '.join(missing)}\n"
                "Required for ArcReel agent runtime. Install:\n"
                "  Ubuntu/Debian: sudo apt install bubblewrap socat\n"
                "  Fedora:        sudo dnf install bubblewrap socat\n"
                "  Arch:          sudo pacman -S bubblewrap socat"
            )
        # bwrap 装了不代表跑得起来。两类常见失败：
        # 1) 创建 user namespace 被拒：seccomp / apparmor / sysctl 屏蔽
        #    → "No permissions to create new namespace"
        # 2) 新 net namespace 内 loopback 配置被拒：容器缺 CAP_NET_ADMIN
        #    → "loopback: Failed RTM_NEWADDR: Operation not permitted"
        # 用与 SDK 实际调用接近的 unshare 参数试跑，启动期就拦下来，
        # 避免 agent 第一次调 Bash 才神秘失败。
        probe_cmd = [
            "bwrap",
            "--unshare-user",
            "--unshare-net",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "/bin/true",
        ]
        try:
            probe = subprocess.run(probe_cmd, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                "SANDBOX_BWRAP_BROKEN on Linux\n"
                f"  bwrap probe failed to execute: {exc}\n"
                "Required for ArcReel agent runtime."
            ) from exc
        if probe.returncode != 0:
            stderr = probe.stderr.decode("utf-8", errors="replace").strip() or "(no stderr)"
            raise RuntimeError(
                "SANDBOX_BWRAP_BROKEN on Linux\n"
                f"  bwrap installed but cannot run: {stderr}\n"
                f"{_diagnose_bwrap_failure()}"
            )
        return True
    logger.warning(
        "SANDBOX_UNSUPPORTED on %s — server 启动 sandbox=disabled，Bash 工具回退到代码白名单"
        "（python .claude/skills/.../scripts/*.py / ffmpeg / ffprobe）。"
        "生产部署推荐 macOS / Linux / Docker；Windows 用户建议使用 WSL2。",
        system,
    )
    return False


_DOCKERENV_PATH = Path("/.dockerenv")
_CGROUP_PATH = Path("/proc/1/cgroup")


def detect_docker_environment() -> bool:
    """启动期一次性检测当前是否在 Docker / Podman 容器内。

    用于决定是否启用 ``SandboxSettings.enableWeakerNestedSandbox``。
    """
    if _DOCKERENV_PATH.exists():
        return True
    try:
        content = _CGROUP_PATH.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "docker" in content or "podman" in content


# 初始化日志：模块导入期只挂 stream handler。
# file handler 推迟到 lifespan，前面要先跑 migrate_legacy_log_dir()
# 把旧 app_data_dir()/logs 平移到 PROJECT_ROOT/logs，否则新目录在 import
# 期被创建会堵掉 rename；同时也避免 pytest 收集阶段 import server.app 时
# 对真实文件系统产生副作用。
setup_logging(file=False)
logger = logging.getLogger(__name__)


def _log_profile_sync_outcome(stats: dict, *, log: logging.Logger = logger) -> None:
    """根据 ``sync_all_agent_profiles`` 返回的 stats 决定打 info 还是 warning。

    ``stats["aborted"]`` 是 bool；而 bool 是 int 的子类——简单的
    ``isinstance(v, int) and v > 0`` 会把 ``aborted=True`` 当成"同步完成"的正向
    信号，与实际状态相反。先单独处理 abort 信号，再用 ``type(v) is int``（严格
    类型相等）仅统计真正的整数计数。
    """
    if stats.get("aborted"):
        log.warning("agent_runtime profile 同步已中止: %s", stats)
        return
    if any(type(v) is int and v > 0 for v in stats.values()):
        log.info("agent_runtime profile 同步完成: %s", stats)


async def _migrate_source_encoding_on_startup(projects_root: Path) -> dict[str, dict]:
    """对每个项目执行幂等编码迁移。失败被捕获并写日志，不阻塞启动。"""
    summary: dict[str, dict] = {}
    if not projects_root.exists():
        return summary

    def _run_one(project_dir: Path) -> dict:
        marker_dir = project_dir / ".arcreel"
        marker = marker_dir / "source_encoding_migrated"
        if marker.exists():
            return {"skipped": True}
        try:
            result = migrate_project_source_encoding(project_dir)
            marker_dir.mkdir(exist_ok=True)
            marker.touch()
            if result.failed:
                err_log = marker_dir / "migration_errors.log"
                err_log.write_text(
                    "\n".join(f"FAILED: {name}" for name in result.failed) + "\n",
                    encoding="utf-8",
                )
            return {
                "migrated": result.migrated,
                "skipped": result.skipped,
                "failed": result.failed,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "源文件编码迁移失败 project=%s，已跳过，server 继续启动",
                project_dir.name,
            )
            try:
                marker_dir.mkdir(exist_ok=True)
                (marker_dir / "migration_errors.log").write_text(f"FATAL: {exc}\n", encoding="utf-8")
                marker.touch()
            except Exception:  # noqa: BLE001
                pass
            return {"error": str(exc)}

    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        summary[project_dir.name] = await asyncio.to_thread(_run_one, project_dir)
    return summary


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    # 安全红线检测：先父进程 env 净化，再 sandbox 工具可用性，再 docker 检测
    assert_no_provider_secrets_in_environ()
    sandbox_enabled = check_sandbox_available()
    # detect_docker_environment 仅在 sandbox 可用平台有意义（Linux 路径探测）；
    # Windows 回退时跳过，避免无意义的文件系统调用。
    is_docker = detect_docker_environment() if sandbox_enabled else False
    logger.info("Sandbox runtime: enabled=%s docker=%s", sandbox_enabled, is_docker)

    app.state.in_docker = is_docker
    app.state.sandbox_enabled = sandbox_enabled

    # 日志文件持久化：先一次性平移旧 app_data_dir()/logs，再挂 file handler。
    # 顺序很重要——file handler 会 mkdir 新目录，提前挂会让 migrate 的 rename
    # 撞到 "新旧都存在" 分支放弃迁移。
    await asyncio.to_thread(migrate_legacy_log_dir)
    attach_file_handler()

    ensure_auth_password()

    # Run Alembic migrations (auto-creates tables on first start)
    await init_db()
    if _billing_runtime is not None:
        await _billing_runtime.start()
    await _generation_runtime.start()
    await _audio_runtime.start()

    projects_root = app_data_dir()

    # 源文件编码迁移（幂等；失败不阻塞启动）。先于 schema 迁移跑：源文一律先归到 UTF-8，
    # 之后所有按 UTF-8 读源文的链路（分集规划、派生文件对账）才有统一的输入。
    source_migration_summary = await _migrate_source_encoding_on_startup(projects_root)
    migrated_total = sum(len(s.get("migrated") or []) for s in source_migration_summary.values())
    failed_total = sum(len(s.get("failed") or []) for s in source_migration_summary.values())
    if migrated_total or failed_total:
        logger.info(
            "源文件编码迁移完成：migrated=%d failed=%d projects=%d",
            migrated_total,
            failed_total,
            len(source_migration_summary),
        )

    # Run any pending project.json schema migrations (file-based).
    # Both calls are synchronous filesystem walks — offload to a worker thread
    # so they don't block the event loop during uvicorn startup.
    migration_summary = await asyncio.to_thread(run_project_migrations, projects_root)
    if migration_summary.migrated or migration_summary.failed:
        logger.info(
            "Project migrations: migrated=%s skipped=%d failed=%s",
            migration_summary.migrated,
            len(migration_summary.skipped),
            migration_summary.failed,
        )
    await asyncio.to_thread(cleanup_stale_backups, projects_root, 7)

    # Migrate any pre-existing local SDK jsonl transcripts into the DbSessionStore.
    # Runs once (marker-gated); failures are non-fatal and logged.
    if session_store_enabled():
        try:
            store = DbSessionStore(async_session_factory)
            await migrate_local_transcripts_to_store(
                store,
                projects_root=projects_root,
                data_dir=projects_root,  # same place .arcreel.db lives, so docker volume catches it
            )
        except Exception:
            logger.exception("session-store transcript migration failed (non-fatal)")

    # Migrate legacy .system_config.json → DB (no-op if file doesn't exist or already migrated)
    try:
        from lib.config.migration import migrate_json_to_db

        json_path = app_data_dir() / ".system_config.json"
        async with async_session_factory() as session:
            await migrate_json_to_db(session, json_path)
    except Exception as exc:
        logger.warning("JSON→DB config migration failed (non-fatal): %s", exc)

    # 旧任务级文本 backend 键 → 档位键（docs/adr/0051）。放在 JSON→DB 迁移之后：
    # 旧 JSON 里的同名键经 catch-all 落库后也能被本迁移收编
    try:
        from lib.config.migration import migrate_text_tier_settings

        async with async_session_factory() as session:
            await migrate_text_tier_settings(session)
    except Exception as exc:
        logger.warning("text tier settings migration failed (non-fatal): %s", exc)

    # 把 agent_runtime_profile 同步到存量项目（manifest 物化，同步文件 I/O → worker 线程）
    from lib.project_manager import get_project_manager

    _pm = get_project_manager()
    _profile_sync_stats = await asyncio.to_thread(_pm.sync_all_agent_profiles)
    _log_profile_sync_outcome(_profile_sync_stats)

    # 启动共享 httpx 客户端（用于版本检查等外部 API 调用）
    await startup_http_client()

    if _editing_runtime is not None:
        await _editing_runtime.start()

    # Initialize async services
    await assistant.assistant_service.startup(in_docker=is_docker, sandbox_enabled=sandbox_enabled)
    assistant.assistant_service.session_manager.start_patrol()

    logger.info("启动 GenerationWorker...")
    worker = create_generation_worker()
    app.state.generation_worker = worker
    # 注入 in-process cancel 回调必须在 worker.start() 之前，
    # 否则有窗口期 callback 为 None、cancel running 信号丢失（违反 ADR 0006 秒级响应）。
    from lib.generation_queue import get_generation_queue

    get_generation_queue().set_worker_cancel_callback(worker.request_cancel)
    await worker.start()
    logger.info("GenerationWorker 已启动")

    logger.info("启动 ProjectEventService...")
    project_event_service = ProjectEventService(PROJECT_ROOT, projects_root=app_data_dir())
    app.state.project_event_service = project_event_service
    await project_event_service.start()
    logger.info("ProjectEventService 已启动")

    yield

    # Shutdown
    project_event_service = getattr(app.state, "project_event_service", None)
    if project_event_service:
        logger.info("正在停止 ProjectEventService...")
        await project_event_service.shutdown()
        logger.info("ProjectEventService 已停止")
    worker = getattr(app.state, "generation_worker", None)
    if worker:
        logger.info("正在停止 GenerationWorker...")
        from lib.generation_queue import get_generation_queue

        # 先 stop（内部 drain inflight + 退出主循环）：期间 cancel API 仍可发起，
        # callback 仍可用，避免重新部署窗口期 cancel 信号被丢弃。
        # 依赖 worker.stop() 内部已 await _wait_inflight_completion——若后续重构
        # stop 拆掉 drain 步骤，需同时回访这里的顺序假设。
        # try/finally 保证 callback 清理必达：worker.stop 抛错时 _worker_cancel_callback
        # 仍能清空，避免污染后续生命周期/测试。
        try:
            await worker.stop()
        finally:
            get_generation_queue().set_worker_cancel_callback(None)
        logger.info("GenerationWorker 已停止")
    await shutdown_http_client()
    if _storyboard_runtime is not None:
        _storyboard_runtime.close()
    if _editing_runtime is not None:
        await _editing_runtime.close()
    _asset_runtime.close()
    await _projects_runtime.close()
    _content_runtime.close()
    await _generation_runtime.close()
    await _tasks_runtime.close()
    await _model_control_runtime.close()
    await _audio_runtime.close()
    if _compliance_runtime is not None:
        await _compliance_runtime.close()
    if _team_runtime is not None:
        await _team_runtime.close()
    if _billing_runtime is not None:
        await _billing_runtime.close()
    if _review_runtime is not None:
        await _review_runtime.close()
    if _commercial_runtime is not None:
        await _commercial_runtime.close()
    if _admin_business_runtime is not None:
        _admin_business_runtime.close()
    if _admin_finance_runtime is not None:
        _admin_finance_runtime.close()
    if _admin_governance_runtime is not None:
        _admin_governance_runtime.close()
    if _operations_runtime is not None:
        _operations_runtime.close()
    if _open_platform_runtime is not None:
        _open_platform_runtime.close()
    _marketplace_runtime.close()
    await close_db()


# 创建 FastAPI 应用
app = FastAPI(
    title="视频项目管理 WebUI",
    description="AI 视频生成工作空间的 Web 管理界面",
    version="1.0.0",
    lifespan=lifespan,
)

# M13 keeps its own synchronous-domain bridge over SQLAlchemy asyncio.  It is
# deliberately composed once for the application lifetime and closed with the
# application, rather than falling back to an in-memory repository per request.
_marketplace_runtime = create_production_marketplace_runtime()

try:
    _admin_business_runtime = create_production_admin_business_runtime()
except (RuntimeError, ValueError) as error:
    _admin_business_runtime = None
    _admin_business_router = create_unavailable_admin_business_router(str(error) or "ADMIN_BUSINESS_UNAVAILABLE")
    logger.warning("M15 admin business runtime unavailable: %s", error)
else:
    _admin_business_router = _admin_business_runtime.router

try:
    _admin_finance_runtime = create_production_admin_finance_runtime()
except (RuntimeError, ValueError) as error:
    _admin_finance_runtime = None
    _admin_finance_router = create_unavailable_admin_finance_router(str(error) or "ADMIN_FINANCE_UNAVAILABLE")
    logger.warning("M16 admin finance/model runtime unavailable: %s", error)
else:
    _admin_finance_router = _admin_finance_runtime.router

try:
    _admin_governance_runtime = create_production_admin_governance_runtime()
except (RuntimeError, ValueError) as error:
    _admin_governance_runtime = None
    _admin_governance_router = create_unavailable_admin_governance_router(str(error) or "ADMIN_GOVERNANCE_UNAVAILABLE")
    logger.warning("M17 admin governance runtime unavailable: %s", error)
else:
    _admin_governance_router = _admin_governance_runtime.router

try:
    _operations_runtime = create_production_operations_runtime()
except (RuntimeError, ValueError) as error:
    _operations_runtime = None
    _operations_router, _preferences_router = create_unavailable_operations_routers(
        str(error) or "OPERATIONS_RUNTIME_UNAVAILABLE"
    )
    logger.warning("M18 operations runtime unavailable: %s", error)
else:
    _operations_router = _operations_runtime.router
    _preferences_router = _operations_runtime.preferences_router

try:
    _open_platform_runtime = create_production_open_platform_runtime()
except (RuntimeError, ValueError) as error:
    _open_platform_runtime = None
    _open_platform_router = create_unavailable_open_platform_router(str(error) or "OPEN_PLATFORM_RUNTIME_UNAVAILABLE")
    logger.warning("M19 open platform runtime unavailable: %s", error)
else:
    _open_platform_router = _open_platform_runtime.router

# M03 运行时在配置或数据库不可用时自行提供稳定 503 路由，绝不回退到文件或内存仓储。
_projects_runtime = create_production_projects_runtime()
_content_runtime = create_production_content_runtime()
_tasks_runtime = create_production_tasks_runtime()
_model_control_runtime = create_model_control_runtime()

# M06 only exposes persisted reads/control operations when its asynchronous
# database is configured. Submission stays explicitly unavailable until a real
# Provider adapter has been wired; it never falls back to the legacy queue.
_generation_runtime = create_production_generation_runtime()

# M07 uses only its PostgreSQL adapter and trusted Java session context.
# Missing database, Provider or object storage remains an explicit failure at
# the relevant action; it never falls back to a local media implementation.
_audio_runtime = create_production_audio_runtime()

# M09 runs only from its PostgreSQL authority tables and trusted Java identity
# context.  Missing configuration leaves all routes in a stable 503 state;
# no SQLite or in-memory evidence can unlock a formal export.
try:
    _compliance_runtime = create_production_compliance_runtime()
except (ComplianceRuntimeConfigurationError, ComplianceRuntimeUnavailable, ValueError) as error:
    _compliance_runtime = None
    _compliance_router = create_unavailable_compliance_router()
    logger.warning("M09 compliance runtime unavailable: %s", error)
else:
    _compliance_router = create_production_compliance_router(_compliance_runtime)

# M10 only composes the PostgreSQL repository against the trusted Java session.
# When deployment configuration is absent, it exposes a stable 503 rather than
# falling back to a file, memory store, or caller-supplied workspace identity.
try:
    _team_runtime = create_production_team_runtime()
except TeamRuntimeConfigurationError as error:
    _team_runtime = None
    _team_router = create_unavailable_team_router(str(error) or "TEAM_RUNTIME_UNAVAILABLE")
    logger.warning("M10 team runtime unavailable: %s", error)
else:
    _team_router = create_team_router(_team_runtime)

# M11 reads the production generation/audio/editing ledgers and stores only
# plan, invoice, idempotency and audit metadata in PostgreSQL. Missing database
# configuration remains a stable 503 and never falls back to the domain memory repository.
try:
    _billing_runtime = create_production_billing_runtime()
except (BillingRuntimeConfigurationError, ValueError) as error:
    _billing_runtime = None
    _billing_router = create_unavailable_billing_router()
    logger.warning("M11 billing runtime unavailable: %s", error)
else:
    _billing_router = create_billing_router(_billing_runtime)

# M12 external review links are only served from PostgreSQL plus a deployment
# secret.  Missing prerequisites remain a visible 503 and never become an
# in-process review store.
try:
    _review_runtime = create_production_review_runtime()
except ReviewRuntimeConfigurationError as error:
    _review_runtime = None
    _review_router = create_unavailable_review_router(str(error) or "REVIEW_RUNTIME_UNAVAILABLE")
    logger.warning("M12 review runtime unavailable: %s", error)
else:
    _review_router = create_review_router(_review_runtime)

# M14 reads and writes only its PostgreSQL aggregate store and resolves every
# actor from the Java-selected workspace.  Until a real accounting adapter is
# deployed, the fail-closed port rejects financial state changes instead of
# recording a fabricated paid or frozen settlement.
try:
    _commercial_runtime = create_production_commercial_runtime()
except CommercialRuntimeConfigurationError as error:
    _commercial_runtime = None
    _commercial_router = create_unavailable_commercial_router(str(error) or "COMMERCIAL_RUNTIME_UNAVAILABLE")
    logger.warning("M14 commercial runtime unavailable: %s", error)
else:
    _commercial_router = _commercial_runtime.router

# M08 starts only with its real database, renderer, storage and callback
# secret. Missing deployment prerequisites return stable 503 at its public
# routes; they never fall back to files, memory, or a fake renderer.
try:
    _editing_runtime = create_production_editing_runtime()
except EditingRuntimeConfigurationError as error:
    _editing_runtime = None
    _editing_router = create_unavailable_editing_router(str(error))
    logger.warning("M08 editing runtime unavailable: %s", error)
else:
    _editing_router = _editing_runtime.router()

# M04 only mounts a SQLAlchemy-backed asset runtime.  Missing configuration is
# handled by its own stable 503 router; no file or in-memory asset repository is
# ever used by the main application.
try:
    _asset_runtime = create_production_asset_runtime(
        generation_submitter=_generation_runtime.submit,
        generation_asset_resolver=_generation_runtime.generated_artifact_file,
        generation_task_resolver=_generation_runtime.get_task,
    )
except (AssetRuntimeConfigurationError, ValueError) as error:
    logger.warning("M04 asset runtime unavailable: %s", error)
    _asset_runtime = create_production_asset_runtime(
        database_url="",
        generation_submitter=_generation_runtime.submit,
        generation_asset_resolver=_generation_runtime.generated_artifact_file,
        generation_task_resolver=_generation_runtime.get_task,
    )

# 缺失 M05 部署前置时不影响主应用启动；相关接口保持稳定 503，且绝不退回内存实现。
try:
    _storyboard_runtime = create_production_storyboard_runtime()
except (StoryboardRuntimeConfigurationError, ValueError) as error:
    _storyboard_runtime = None
    _storyboard_router = create_unavailable_storyboard_router()
    logger.warning("M05 storyboard runtime unavailable: %s", error)
else:
    _storyboard_router = create_storyboard_router(
        create_storyboard_http_dependencies(
            service=_storyboard_runtime.service,
            trusted_context_resolver=_storyboard_runtime.trusted_context_resolver,
            project_scope_authorizer=_storyboard_runtime.project_scope_authorizer,
            upload_store=_storyboard_runtime.upload_store,
            smart_draft_generator=_storyboard_runtime.generate_smart_drafts,
            prompt_repository=_storyboard_runtime.prompt_repository,
            audit_repository=_storyboard_runtime.repository,
            generation_runtime=_generation_runtime,
        )
    )

# CORS 配置（env 驱动）：
#   - CORS_ORIGINS 未设置 / 空 / 包含 "*" → 通配 origins，credentials 强制关闭
#     （CORS spec 不允许通配 + credentials 组合；Starlette 在初始化时会 RuntimeError）
#   - 否则按逗号分隔解析为白名单，credentials 打开供前端附带 cookie / Authorization 跨域
#
# 须在 register_error_handlers(app) 之前算出：未预期异常的 500 由
# ServerErrorMiddleware 兜底发送，绕过 CORSMiddleware（见
# server/error_handlers.py::_cors_headers_for），handler 需要这份配置手工补 CORS 头。
_cors_raw = os.environ.get("CORS_ORIGINS", "*").strip()
_allow_origins: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()]
if not _allow_origins or "*" in _allow_origins:
    _allow_origins = ["*"]
    _allow_credentials = False
else:
    _allow_credentials = True

# app 级异常处理器：异常→状态码→detail 映射的单点（见 server/error_handlers.py）
register_error_handlers(app, cors_allow_origins=_allow_origins, cors_allow_credentials=_allow_credentials)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PlatformSecurityMiddleware)


def _resolve_listen_addr() -> tuple[str, int]:
    """解析 ``LISTEN_HOST`` / ``LISTEN_PORT``，供 ``__main__`` 块与测试共用。

    **作用范围**：仅当通过 ``python server/app.py`` 直接执行（走下方 ``__main__``）
    时生效。通过 ``uvicorn server.app:app`` 这种标准 ASGI CLI 启动时，listen 地址
    由 uvicorn 进程自身的 ``--host`` / ``--port`` 参数决定，本函数不参与 —— 因为
    ASGI app 模块在 import 时无法回头改 uvicorn 进程的绑定。Docker、systemd 等
    部署需要把 host/port 作为 uvicorn CLI 参数显式传入。

    truthy 默认（``or``）兜底，覆盖 ``.env`` 误写空值（如 ``LISTEN_PORT=``）的场景。
    """
    host = os.environ.get("LISTEN_HOST") or "0.0.0.0"
    port = int(os.environ.get("LISTEN_PORT") or "1241")
    return host, port


# 前端每 3s 轮询下述接口获取任务状态；稳态下成功响应会把真正的错误/慢请求淹没，
# 所以对 2xx + 快速响应降级到 DEBUG，异常/慢响应仍走 INFO 保证可观测。
_QUIET_POLL_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/tasks"),
        ("GET", "/api/v1/tasks/stats"),
    }
)
_QUIET_SLOW_THRESHOLD_MS = 500.0


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    path = request.url.path
    _skip_log = path.startswith("/assets") or path == "/health"
    try:
        response: Response = await call_next(request)
    except Exception:
        if not _skip_log:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "%s %s 500 %.0fms (unhandled)",
                request.method,
                path,
                elapsed_ms,
            )
        raise
    if not _skip_log:
        elapsed_ms = (time.perf_counter() - start) * 1000
        is_quiet = (
            (request.method, path) in _QUIET_POLL_ENDPOINTS
            and response.status_code < 400
            and elapsed_ms < _QUIET_SLOW_THRESHOLD_MS
        )
        log = logger.debug if is_quiet else logger.info
        log(
            "%s %s %d %.0fms",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )
    return response


# 注册 API 路由

# 平台内部回调使用自己的服务凭据；星镜运行时在各自的 HTTP 边界校验工作区上下文。
app.include_router(internal_identity_notifications.router, prefix="/api/v1")
# M02 formal routes must be registered before the legacy ArcReel project
# router because both retain the public /projects paths during migration.
app.include_router(_projects_runtime.router, prefix="/api/v1", tags=["星镜项目管理"])
app.include_router(_tasks_runtime.router, prefix="/api/v1", tags=["平台任务、事件与通知"])
app.include_router(_model_control_runtime.router, prefix="/api/v1", tags=["模型目录与路由控制"])

#
# 认证要求的唯一真相源就是这个区块：带 dependencies 的 router 要求 Bearer token，
# 端点签名里的 CurrentUser 只表示「处理函数要用用户对象」，不承担授权职责。
# 不挂依赖的两组另有说明，见下方分组注释。
app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["项目管理"])
app.include_router(characters.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["角色管理"])
app.include_router(scenes.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["场景管理"])
app.include_router(props.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["道具管理"])
app.include_router(products.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["产品管理"])
app.include_router(files.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["文件管理"])
app.include_router(generate.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["生成"])
app.include_router(
    script_review.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["剧本审核 gate"]
)
app.include_router(shot_uploads.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["镜头上传"])
app.include_router(end_frames.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["镜头尾帧"])
app.include_router(versions.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["版本管理"])
app.include_router(usage.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["费用统计"])
app.include_router(auth_router.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["认证"])
app.include_router(
    assistant.router,
    prefix="/api/v1/projects/{project_name}/assistant",
    dependencies=[Depends(get_current_user)],
    tags=["助手会话"],
)
app.include_router(tasks.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["任务队列"])
app.include_router(providers.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["供应商管理"])
app.include_router(system_config.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["系统配置"])
app.include_router(system.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["系统"])
app.include_router(api_keys.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["API Key 管理"])
app.include_router(agent_chat.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["Agent 对话"])
app.include_router(agent_config.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["Agent 配置"])
app.include_router(
    custom_providers.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["自定义供应商"]
)
app.include_router(
    cost_estimation.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["费用估算"]
)
app.include_router(grids.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["宫格图"])
app.include_router(
    reference_videos.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["参考生视频"]
)
app.include_router(assets.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["全局资产库"])
app.include_router(onboarding.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["首次使用引导"])

# 公开端点：匿名可达。登录入口是拿 token 的前提，静态媒体经 <img src> / <video src> 加载。
app.include_router(auth_router.public_router, prefix="/api/v1", tags=["认证"])
app.include_router(files.public_router, prefix="/api/v1", tags=["文件管理"])

# 自带认证端点：成因都是浏览器直发请求带不了 Authorization header，
# 端点内自行校验凭证（SSE 用 CurrentUserFlexible 收 ?token=，导出用短时效下载 token）。
app.include_router(
    assistant.self_auth_router,
    prefix="/api/v1/projects/{project_name}/assistant",
    tags=["助手会话"],
)
app.include_router(project_events.self_auth_router, prefix="/api/v1", tags=["项目变更流"])
app.include_router(projects.self_auth_router, prefix="/api/v1", tags=["项目管理"])

# 星镜平台扩展路由。各运行时通过可信身份上下文、服务凭据或其公开访问票据完成授权。
app.include_router(_admin_business_router)
app.include_router(_admin_finance_router)
app.include_router(_admin_governance_router)
app.include_router(_operations_router)
app.include_router(_preferences_router)
app.include_router(_open_platform_router)
app.include_router(_marketplace_runtime.router())
app.include_router(_content_runtime.router(), prefix="/api/v1", tags=["剧本内容与 AI 导演"])
app.include_router(create_generation_router(_generation_runtime), prefix="/api/v1", tags=["图片与视频生成"])
app.include_router(create_audio_router(_audio_runtime))
app.include_router(_compliance_router)
app.include_router(_team_router)
app.include_router(_billing_router)
app.include_router(_review_router)
app.include_router(_commercial_router, prefix="/api/v1", tags=["商单"])
app.include_router(_editing_router)
app.include_router(_asset_runtime.router(), prefix="/api/v1", tags=["主体与素材资产"])
app.include_router(_storyboard_router, prefix="/api/v1", tags=["分镜与故事板"])


def create_generation_worker() -> GenerationWorker:
    return GenerationWorker()


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "message": "视频项目管理 WebUI 运行正常"}


@app.get("/health/live", include_in_schema=False)
async def liveness_check() -> dict[str, object]:
    return {"status": "healthy", "service": "xingjing-platform"}


@app.get("/health/ready", include_in_schema=False)
async def readiness_check() -> JSONResponse:
    checks: list[dict[str, str]] = []
    ready = True
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks.append({"component": "database", "status": "healthy"})
    except Exception as error:
        ready = False
        checks.append({"component": "database", "status": "unhealthy", "detail": type(error).__name__})

    storage_roots = {
        "projects": PROJECT_ROOT / "projects",
        "logs": PROJECT_ROOT / "logs",
    }
    for component, root in storage_roots.items():
        if root.is_dir() and os.access(root, os.R_OK | os.W_OK):
            checks.append({"component": component, "status": "healthy"})
        else:
            ready = False
            checks.append({"component": component, "status": "unhealthy", "detail": "storage_unavailable"})
    return JSONResponse(
        {"status": "healthy" if ready else "unhealthy", "service": "xingjing-platform", "checks": checks},
        status_code=200 if ready else 503,
    )


@app.get("/skill.md", include_in_schema=False)
async def serve_skill_md(request: Request) -> Response:
    """动态渲染 skill.md 模板，将 {{BASE_URL}} 替换为实际服务地址（无需认证）。"""
    from starlette.responses import PlainTextResponse

    template_path = PROJECT_ROOT / "public" / "skill.md.template"

    def _read() -> tuple[bool, str]:
        if not template_path.exists():
            return False, ""
        return True, template_path.read_text(encoding="utf-8")

    exists, template = await asyncio.to_thread(_read)
    if not exists:
        return PlainTextResponse("skill.md 模板不存在", status_code=404)

    # 从请求推断 base URL；仅信任 x-forwarded-proto（反向代理标准头），
    # host 使用连接实际目标地址，不接受可被用户伪造的 x-forwarded-host。
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto or request.url.scheme or "http"
    host = request.url.netloc
    base_url = f"{scheme}://{host}"

    content = template.replace("{{BASE_URL}}", base_url)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


class SPAShellNoCacheMiddleware:
    """SPA 入口 HTML 外壳禁止浏览器缓存。

    覆盖 spa_deep_link 与 app.frontend 原生 fallback 两条路径共用的响应特征
    （text/html），否则重新部署后浏览器可能沿用旧壳加载已被删除的旧哈希资源，
    导致白屏——按 content-type 而非按路由判定，才能同时管住 "/"、"/login" 等
    落在原生 fallback 上的入口。纯 ASGI 实现而非 BaseHTTPMiddleware：这是个作用于
    全部请求的全局中间件，BaseHTTPMiddleware 的 anyio TaskGroup + contextvars
    复制机制会给每个请求引入额外开销。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if headers.get("content-type", "").lower().startswith("text/html"):
                    headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(SPAShellNoCacheMiddleware)


# 前端构建产物：SPA 静态文件服务。fallback 仅对 GET/HEAD 生效，写请求误入页面路径不再返回页面。
# 挂载条件必须检查 index.html 而非目录：app.frontend 在启动期校验 fallback 文件，
# 构建产物不完整时会抛 RuntimeError 拖垮整个应用（含全部 API）
frontend_dist_dir = PROJECT_ROOT / "frontend" / "dist"

if (frontend_dist_dir / "index.html").is_file():

    @app.get("/app/{_rest:path}", include_in_schema=False)
    async def spa_deep_link(_rest: str) -> FileResponse:
        # SPA 深链末段可能带扩展名（如 /app/projects/x/source/chapter1.txt），
        # app.frontend 的 fallback 会将其判为静态资源请求返回 404，此处显式兜底回 SPA 外壳。
        # 本路由注册在 app.frontend 之前，/app/ 下任何请求都会先到这里——若构建产物中
        # 恰好存在 dist/app/... 下的真实静态文件（URL 路径与 app.frontend 的映射规则一致，
        # 即相对 dist 根目录同路径），须优先返回该文件，避免被无条件遮蔽。
        # _rest 是用户可控的 URL 段：越界一律降级回 SPA 外壳，不暴露 dist 之外的文件
        candidate = try_safe_join(frontend_dist_dir / "app", _rest, require_file=True)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(frontend_dist_dir / "index.html")

    app.frontend("/", directory=frontend_dist_dir, fallback="index.html")
else:
    logger.warning("frontend/dist/index.html 不存在，跳过前端页面挂载（API 不受影响）")


if __name__ == "__main__":
    import uvicorn

    _host, _port = _resolve_listen_addr()
    uvicorn.run(app, host=_host, port=_port, reload=True)
