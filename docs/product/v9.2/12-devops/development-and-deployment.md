# v9.2 研发与部署规范

## 1. 推荐仓库结构

```text
xingjing-drama-studio/
  apps/
    creator-web/
    team-admin-web/
    platform-admin-web/
    client-review-web/
  services/
    gateway/
    auth-service/
    user-workspace-service/
    project-service/
    content-service/
    generation-service/
    billing-service/
    export-compliance-service/
    admin-service/
    ai-service/
  workers/
    media-worker/
  packages/
    ui/
    api-client/
    contracts/
    observability/
  deploy/
    compose/
    kubernetes/
    monitoring/
  migrations/
  docs/
```

四个 Web 应用共享基础 UI 和 API 客户端，不共享路由、角色假设和发布包。Java 服务可采用 Gradle/Maven 多模块仓库，但每个服务独立构建镜像和迁移责任。

## 2. 环境

| 环境 | 用途 | 数据 | 外部服务 |
|---|---|---|---|
| local | 单人开发 | 合成数据 | 模拟供应商/沙箱 |
| ci | 自动测试 | 每次临时创建 | 全模拟 |
| integration | 跨服务联调 | 可重置固定数据 | 沙箱、低配额 |
| staging | 发布验收 | 脱敏/合成 | 生产同类沙箱 |
| production | 商用 | 真实数据 | 正式合同通道 |

任何非生产环境不得复制未脱敏生产剧本、媒体、身份和账务数据。

## 3. 本地基础设施

开发 Compose 必须包含：PostgreSQL 16+、Redis、RabbitMQ management、MinIO、Nacos、Mail/SMS stub、OpenTelemetry collector。业务服务可本地运行或通过 profile 容器运行。

推荐端口仅限本机绑定：PostgreSQL 5432、Redis 6379、RabbitMQ 5672/15672、MinIO 9000/9001、Nacos 8848、OTel 4317/4318。生产端口不直接暴露公网。

## 4. 配置与环境变量

配置分为：

- 非敏感默认值：仓库内版本化配置；
- 环境差异：部署平台 ConfigMap/参数中心；
- 密钥：Secret Manager/Vault/KMS；
- 动态业务配置：平台配置中心，版本化并审计。

所有服务至少支持：

```text
APP_ENV
SERVICE_NAME
SERVER_PORT
DATABASE_URL
REDIS_URL
RABBITMQ_URL
OBJECT_STORAGE_ENDPOINT
OBJECT_STORAGE_BUCKET
NACOS_SERVER
OTEL_EXPORTER_OTLP_ENDPOINT
LOG_LEVEL
```

密钥变量只允许以 secret 引用注入，不写入 `.env.example` 的值。启动时校验必需配置，缺失立即失败。

## 5. 分支与变更

- 主分支 `main` 始终可发布，采用短分支和 Pull Request。
- 分支：`feat/<ticket>-<name>`、`fix/<ticket>-<name>`、`docs/<ticket>-<name>`。
- 禁止长期 `develop` 分叉；未完成功能用服务端功能开关隔离。
- PR 必须关联需求/页面 ID、API/迁移影响、测试证据、回滚方式和安全/计费/合规影响。
- 至少一名代码所有者审批；账务、权限、合规和迁移要求对应领域所有者审批。

## 6. CI 门禁

每个 PR 顺序执行：

1. 格式、lint、类型检查；
2. 单元和组件测试；
3. OpenAPI/事件 schema 校验及破坏性 diff；
4. 数据库迁移从空库和上一版本升级测试；
5. 服务集成与契约测试；
6. SAST、依赖、secret 和许可证扫描；
7. 容器镜像构建与漏洞扫描；
8. 关键页面组件和可访问性测试；
9. 生成 SBOM、镜像签名和不可变版本号。

失败门禁不得由普通开发者跳过；紧急豁免记录审批、风险、时限和补测。

## 7. 构建与制品

- 镜像标签：`<service>:9.2.<release>-<gitsha>`，生产只部署 digest。
- 前端静态资源包含内容 hash；源映射上传错误平台后不公开暴露。
- 制品库保留最近发布、当前生产和回滚所需版本。
- OpenAPI、数据库迁移、事件 schema 和 SBOM 与发布版本一起归档。

## 8. 数据库迁移

- 使用版本化迁移工具；每个 schema 由所属服务维护。
- 禁止生产手工 DDL。
- 兼容顺序：先加新结构 → 双读/双写 → 回填 → 切读 → 停旧写 → 后续版本删除。
- 大表索引使用在线/并发方式；回填限速并可续跑。
- 迁移前评估锁、空间和回滚；不可逆迁移必须有前向修复方案和备份验证。

## 9. 发布流程

1. 从 main 创建候选版本并冻结 OpenAPI/迁移/schema。
2. 部署 integration，执行契约和 E2E。
3. 部署 staging，执行迁移、模型 POC 回归、性能抽样和业务验收。
4. 生产先部署向后兼容数据库变化。
5. 5% 实例灰度，观察错误、延迟、队列、账务、模型和合规指标。
6. 逐步 25%/50%/100%，每阶段有最短观察窗。
7. 发布后运行合成主流程和账务守恒检查。

前端功能与后端 capability 同时通过 feature flag 控制，不能只隐藏按钮。

## 10. 回滚

- 应用：部署上一镜像 digest，要求 API 和数据库向后兼容。
- 数据库：优先前向修复；破坏性数据变更前有快照和恢复演练。
- 模型：路由配置版本化，可立即切回上一模型组合。
- 配置：每次发布生成不可变版本，可一键恢复并记录审计。
- 计费异常：暂停相关任务创建/自动结算，不通过回滚删除流水。

自动回滚触发参考：5xx 比基线上升 2 倍且持续 5 分钟、账务守恒失败、跨租户安全告警、正式导出门禁绕过、关键任务成功率显著下降。

## 11. 可观测性

### 指标

- RED：请求率、错误、延迟。
- USE：CPU、内存、连接、磁盘、GPU、worker 利用率。
- 业务：任务队列深度、成功率、各供应商失败率、冻结未结算量、导出成功率、合规阻断率、退款差异。

### 日志

结构化 JSON，字段 service、env、level、timestamp、requestId、traceId、workspaceId、actorId、operation、targetId、result、errorCode。敏感内容脱敏，日志采样不能采掉账务、审计和安全事件。

### 告警

- P1：跨租户、账本不平、密钥泄露、数据不可用、正式导出绕过。
- P2：核心 API 大面积失败、队列持续积压、模型全不可用、对象存储/数据库容量危险。
- P3：单模型异常、单任务异常率、非核心页面降级。

每条告警关联运行手册、值班组、升级路径和静默规则。

## 12. 备份与恢复

- PostgreSQL：每日全量 + 持续 WAL，核心目标 RPO≤5 分钟；账务/审计使用同步保护和额外归档。
- 对象存储：版本控制、生命周期、关键交付跨可用区复制。
- RabbitMQ：持久化 quorum queue；消息不是唯一业务记录，任务可从数据库/outbox 重建。
- Redis：不承载唯一真相，可从数据库恢复。
- 配置和密钥：版本化备份，恢复后强制验证访问策略。

每季度执行恢复演练，验证数据时间点、对象摘要、账本重算、审计完整性和 RTO。

## 13. 运行手册最低清单

- 数据库连接耗尽/主库故障；
- RabbitMQ 积压、死信和重复消费；
- 对象存储上传/下载失败；
- 单一/全部模型供应商不可用；
- 任务成功但结算 pending；
- 账本不平和重复扣费；
- 正式导出失败或合规服务不可用；
- 客户外链泄露或暴力访问；
- 密钥泄露、跨租户访问和后台账号入侵。

每份运行手册包含判断指标、影响、止损、诊断、恢复、验证、通知和复盘步骤。

## 14. 开发完成定义

- 需求、页面、API、数据、权限和测试追踪完整。
- 代码审查、自动测试、安全扫描和迁移测试通过。
- 指标、日志、告警和运行手册随功能提交。
- 新的计费、权限、合规和模型能力通过领域评审。
- staging 验收和回滚演练有证据，生产发布不依赖人工改库。
