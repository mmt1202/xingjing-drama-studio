# 本地平台启动

此入口连接实际 Java 身份服务、PostgreSQL 业务库和 FastAPI，不安装演示业务数据，不依赖临时预览 Runtime。它用于本机开发，不是生产部署工具。

## 准备与启动

需要 Python/uv、前端 Node/pnpm 依赖、Java 21、已打开的 Docker Desktop，以及配置好的 `infra/compose/.env`。后者可以首次从同目录示例复制；已有文件不要覆盖。先在前端执行 `pnpm install`，在仓库根执行 `uv sync`，随后：

```powershell
uv run python scripts/local_platform.py start
```

Windows 也可使用 `./scripts/Start-LocalPlatform.ps1`。前端已单独运行时，可添加 `--no-frontend`，工具不会管理该 Vite 进程。

启动顺序为 Docker Compose 健康检查 → 真实目标数据库 → Java 构建与 Flyway → Python Alembic → FastAPI → Vite。已有目标库在迁移前备份，不删除数据卷、不清空数据库、不使用 `stamp head` 跳过迁移。默认本地业务库为 `xingjing_platform`，不将旧预览数据库中的构造数据作为正式数据。

| 服务 | 默认本机地址 |
|---|---|
| React | `http://127.0.0.1:5173` |
| FastAPI | `http://127.0.0.1:1241` |
| Java 身份 | `http://127.0.0.1:8080` |
| PostgreSQL | `127.0.0.1:15432` |

首次使用从前端注册页创建账号，登录后选择工作区。新工作区没有业务记录时显示空列表；未建立财务账户时余额为未知，不自动赠送余额、伪造收款或生成产物。平台后台仍要求相应管理权限，不把普通工作区 owner 自动升级成平台管理员。

## 配置来源与同库

优先级为显式进程环境 → 仓库 `.env` → 忽略的 `.env.local`。启动工具只补缺失配置；显式空值或冲突会报出变量名，需修改该赋值，不会悄悄覆盖。不要将本地配置或运行数据提交到 Git。

Java 子进程使用 JDBC `DATABASE_URL`；Python 的 `DATABASE_URL` 使用 `postgresql+asyncpg`，各同步领域使用 `postgresql+psycopg`。它们必须指向同一物理库，分别维护 `identity` 和 `public`，不能把 Java 的 JDBC 字符串传给 Python。市场读取 Python 主数据库。

普通 `uv run uvicorn server.app:app ...` 也会加载 `.env.local` 的缺失值。显式环境和 `.env` 保持优先，原兼容数据目录不自动迁移到新工作区。

SDK 智能体子进程使用单独组装的环境：应用的数据库、认证及内部服务配置被清空，明确注入的模型环境保持可用；技能子进程不会重新加载仓库 `.env` 或 `.env.local`。

## 状态、日志与停止

```powershell
uv run python scripts/local_platform.py status
uv run python scripts/local_platform.py stop
```

日志、进程身份、备份和媒体目录位于 `.xingjing-data/`。停止前核对 PID、进程创建时间、可执行文件和命令摘要；端口被其他进程占用时不接管、不结束那个进程。停止不会关闭 Docker 或删除容器数据卷。

Windows 下 Java/Gradle 的临时目录由启动工具单独设置为 `.xingjing-data/java-tmp`，同时传递 `java.io.tmpdir` 与 `jdk.net.unixdomain.tmpdir`，不修改系统级 TEMP、注册表或安全策略。

Java 的 `/actuator/health` 依现有权限策略可能返回 401；本地工具通过未认证 `/api/v1/session/context` 的预期错误格式确认身份服务已就绪，并检查真实迁移状态。它不会为健康检查开放管理接口。

## 外部能力边界

数据库连接成功不等于外部能力已配置。图片/视频/音频生成需要实际 Provider、模型凭据和有效报价；成片渲染需要真实渲染服务、存储及计费策略；支付需要商户网关与渠道；大额退款需要可信 MFA。缺少这些条件时，执行请求明确不可用，不创建假任务结果或资金变动。

时间线、项目、剧本、资产、任务记录、审片、看板和账务读取使用真实业务接口。编辑模块在未配置渲染器时仍可读取并保存本地时间线，提交渲染在创建任务及冻结额度之前检查依赖。

Docker Desktop 若显示 `dockerInference` 启动错误，先正常退出并重新打开；不要为了启动项目直接恢复出厂设置或删除数据卷。Docker 自身必须先恢复可用，工具随后才可启动数据库。
