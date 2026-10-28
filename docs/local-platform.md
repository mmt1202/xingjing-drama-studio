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

## 分镜确定性编辑检查

正常应用装配默认加载 `deterministic-editorial` v1，并将同一个不可变策略注入分镜工作台和统一版本操作。无需配置模型、生成媒体或额外环境开关即可使用这组结构与编辑检查。

| 规则 | 默认值 | 行为 |
|---|---|---|
| 镜头时长建议区间 | 1000–30000 ms | 超出时 WARN，不阻止冻结 |
| 相邻镜头时长差建议上限 | 10000 ms | 超出时 WARN，不阻止冻结 |
| 对白文本密度建议上限 | 12 字符/秒 | 超出时 WARN，不阻止冻结 |
| 场景引用 | 必需 | 缺少时 ERROR，阻止冻结 |
| 有对白镜头的角色引用 | 必需 | 缺少时 ERROR，阻止冻结 |
| 额外关键词 | 空集合 | 未执行额外关键词筛查，不等于内容合规通过 |

空故事板不能冻结，但无需先生成图片或视频。冻结会重新计算当前版本的问题，并通过既有项目权限、资产真实归属、版本 CAS 和幂等校验；WARN 可以保留，ERROR 必须先处理。策略 ID、版本、摘要和规则保存在质检及冻结审计中。

这些数值是编辑建议，不是视频模型的能力限制，也不是 AI 质量评分。对白文本密度是按字符和镜头时长计算的粗略提示，没有测量真实语速，可能受语言影响。模型执行仍按实际选定模型的能力约束校验；冻结不能代替正式发布前的内容、版权与授权审核。

默认策略摘要为 `sha256:95bfbea49570fc74c7ba4db9543943ccef1e3e16a68b47b92a6ae1c671049497`。具有分镜读取权限的客户端可通过 `GET /api/v1/projects/{projectId}/shots?includeQualityPolicy=true` 的 `meta.qualityPolicy` 查看服务端实际策略；原列表请求不增加该字段。质检动作响应也返回策略描述。

需要部署覆盖时，由可信运维人员在服务可读的持久目录保存 UTF-8 JSON，例如 `.xingjing-data/config/storyboard-quality.json`，完整结构如下。该目录及本地配置不提交 Git；下面是配置示例，不是自动创建的文件。

```json
{
  "schema": "xingjing.storyboard-quality/v1",
  "policyId": "deterministic-editorial",
  "version": 2,
  "rules": {
    "minimum_duration_ms": 1000,
    "maximum_duration_ms": 30000,
    "max_adjacent_duration_delta_ms": 10000,
    "max_dialogue_characters_per_second": 12,
    "require_scene_reference": true,
    "require_character_for_dialogue": true,
    "compliance_terms": []
  }
}
```

在 `.env.local` 或显式服务进程环境中设置持久文件的绝对路径，例如：

```dotenv
XINGJING_STORYBOARD_QUALITY_POLICY_PATH=D:/aivideo/xingjing-platform/.xingjing-data/config/storyboard-quality.json
```

策略只在应用启动时加载，修改后须受控重新加载服务；不会热读取某个用户提交的配置路径。变更规则应递增 `version`，通过返回的版本与摘要确认实际生效。显式覆盖不能缺字段或添加其他字段：版本必须为正整数，时长最小值至少为 1、最大值不小于最小值、相邻差值不小于 0、字符/秒至少为 1；两个必需引用开关必须保持 `true`。关键词必须是字符串数组，最多 1024 项，每项非空白且不超过 256 字符。关键词按大小写不敏感的文字子串匹配，命中会阻止冻结，但这仍不替代独立内容审核。

文件上限为 128 KiB。显式空路径、文件不可读、无效 JSON、错误字段或类型均使质量策略不可用，记录 `STORYBOARD_QUALITY_POLICY_INVALID`，不会悄悄采用默认策略。此时分镜读取、保存和历史操作保留，质检／冻结不能绕过缺失策略继续成功；修正配置并重新加载服务后再读取策略确认。不要把配置放在 `.tmp` 验收目录，也不要把用户可写的上传目录用作可信策略来源。

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

### 图片与视频输入的网关契约

目录资产的描述 JSON 与图片文件是两种内容。生成请求通过 `input_asset_references` 指定目录资产 ID、版本 ID，以及可选的 `asset_content_sha256`（该版本 JSON 的摘要）。后端解析该版本关联的已入库生成媒体，核对真实文件大小和二进制 SHA-256，并将来源项目、任务、媒体 ID、对象键和摘要写入任务。直接选择不可变的生成媒体 ID 也走相同核验。缺少版本、只有描述没有图片、文件丢失或摘要不符时，在创建任务和冻结费用前拒绝执行。

带媒体输入的 M06 网关必须实现 `verified-media/v1`。HTTP 请求的 `inputAssets` 按选定顺序携带 `assetId`、`assetVersionId`、`assetContentSha256`、`generatedAssetId`、`sourceProjectId`、`sourceTaskId`、`mediaType`、`mimeType`、`sha256`、`sizeBytes` 和 `dataUrl`。接收端必须使用这些字节，核验二进制摘要及大小，不得再按 `inputAssetIds` 查询当前最新素材；首尾帧按数组首、尾两张图映射。`assetContentSha256` 不能替代 `sha256`。当前传输上限为每个文件 64 MiB、总输入 128 MiB；这是服务端内存与 HTTP 传输限制，不是模型能力上限。局部重绘的原图/遮罩角色契约尚未实现，提交会明确拒绝。

接收端还必须以 `Idempotency-Key: generation:{taskId}` 原子保留提交结果，同一个任务键重复到达时返回原 `jobId`，不能重新调用或计费；键对应输入发生变化时必须拒绝。只有完成真实网关联调后，才可将 `XINGJING_M06_PROVIDER_INPUT_CONTRACT` 设置为 `verified-media/v1`。默认不启用；不要仅为了隐藏错误开启。无媒体输入的文生任务保留原通道，资产首张参考图可通过文生生成，采用后形成可用于派生与分镜的新资产版本。

旧任务仍可查询，已有输出保持可用；缺少精确输入证据的旧任务不会自动按最新资产恢复提交，需要重新选择可验证的输入后创建新任务。网络结果未知的恢复继续使用原任务键、原输入和原计费记录。该协议与本地隔离验证不能代替实际接收网关及模型服务的验收。
