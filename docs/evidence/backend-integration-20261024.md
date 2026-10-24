# 本地正式后端接通证据（2026-10-24）

## 范围与结果

正常 React 入口已连接 Java 身份服务、PostgreSQL 与 Python FastAPI。验收使用真实 HTTP 注册、登录及项目操作，不使用 PreviewGateway、身份 SQL 种子、业务演示记录或伪造模型产物。此记录证明本机身份与业务数据链路，不能替代全部产品需求、外部供应商或生产部署验收。

| 服务 | 本机入口 | 核验结果 |
|---|---|---|
| React/Vite | `http://127.0.0.1:5173` | 正常入口登录、建项、刷新读取通过 |
| Python/FastAPI | `http://127.0.0.1:1241` | `/health/ready` 健康，正式业务接口已联调 |
| Java 身份 | `http://127.0.0.1:8080` | 注册、登录、会话与工作区切换通过 |
| PostgreSQL | `127.0.0.1:15432` / `xingjing_platform` | Java/Python 使用同一物理数据库 |
| Docker 基础设施 | `infra/compose` | PostgreSQL、RabbitMQ、MinIO 健康 |

独立本地业务库最初没有身份或项目数据；旧 `xingjing` 和预览数据库保留，不作为正常入口数据源。Flyway 成功版本为 8，失败迁移数为 0；Alembic 为 `b0e5e8effffa`，与仓库 head 一致。

## 实际浏览器与 HTTP 验收

通过正式注册接口创建本地验收账号，前端登录直接确认 Java 会话与工作区，然后进入真实空首页，无需先访问团队页面。通过引导式建项创建“后端接通验证”，保存原文并进入总控；刷新和正式服务重新启动后，项目库仍可回读该项目。此项目是通过正式操作产生的验收输入，未伪造生成任务、媒体或资金。

另两个通过正式接口注册的工作区完成 119 次 HTTP 请求。脱敏记录位于本地 `output/backend-integration-20261024/live-http-summary.json`，不包含凭据、请求头或邮箱；该输出目录不提交。

| HTTP 状态 | 次数 | 含义 |
|---|---:|---|
| 200 | 61 | 正常读取、工作区选择、时间线保存等 |
| 201 | 3 | 正式注册与资源创建 |
| 401 | 10 | 未登录请求被拒绝 |
| 403 | 14 | 缺权限、工作区上下文冲突或平台管理权限不足被拒绝 |
| 404 | 31 | 跨工作区资源不可见，及草稿项目尚未进入生成阶段 |

119 项均符合各自预期，没有未解释的失败。两次草稿生成列表 404 在正式激活项目后变为 200。主要读取覆盖项目、内容、资产、分镜、生成/音频、时间线/成片、任务、账单、审片、市场、通知与看板；空列表只表明尚无真实业务记录，不表示相应外部生产流程已验收。

PostgreSQL 只读查询核对了这两个账号的 Java 身份、ACTIVE OWNER 成员、M10 可信投影、两个 ready 项目和时间线版本 1/2，均与接口一致。两工作区的账务账户、生成任务、支付订单和退款均为 0。普通工作区 owner 访问平台通知管理返回 403，未创建平台管理员。

本地浏览器证据保存在同一输出目录：

- `01-real-empty-home.jpg`：正式注册后的真实空首页。
- `02-real-project-control.jpg`：正式 UI 创建并刷新后的项目总控。
- `03-real-empty-billing.jpg`：无账务账户时不显示赠送余额或假流水。
- `04-persisted-project-library.jpg`：正式启动工具重启服务后项目仍可读取。

## 接通时修复的实际问题

- 以 Java 已验证的成员事实补齐缺失 M10 投影，再解析动态权限。已有停用成员、自定义角色、角色权限和版本均保留，不在访问时恢复权限。
- 登录先确认身份、工作区切换结果及复读上下文，再写前端登录状态，防止失败切换继续进入业务页。
- 受保护的生成/对口型视频使用 Bearer 请求后播放 Blob，并随身份、工作区或资源变化取消请求、释放对象 URL；未加入 URL token。没有真实媒体输入，实际解码播放尚未验收。
- 编辑模块的 SQL 时间线读取、保存与外部渲染执行分离。缺渲染地址、令牌、回调或报价时，在建任务及冻结额度前明确拒绝渲染，不使用示例服务或假计费。
- 在线 PostgreSQL 迁移使用 psycopg `ClientCursor` 执行包含多段 DDL 的迁移，业务运行时仍使用原 asyncpg/psycopg 分工。配置方式参考 [SQLAlchemy PostgreSQL 文档](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)。
- 核对并纳入八项已有迁移修正：避免较早的共享 ORM 建表已包含后续字段时重复新增，并修复 PostgreSQL 更新子查询范围。Java 纳入实际身份服务需要的 RestClient 依赖。
- Windows Java 临时目录限定到仓库 `.xingjing-data/java-tmp`，解决本机 Java/Gradle 回环连接初始化失败；只改子进程环境，不修改系统 TEMP、安全策略或注册表。
- SDK 子进程在合并父环境前清空应用数据库、认证、内部回调等私有配置，并标记技能进程跳过仓库 dotenv 重载；显式注入的 Anthropic/provider/model 环境保留。该保护也覆盖 Windows 无 POSIX Bash scrub 的路径，主 API 和 worker 的 dotenv 加载保持正常。

## 可持续启动与持久化

在仓库根执行：

```powershell
uv run python scripts/local_platform.py start
uv run python scripts/local_platform.py status
uv run python scripts/local_platform.py stop
```

完整 `start` 已实际通过：基础设施健康 → 目标库检查与备份 → Java 构建/Flyway → Alembic → FastAPI 就绪 → Vite。日志和进程状态保存在忽略的 `.xingjing-data/`，本次启动标识为 `e0f0cab4134b5c35`。

迁移前实际生成 `.xingjing-data/backups/postgres-e0f0cab4134b5c35.dump`（534414 字节），通过 `PGDMP` 头和 `pg_restore --list` 检查。未执行恢复演练，不把目录检查等同于恢复验收。

配置优先级为显式环境、`.env`、`.env.local`。工具补缺失值且保留稳定密钥，拒绝显式空值、同库冲突、远端数据库和非 Compose 凭据；本地私密配置不提交。停止时核验 PID、创建时间、可执行文件及命令摘要，只管理自身进程树，不关闭 Docker。实际 `stop` 成功停止 frontend/backend/identity，三个应用端口均不再监听，Docker 基础设施保留；随后再次执行完整 `start`，标识 `c2e9e67b993da5d9`，所有服务就绪。第二份迁移前备份为 `postgres-c2e9e67b993da5d9.dump`（534654 字节），同样完成 archive 检查。重新刷新正式项目库后原项目仍在，服务保持运行。

## 检查结果与限制

- Java `bootJar --rerun-tasks` 成功，6 个任务执行；正常启动工具中的构建也成功。
- 前端 lint、类型检查和生产构建通过；全量 `pnpm check` 为 1851 通过、6 失败，均为已记录的四项旧登录断言和两项旧根路由断言。未改测试文件。
- 本次 Python 文件 Ruff、格式通过；核心接通模块 scoped basedpyright 为 0 error / 0 warning，环境隔离四文件为 0 error / 2 条既有 SDK 可空判断 warning；import-linter 为 1 kept / 0 broken。
- 全量 Python basedpyright 保留 69 个既有错误、1079 个 warning；未将其描述为全量通过。
- 已有 Team domain 10 项、编辑相关 35 项通过，编辑相关另 1 项已有跳过；两个既有身份协程通过 stdin 直接执行。补充持久化/ASGI检查只通过 stdin 运行，未创建测试脚本、夹具或快照。
- 已有安全/options 专项为 114 通过、7 跳过、1 失败。失败项 `test_options_assembler.py:108` 严格要求环境字典只含 Anthropic 一项，与新增私有键清空和隔离标记的必要行为冲突；该项是安全合同变化导致的既有断言失配，独立于上述前端六项基线，遵守用户要求未修改测试。13 项 stdin 实际组装与真实子进程 `import lib` 验证全部通过，确认 SDK 最终环境隔离、模型配置保留、主进程环境不变以及标记子进程不再重载 dotenv；未调用外部 SDK/模型服务。
- 冻结产品基线 `docs/product/v9.2` 的 25 项 SHA256 均匹配。

模型供应商、音视频生成、付费报价、正式渲染、商户支付渠道及 MFA 仍需要真实配置与独立验收。此次未执行外部付费生成、收款、退款或生产部署，也不把空数据读取或本地联调扩大为全部需求已完成。
