# 封面制作与标题 A/B 实现记录

日期：2026-10-23。证据 ID：EV-M18-COVER-002。范围：CR-022 / CR-023 及其发布材料交接。状态为本地实现与验证完成，真实服务联合验收保持开放。

## 原型与业务实现

依据原始 v9.2 `creator/cover-generate.html`、`creator/cover-title-ab-test.html` 及逐页目标，沿用已完成的创作者公共壳，实现专用封面工作台。原页面的通用偏好 JSON 和普通图片生成入口不能表达项目封面、文案组合与选用记录。

- 方案绑定明确成片版本、剧集和目标平台，创建后不可修改绑定；支持抖音、快手、视频号、小红书独立目标，9:16 / 3:4 / 1:1 / 16:9 画幅，提示词及百分比标题参考区。
- 候选来自 M06 真实生成任务或当前项目已有的受控图片。生成采用 `image_generation` 与 `aspect_ratio`；模型、计费或回调未配置时明确不可生成，不创建虚假任务或返回示例结果。
- 组合分别保存图片、标题、简介、标签；A/B 并排比较，人工评分及评估说明、推荐、选用、应用。修改组合使该组合评估及相关选用状态失效；应用产生不可变发布选用快照。
- 标题与安全框为布局预览，保留原图片与独立文案/布局清单；未实现标题烧录、自动文案生成、CTR 预测或线上流量实验。图片生成来源也不等于版权授权。
- 页面包含服务端筛选/分页、实际任务状态、真实报价或未知状态、版本记录、三语文本、加载/空/权限/服务错误、草稿保护与 409 显式放弃重读。来源任务不可用时用降级状态显示。

## 数据、权限与接口

新增 `server/xingjing_cover`、迁移 `98c3c6cfddd8`（父版本 `87b2b5beccc7`），四张表保存方案、命令回执、审计、不可变选用快照。主应用已装配路由及运行时关闭流程。所有业务写入使用版本 CAS、稳定幂等键、审计与 outbox 同事务；生成命令持久 claim 支持 M06 已接受但响应丢失后的恢复。

接口前缀 `/api/v1/projects/{projectId}/cover-plans`：

| 接口 | 用途 |
|---|---|
| `GET /catalog` | 权限、成片、平台、画幅与动态 M06 可用性 |
| `GET /` / `POST /` | 方案筛选分页 / 创建 |
| `GET /{planId}` | 方案、候选、组合、任务与审计 |
| `POST /{planId}/actions` | 更新、生成、关联候选、保存/评估/推荐/选用组合、应用 |
| `GET /{planId}/candidates/{assetId}/content` | 鉴权图片读取，私有且不缓存 |

可信会话按租户、工作区和项目校验，拒绝 API Key。查看要求 `settings.view + project.view + final.view`，管理另需 `settings.manage`；生成另需 `generation.view/manage`，应用另需 `generation.view + export.manage`。自定义成员取可信工作区与项目分配权限交集，仅接受当前完整项目范围，未知或剧集/镜头窄范围拒绝，不扩权。前端封面页门禁使用其自身权限，视觉阶段仍归导出。

`XINGJING_COVER_DATABASE_URL` 为同步 SQLAlchemy URL，必须与明确配置的项目/剪辑权威来源指向同一数据库，自定义成员还检查团队来源。未配置或错误库返回 503；没有运行时自动建表。候选读取、推荐、选用和应用检查成功任务与资产归属、大小、SHA-256、真实格式和尺寸。

## 正式发布交接

发布深链携带 `projectVersion`、`episodeId`、`target`、`coverPlanId`、`coverSelectionId`。M09 的可用 ZIP 格式可消费精确选用快照；小红书目前不在 M09 正式目标枚举中，明确拒绝，不映射成其他平台。

`run_check` 将完整 `releaseCreative` 和 `releaseCreativeDigest` 送入可信审核服务，其响应须返回精确匹配的 `reviewedCreativeDigest`。ReviewRow 保存输入与回执摘要。预检、提交和实际打包前均核对，缺失、错误或旧视频回执返回 409 `COVER_COMPLIANCE_REVIEW_REQUIRED`。供应商未支持该回执时，带封面正式导出保持阻断。

ZIP 含经过一次读取并验证的原始封面字节、独立文案及选用/布局 manifest。交付先保存 `processing` reservation 冻结选用快照，同 key 通过 PostgreSQL advisory lock 串行；重试使用原快照。已完成交付优先重放，之后的草稿、最新选用或源图丢失不改写历史成功结果。无封面历史 MP4 保持兼容。危险请求 key 使用安全目录摘要。

## 验证结果与边界

| 检查 | 结果 |
|---|---|
| 前端全量 `pnpm lint`、最终修改定向 ESLint | 通过 |
| 前端 `pnpm build`（含类型检查） | 通过，保留既有大包及动态/静态导入提示 |
| 全量 `pnpm check` | 1851 passed / 6 failed；166 文件通过 / 2 失败 |
| 修改 Python 文件 Ruff | 通过 |
| 封面模块、新迁移及修改 M09 文件 basedpyright | 0 errors / 0 warnings |
| 全量 basedpyright | 69 errors / 1079 warnings，错误仍在其他既有模块 |
| import-linter | 1 kept / 0 broken |
| 隔离迁移 | SQLite 升级/重复升级、4 表 PostgreSQL DDL 编译、单 head 通过 |
| 封面直接 ASGI/行为验证 | 64 项通过 |
| M09 helper / 审核传输合同直接验证 | 27 项通过 |
| 实际 M09 ZIP 串联验证 | 35 项通过 |
| 已有 M09 专项 | 20 passed |
| 冻结产品基线 SHA-256 | 25 条一致，未修改 v9.2 原型资料 |

6 项前端失败与用户已批准推送的公共布局提交一致：4 项既有 LoginPage，2 项 router 仍断言旧根入口。没有新增失败，也没有创建或修改测试文件、脚本、夹具、快照。M09 已有专项正常收集受缺分类 marker 的既有规则阻断，使用 `--noconftest` 与隔离 basetemp 执行，不改测试内容。

64 项覆盖权限撤回/跨租户/跨对象/窄范围拒绝、精确旧成片绑定、人工评估前禁选、CAS、幂等与冻结选用、缺模型、已接受任务响应丢失恢复、图片篡改/缺失和来源库不一致。27 项覆盖媒体完整性、精确 digest 失效、缺/错审核回执与无封面旧响应兼容。

35 项使用实际 ProductionComplianceRuntime、SQL 仓储、LocalFormalExportExecutor、HTTP router：审核→预检→提交 ZIP→下载，读取真实 ZIP 核对图片 SHA、标题/简介/标签、成片/剧集/目标与审核清单；验证同 key 重放、改变 ID 冲突、草稿变更/源图丢失后历史成功重放、新选用不能复用旧回执、processing 中断后使用冻结材料恢复、无封面历史 MP4 兼容，以及显式选用 query 可读。SQLite 的 JSONB/UTC 类型和 advisory 函数仅在验证进程适配，不证明真实 PostgreSQL 并发语义；可信 provider/授权/结算为隔离适配与权威记录，不代表外部服务验收。

## 浏览器证据

使用独立端口和临时 SQLite、实际仓储/媒体/运行时；身份为本地虚拟适配，不是 Java 登录验收。两张候选来自已有真实图片，用于显示与完整性验证，不是付费模型生成证据。

- 确认专用页面实际读出候选、评估、版本与选用，缺模型按钮禁用且不显示虚假费用；生成页→A/B 保留 `p1-final-v1`，没有随较新 `p1-final-v2` 漂移。
- 修改 B 标题时切页被草稿保护阻止；保存后旧评分失效，重新录入 88.5 分与说明，选用并应用产生新快照；发布页携带新 `coverSelectionId` 和原成片/剧集/平台。
- p2 空方案从精确成片和小红书深链切到 A/B，创建表单仍保留绑定；保存自定义 90% × 25% 标题参考区后真实回读成功。
- 两个编辑页面产生真实 409，旧输入保留、旧写入不落库；显式放弃重读恢复新版本。修正冲突时按钮误示“正在保存”的文案状态。
- 预览未挂 M09 完整生产服务，发布页明确显示 503，不将该页面记作真实正式导出成功；正式 ZIP 的独立 HTTP 串联证据见上表。

本地截图与日志位于 `output/cover-workflow-20261023/`：`prototype-generate.jpg`、`prototype-ab.jpg`、`cover-generate.jpg`、`cover-ab.jpg`、`conflict-draft.jpg`、`lint.log`、`build.log`、`check.log`、`backend-types.log`、`backend-types-full.log`。实际 ZIP 保留在 `.tmp/cover-m09-chain-81k1gj1b/exports/tenant/workspace/exports/p1/delivery-a/formal-delivery.zip`，截图、临时数据库和 ZIP 不随代码提交。

## 部署和剩余验收

需要先应用迁移并配置同源数据库、可信 Java 会话、M06 模型/计费/回调/对象存储、支持精确封面文案回执的合规服务。真实 PostgreSQL 同 key 并发与中断恢复、付费模型和外部版权/结算/审核尚未验收。M18 保持开发中、0/18 已验证，不把本地链路通过升级为 D4/A8 联合验收完成。回滚前先保留已应用选用与交付审计，不以删除生产表恢复旧行为。
