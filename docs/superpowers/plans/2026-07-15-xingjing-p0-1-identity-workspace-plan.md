# 星镜剧创 P0-1 身份、工作区、多租户、RBAC 与审计实施计划

> 产品真相源：`docs/product/v9.2/`（XJ-WEB-9.2）。本计划只拆解 P0 内部工作包，不触发 `docs/project/XINGJING_REQUIREMENTS_PROJECT.md` 里程碑更新。

## 目标

交付可供 Web、Windows、macOS、iOS、Android、HarmonyOS 共用的统一身份与租户平台：创作者注册/登录/刷新/退出，工作区创建与切换，服务端能力上下文，工作区成员与角色裁决，应用层租户过滤 + PostgreSQL RLS 纵深防御，以及登录、成员、权限和跨租户拒绝的追加写审计。

## 不在本工作包内

- P1 的完整团队邀请、席位、企业认证、SSO、项目级自定义角色 UI；
- 平台员工独立登录域、客户审片身份和 API 客户端身份；
- 计费、合规、项目内容和 AI 任务领域实现。

这些能力必须复用本工作包的身份域、`RequestContext`、权限表达式和审计接口，不得另建平行真相源。

## 固定工程决策

- Java 平台层持有账号、工作区、成员、角色、权限和审计真相；ArcReel Python 不再新增这些业务真相。
- 先保持单体部署、模块化包边界：`auth`、`workspace`、`authorization`、`audit`、`web`；不为形式拆微服务。
- access token 为短期签名 JWT；refresh token 为随机不透明凭证，只保存 SHA-256 哈希，每次刷新旋转并检测复用。
- 密码只保存自带 salt 的当前安全哈希；响应、日志、审计均不得出现密码、验证码、完整 token 或 identifier 明文。
- 所有租户仓储方法显式接收 `workspaceId`；对象查询使用 `id + workspace_id`；生产表启用并强制 RLS。
- 角色/权限变更使用乐观版本；权限变化后新请求立即生效。
- 审计表只追加；应用角色无 UPDATE/DELETE，测试必须证明数据库层拒绝篡改。

## Task 1：建立身份域核心类型与安全配置边界

**Files:**
- Create: `platform-services/platform-core/src/main/java/com/xingjing/platform/core/UuidV7.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/domain/UserStatus.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/domain/IdentityType.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/web/RequestContext.java`
- Test: `platform-services/platform-core/src/test/java/com/xingjing/platform/core/UuidV7Test.java`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/web/RequestContextTest.java`

1. 先写 UUIDv7 版本位、变体位、同毫秒有序性和并发唯一性失败测试。
2. 实现无外部状态的 UUIDv7 生成器；不允许业务代码直接调用 `UUID.randomUUID()` 创建领域主键。
3. 先写 `RequestContext` 对 requestId、actorId、workspaceId、sessionId、identityDomain 的必填/可空规则测试。
4. 实现不可变上下文；未认证请求只有 requestId，租户业务请求必须同时具有 actorId、sessionId、workspaceId。
5. Run: `gradle :platform-services:platform-core:test :platform-services:platform-app:test --tests '*UuidV7Test' --tests '*RequestContextTest'`
6. Commit: `feat(identity): 建立身份请求核心类型`

## Task 2：把数据库升级为 v9.2 身份、工作区和审计模型

**Files:**
- Create: `platform-services/platform-app/src/main/resources/db/migration/V2__identity_workspace_rbac_audit.sql`
- Modify: `platform-services/platform-app/src/test/java/com/xingjing/platform/db/DatabaseMigrationTest.java`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/db/IdentitySchemaConstraintTest.java`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/db/TenantRlsTest.java`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/db/AuditImmutabilityTest.java`

1. 先扩展迁移测试，要求存在 `auth`、`workspace`、`audit` schema 和下列表：
   - `auth.users`、`auth.user_credentials`、`auth.login_sessions`、`auth.verification_challenges`、`auth.security_events`；
   - `workspace.workspaces`、`workspace.workspace_members`、`workspace.roles`、`workspace.permissions`、`workspace.role_permissions`、`workspace.member_roles`；
   - `audit.audit_logs`。
2. 用 V2 向前迁移 V1 公共表，禁止修改已应用的 V1；开发数据通过显式迁移或重建处理。
3. 字段、状态、唯一约束和索引以 `05-data/data-dictionary.md` 为准；identifier、refresh token 只存哈希。
4. 为租户表创建并 `FORCE ROW LEVEL SECURITY`，策略读取事务内 `app.workspace_id`；没有变量时 fail closed。
5. 为审计表创建拒绝 UPDATE/DELETE 的触发器和最小应用角色权限。
6. 先写两个工作区 ID 替换测试，证明 A 租户连接无法查询/修改 B 租户成员。
7. 先写审计记录 UPDATE/DELETE 均被 PostgreSQL 拒绝的测试。
8. 连续两次运行全部数据库测试，证明迁移可重复启动且约束真实生效。
9. Run: `gradle :platform-services:platform-app:test --tests '*DatabaseMigrationTest' --tests '*IdentitySchemaConstraintTest' --tests '*TenantRlsTest' --tests '*AuditImmutabilityTest'`
10. Commit: `feat(identity): 建立身份租户与审计数据库模型`

## Task 3：实现密码、identifier 与 token 安全原语

**Files:**
- Modify: `platform-services/platform-app/build.gradle.kts`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/security/IdentifierHasher.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/security/PasswordHasher.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/security/RefreshTokenFactory.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/security/AccessTokenService.java`
- Test: matching `*Test.java` files under `src/test/java/com/xingjing/platform/auth/security/`

1. 根据 Spring Security 4.1 对应官方文档确认依赖与 API 后再改构建文件。
2. 先写 identifier 规范化/哈希测试：邮箱大小写与首尾空格归一，数据库和日志无明文。
3. 先写密码哈希测试：相同密码产生不同哈希、正确密码验证、错误密码拒绝、明文不可恢复。
4. 先写 refresh token 测试：至少 256 bit 随机性、只返回一次明文、持久化 SHA-256 哈希。
5. 先写 access token 测试：issuer、subject、sessionId、identityDomain、iat/exp、签名和短期有效期。
6. 配置密钥只从环境/密钥引用读取；缺失时生产启动失败，测试使用独立临时密钥。
7. Run: `gradle :platform-services:platform-app:test --tests 'com.xingjing.platform.auth.security.*'`
8. Commit: `feat(identity): 建立凭证与令牌安全原语`

## Task 4：实现注册、登录、刷新旋转和退出

**Files:**
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/application/AuthService.java`
- Create: repositories and JDBC/JPA adapters under `auth/infrastructure/`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/web/AuthController.java`
- Create: request/response DTOs under `auth/web/dto/`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/auth/AuthApiTest.java`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/auth/RefreshTokenReuseTest.java`

1. 先写 API 失败测试：注册、登录、刷新、退出路由不存在；错误响应不得确认账号是否存在。
2. `POST /api/v1/auth/register`：创建用户、EMAIL 凭证、个人工作区、所有者成员和系统所有者角色，一个事务完成。
3. `POST /api/v1/auth/login`：统一失败文案；失败计数/锁定；成功创建 login session 和 token 对。
4. `POST /api/v1/auth/refresh`：原 refresh token 单次消费并旋转；检测已消费 token 复用时撤销该会话链并写安全事件。
5. `POST /api/v1/auth/logout`：幂等撤销当前会话；access token 后续请求被拒。
6. 所有成功/失败登录、刷新复用和退出写追加审计/安全事件，不记录凭证明文。
7. Run: `gradle :platform-services:platform-app:test --tests '*AuthApiTest' --tests '*RefreshTokenReuseTest'`
8. Commit: `feat(identity): 实现创作者认证会话闭环`

## Task 5：建立统一认证、请求 ID 与工作区上下文过滤链

**Files:**
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/web/RequestIdFilter.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/auth/security/AccessTokenFilter.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/workspace/security/WorkspaceContextFilter.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/web/ApiExceptionHandler.java`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/web/RequestContextFilterTest.java`

1. 先写请求 ID 生成/透传测试；响应必须回传 `X-Request-Id`。
2. 先写 401 测试：缺失、过期、签名错误、已撤销会话。
3. 先写工作区头测试：租户 API 缺 `X-Workspace-Id`、非成员、已停用成员全部 403；不得泄露对象信息。
4. 过滤链只解析身份和上下文，不在 gateway/过滤器里替代业务权限裁决。
5. 错误体遵守 v9.2 API 约定：`code`、`message`、`requestId`、可选 `details`；认证失败不回显 identifier。
6. 日志 MDC 只放 requestId、actorId、workspaceId、sessionId 摘要，不放 token。
7. Run: `gradle :platform-services:platform-app:test --tests '*RequestContextFilterTest'`
8. Commit: `feat(identity): 建立统一身份与租户请求链`

## Task 6：实现工作区创建、列表、成员与会话上下文

**Files:**
- Create: application/domain/infrastructure/web classes under `workspace/`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/workspace/WorkspaceApiTest.java`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/workspace/CrossTenantWorkspaceApiTest.java`

1. 先写以下接口的失败测试：
   - `POST /api/v1/workspaces`；
   - `GET /api/v1/workspaces`；
   - `GET /api/v1/workspaces/{workspaceId}`；
   - `GET /api/v1/workspaces/{workspaceId}/members`；
   - `GET /api/v1/session/context`。
2. 创建工作区时同事务创建所有者成员和系统角色；slug/名称唯一约束包含租户语义。
3. 列表只返回当前用户有效成员关系；详情使用 `id + membership` 查询，不先读后判断。
4. session context 返回当前用户、可选工作区、角色、permissions/capabilities 和 `featureFlags`；P0 的 `featureFlags` 由服务端返回空对象，客户端不得本地补默认权限。
5. 用两个用户、两个工作区对每个对象型接口执行 ID 替换，全部返回 403/404 且响应不含目标名称。
6. Run: `gradle :platform-services:platform-app:test --tests '*WorkspaceApiTest' --tests '*CrossTenantWorkspaceApiTest'`
7. Commit: `feat(workspace): 实现工作区与会话上下文`

## Task 7：实现 RBAC 裁决、乐观版本与审计查询

**Files:**
- Create: classes under `authorization/` and `audit/`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/authorization/AuthorizationMatrixTest.java`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/authorization/PermissionVersionTest.java`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/audit/AuditApiTest.java`

1. 种子权限以 `10-security/rbac-and-multitenancy.md` 权限代码为唯一清单；P0 先激活 OWNER 和 CREATOR 基础角色，但表结构支持 P1 角色矩阵。
2. 权限函数执行交集：identity permission ∩ membership ∩ data scope ∩ entitlement ∩ feature flag ∩ object state ∩ compliance/billing restriction；未知层默认拒绝。
3. 每个本工作包实际使用的权限代码至少写允许、拒绝、跨租户拒绝三例。
4. 成员/角色变更要求 `If-Match`，版本不一致返回 409；最后一名所有者不能移除或降级。
5. 权限变更在事务提交后立即使后续 session context 和 API 请求生效。
6. `GET /api/v1/workspaces/{workspaceId}/audit-logs` 仅授权角色可查，支持 requestId、actor、target 和时间分页；普通角色不可删除或修改。
7. Run: `gradle :platform-services:platform-app:test --tests '*AuthorizationMatrixTest' --tests '*PermissionVersionTest' --tests '*AuditApiTest'`
8. Commit: `feat(authorization): 实现租户权限与追加写审计`

## Task 8：建立 P0-1 契约、安全和证据门禁

**Files:**
- Create: `docs/evidence/p0-1-identity-workspace.md`
- Create/Modify: OpenAPI contract under `docs/api/`
- Modify: `.github/workflows/xingjing-platform.yml`
- Create: `scripts/Test-P01IdentityWorkspace.ps1`

1. 生成并校验 OpenAPI：认证接口不要求 Bearer；租户接口要求 Bearer + `X-Workspace-Id`；错误码与 v9.2 一致。
2. 门禁依次运行：单元测试、PostgreSQL/Testcontainers、RLS IDOR、refresh 复用、最后所有者、审计不可变、Boot JAR。
3. 用本地 Compose PostgreSQL 启动服务，执行注册→登录→创建/选择工作区→session context→跨租户拒绝→刷新旋转→退出的 HTTP 冒烟。
4. 证据记录提交号、迁移版本、测试计数、JAR SHA-256、接口响应摘要和阻断项。
5. 不更新总需求项目文档；P0-1 只是 P0 内部工作包。只有 P0 所有工作包通过时，才同步 P0 里程碑进度。
6. Run: `pwsh -NoProfile -File scripts/Test-P01IdentityWorkspace.ps1`
7. Commit: `test(identity): 建立 P0-1 身份租户验收门禁`

## 完成条件

- 注册、登录、refresh 旋转/复用检测、退出和 session context 全部可通过 HTTP 复现；
- 每个租户接口同时通过应用层 `id + workspace_id` 和 PostgreSQL RLS 测试；
- 最后一名所有者、角色版本冲突、权限即时生效规则有自动化证明；
- 登录/成员/权限/跨租户拒绝审计可按 requestId 检索且数据库拒绝 UPDATE/DELETE；
- ArcReel 原有前后端门禁、平台 Java 门禁和本地基础设施保持通过；
- 没有密码、identifier、token、验证码或敏感 URL 出现在数据库明文字段、响应、日志或审计摘要中。
