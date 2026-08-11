# 星镜剧创 v9.2 全系统开发规格资料包

## 基线声明

本资料包以 `D:\aivideo\xingjing_web_full_v9_2_nav_fix` 为唯一产品与交互基线，覆盖 197 个业务页面：创作者前台 124 页、团队/企业后台 18 页、平台运营后台 50 页、客户审片 Web/H5 5 页。

根目录旧版 PRD、技术架构和 UI 设计语言用于补充业务语义，不得覆盖 v9.2 已确定的菜单、页面归属与交互层级。

## 阅读顺序

1. `00-governance/source-of-truth.md`
2. `00-governance/decision-register.md`
3. `01-product/scope-and-priority.md`
4. `01-product/information-architecture.md`
5. `02-pages/` 下四套逐页规格
6. `04-architecture/`、`05-data/`、`06-api/`
7. `07-workflows/`、`08-ai/`、`09-billing/`
8. `10-security/`、`11-quality/`、`12-devops/`

## 资料状态

| 领域 | 状态 | 准入条件 |
|---|---|---|
| 基线与治理 | 可评审 | 版本、术语、变更规则完整 |
| 产品范围与信息架构 | 可评审 | 197 页均有阶段与领域归属 |
| 逐页开发规格 | 可评审 | 197/197 且必填章节完整 |
| UI 开发规范 | 可评审 | 组件、状态、响应式、可访问性完整 |
| 系统与服务架构 | 可评审 | 服务边界和关键时序完整 |
| 数据库设计 | 可评审 | ERD、字段、约束、索引完整 |
| API 契约 | 可评审 | OpenAPI 可解析、operationId 唯一 |
| 状态机与事件 | 可评审 | 状态、事件、重试、补偿完整 |
| AI 模型接入 | 可评审 | 能力、适配、路由、错误完整 |
| 算力计费 | 可评审 | 金额不变量和异常闭环完整 |
| 权限、合规与安全 | 可评审 | 权限矩阵、隔离、阻断和审计完整 |
| 测试与研发部署 | 可评审 | 追踪、测试、CI/CD、恢复完整 |

当前资料包已具备正式开发评审条件。能否进入商用开发和生产发布，还需完成 `00-governance/decision-register.md` 中对应领域评审与外部门禁。

## 自动验证结果

- 规格覆盖：197/197（创作者 124、团队 18、平台后台 50、客户审片 5）。
- 阶段分布：P0 56、P1 121、P2 20。
- 追踪矩阵：197 行，路由一一对应。
- 页面必填章节、路由唯一性、API 引用和占位内容扫描：通过。
- OpenAPI：Redocly CLI 校验通过，无错误和警告。

复验命令：

```powershell
& .\docs\v9.2-development-baseline\tools\Build-PageSpecifications.ps1
& .\docs\v9.2-development-baseline\tools\Format-OpenApi.ps1
& .\docs\v9.2-development-baseline\tools\Test-SpecCoverage.ps1
npx --yes @redocly/cli lint .\docs\v9.2-development-baseline\06-api\openapi.yaml
```

## 系统边界

- 包含：Web 创作者工作台、Web 团队后台、Web 平台运营后台、响应式客户审片外链。
- 不包含：Windows/macOS 桌面客户端、原生 iOS/Android/HarmonyOS 客户端、内容播放分发平台、专业 NLE 的完整替代。
- 数据库：PostgreSQL。
- 媒体存储：对象存储，数据库仅保存元数据与引用。
- 长任务：RabbitMQ 异步编排，SSE 推送进度。

## 变更记录

参见 `CHANGELOG.md`。任何影响页面、字段、接口、状态、权限、计费或合规的变更，必须同时更新追踪矩阵。
