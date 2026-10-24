"""
环境初始化模块

加载 .env，并从 .env.local 补齐缺失配置。

provider 密钥的真相源是 DB。如果 .env 残留 provider key 写入 os.environ，
父进程 fork 出的 Bash 沙箱子进程会继承到，违反安全红线 — 由
`server.app.assert_no_provider_secrets_in_environ()` 在 lifespan 启动期 fail-fast。
"""

import logging
import os
from pathlib import Path

from lib.config.env_keys import AGENT_SUBPROCESS_ENV_KEY

logger = logging.getLogger(__name__)


def init_environment():
    """初始化项目环境：定位项目根并依次加载 .env 与 .env.local。

    在 Agent Bash 沙箱子进程里，``.env`` 会被沙箱拒读（macOS sandbox-exec /
    Linux bwrap deny list），``env_path.exists()`` 会抛 PermissionError。
    沙箱子进程不需要 .env —— provider/Anthropic env 已由 SessionManager
    通过 ``options.env`` 显式注入。这里吞掉 OSError 让 ``import lib`` 链
    不被沙箱拦截。
    """
    lib_dir = Path(__file__).parent
    project_root = lib_dir.parent
    # Agent/skill subprocesses receive their explicit provider environment from
    # the SDK assembler. Loading server dotenv files here would undo isolation.
    if os.environ.get(AGENT_SUBPROCESS_ENV_KEY) == "1":
        return project_root

    try:
        from dotenv import load_dotenv

        env_path = project_root / ".env"
        # 沙箱里 stat 与 open 是两次独立 syscall：exists() 可能放行而
        # load_dotenv() 仍被 denyRead 拦截。整段统一用 OSError 兜底，
        # 任何文件访问失败都视为 ".env 不可用"，降级继续 import。
        try:
            if env_path.exists():
                load_dotenv(env_path, override=False, encoding="utf-8")
            else:
                # 默认会向上回溯查找 .env
                load_dotenv()
            local_env_path = project_root / ".env.local"
            if local_env_path.exists():
                load_dotenv(local_env_path, override=False, encoding="utf-8")
        except OSError:
            pass
    except ImportError:
        pass

    return project_root


PROJECT_ROOT = init_environment()
