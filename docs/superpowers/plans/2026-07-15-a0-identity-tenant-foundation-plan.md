# A0 身份、租户与审计地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现并验证 A0 的最小真实闭环：账号注册/登录/刷新/退出、个人工作区创建与选择、不透明 access/refresh token、服务端工作区成员校验、基础权限、请求关联 ID 和追加式审计。

**Architecture:** Java `platform-app` 是 A0 的权威服务。令牌不是只在客户端自包含的 JWT，而是随机不透明令牌的 SHA-256 摘要存于 PostgreSQL；每个 Bearer 请求查找未撤销且未过期的会话，因此退出、密码重置和撤权可以在下一次请求立即生效。租户 API 由 `X-Workspace-Id` 指定上下文，认证过滤器与授权服务同时验证成员关系和权限，所有写操作追加 `audit_logs`。

**Tech Stack:** Spring Boot Web/Validation/Security/JDBC、Spring Security 7、PostgreSQL/Flyway、Testcontainers、MockMvc、Java 21。

---

## 文件边界

- `platform-core/src/main/java/.../core/RequestMetadata.java`：不可变请求、用户、工作区上下文。
- `platform-app/.../api/`：通用 envelope、错误响应、请求 ID 过滤器、异常处理。
- `platform-app/.../identity/`：注册、凭证、会话、令牌、认证过滤器和账号端点。
- `platform-app/.../workspace/`：工作区读取、上下文选择、成员/权限授权。
- `platform-app/.../audit/`：只追加的审计写入器。
- `platform-app/.../db/migration/V2__...sql`：身份、角色和审计模式，不修改 V1。
- `platform-app/src/test/.../identity`：端到端 HTTP/Testcontainers 验收。
- `docs/product/v9.2/06-api/openapi.yaml`：补充 A0 auth/session 路径与 schema。

### Task 1：用迁移和集成测试定义身份数据边界

**Files:**
- Create: `platform-services/platform-app/src/main/resources/db/migration/V2__add_identity_sessions_roles_and_audit.sql`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/identity/IdentityDatabaseMigrationTest.java`

- [ ] **Step 1：写失败的迁移测试**

断言 PostgreSQL 中存在 `user_credentials`、`login_sessions`、`roles`、`permissions`、`role_permissions`、`member_roles`、`audit_logs`，并验证 `login_sessions.token_hash`、`login_sessions.refresh_token_hash` 唯一且 `audit_logs` 没有 UPDATE/DELETE 权限授予。

- [ ] **Step 2：运行测试确认 V2 缺失**

```powershell
.\gradlew.bat :platform-services:platform-app:test --tests '*IdentityDatabaseMigrationTest'
```

Expected: FAIL，缺少身份与审计表。

- [ ] **Step 3：实现只向前的 V2 迁移**

迁移必须：

```sql
create table user_credentials (
  id uuid primary key,
  user_id uuid not null references users(id),
  identifier_hash char(64) not null unique,
  password_hash varchar(100) not null,
  failed_count integer not null default 0 check (failed_count >= 0),
  locked_until timestamptz,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  version bigint not null default 0
);
create table login_sessions (
  id uuid primary key,
  user_id uuid not null references users(id),
  token_hash char(64) not null unique,
  refresh_token_hash char(64) not null unique,
  device_name varchar(160) not null,
  expires_at timestamptz not null,
  refresh_expires_at timestamptz not null,
  revoked_at timestamptz,
  last_seen_at timestamptz not null,
  created_at timestamptz not null
);
```

并创建角色、权限、角色权限、成员角色与 `audit_logs`，给新个人工作区写入 `OWNER` 角色和 `workspace.view`/`workspace.manage`/`account.view`/`account.manage` 权限。

- [ ] **Step 4：复跑迁移测试**

Expected: PASS；第二次 Flyway 启动不重复写入系统权限。

### Task 2：定义统一请求元数据和 JSON 错误契约

**Files:**
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/api/ApiEnvelope.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/api/ApiError.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/api/RequestIdFilter.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/api/ApiExceptionHandler.java`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/api/ApiErrorContractTest.java`

- [ ] **Step 1：写失败的 MockMvc 测试**

测试缺少 Bearer token 时返回：

```json
{"error":{"code":"UNAUTHENTICATED","retryable":false},"meta":{"requestId":"..."}}
```

并断言客户端 `X-Request-Id` 原样返回；未提供时服务端生成 UUID。

- [ ] **Step 2：实现 envelope、异常与请求 ID filter**

所有成功响应使用 `{data,meta:{requestId,serverTime}}`，所有异常使用 `{error:{code,message,details,retryable},meta:{requestId}}`。禁止把密码、token 或异常堆栈写入响应。

- [ ] **Step 3：复跑契约测试**

Expected: 401、403、400 均满足 API conventions 的 JSON 结构和 requestId 透传。

### Task 3：实现注册、登录、令牌轮换和退出

**Files:**
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/identity/IdentityRepository.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/identity/IdentityService.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/identity/AuthController.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/identity/TokenHasher.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/identity/SessionPrincipal.java`
- Modify: `platform-services/platform-app/build.gradle.kts`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/identity/AuthApiIntegrationTest.java`

- [ ] **Step 1：写失败的端到端测试**

使用 Testcontainers PostgreSQL 验证：

1. `POST /api/v1/auth/register` 创建用户、BCrypt 凭证、个人工作区和 OWNER 成员；
2. `POST /api/v1/auth/login` 返回不同的 access/refresh token，响应不包含密码摘要；
3. `POST /api/v1/auth/refresh` 旋转 refresh token，旧 refresh token 返回 401；
4. `POST /api/v1/auth/logout` 后旧 access token 访问 `/api/v1/account/profile` 返回 401；
5. 相同未知邮箱与错误密码返回相同 `INVALID_CREDENTIALS`，不泄露账号存在性。

- [ ] **Step 2：加入 Spring Security 与 BCrypt 依赖**

在 `platform-app/build.gradle.kts` 添加 `spring-boot-starter-security` 和 `spring-security-test`。配置无状态 `SecurityFilterChain`：仅 `/api/v1/auth/register`、`/api/v1/auth/login`、`/api/v1/auth/refresh`、readiness 可匿名，其余 `/api/v1/**` 必须认证。

- [ ] **Step 3：实现不透明令牌服务**

令牌由 `SecureRandom` 生成；数据库只保存 SHA-256 hex 摘要；access 有效期 15 分钟、refresh 有效期 30 天；刷新在单事务中撤销旧会话并创建新会话；注销设置 `revoked_at`。

- [ ] **Step 4：复跑认证集成测试**

```powershell
.\gradlew.bat :platform-services:platform-app:test --tests '*AuthApiIntegrationTest'
```

Expected: 注册、登录、轮换、登出、错误不枚举账号全部 PASS。

### Task 4：实现工作区上下文、成员校验和基础 RBAC

**Files:**
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/workspace/WorkspaceRepository.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/workspace/WorkspaceAuthorizationService.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/workspace/WorkspaceController.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/identity/BearerTokenAuthenticationFilter.java`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/workspace/WorkspaceAuthorizationIntegrationTest.java`

- [ ] **Step 1：写失败的跨租户测试**

注册 Alice/Bob；分别创建个人工作区；用 Alice token：

1. `GET /api/v1/session/context` 只返回 Alice 有效成员工作区；
2. `GET /api/v1/workspaces/{aliceWorkspace}` 返回 200；
3. 访问 Bob 的同一路径以及提供 `X-Workspace-Id: bobWorkspace` 时均返回 403 `PERMISSION_DENIED`；
4. Alice 被移除后，下一次请求立即 403，不依赖前端缓存；
5. 缺失 `X-Workspace-Id` 的租户动作返回 400 `VALIDATION_ERROR`。

- [ ] **Step 2：实现 Bearer filter 与成员/权限检查**

Filter 将有效 access token 映射为 `SessionPrincipal`；工作区服务查询必须以 `workspace_id + user_id + ACTIVE` 为条件。`WorkspaceAuthorizationService.require(workspaceId, permission)` 对 OWNER 加载系统权限；所有对象读取在查询时带 workspaceId，不允许先按 id 查询再判断。

- [ ] **Step 3：实现 A0 只读端点**

`GET /api/v1/account/profile`、`GET /api/v1/session/context`、`GET /api/v1/workspaces/{workspaceId}` 返回规范 envelope、版本与权限能力。工作区选择通过 `POST /api/v1/workspaces/{workspaceId}/actions` 的 `selectWorkspace` action，要求 `Idempotency-Key`。

- [ ] **Step 4：复跑授权集成测试**

Expected: 允许、拒绝、跨租户拒绝、撤权即时生效和请求上下文测试全部 PASS。

### Task 5：追加审计与 OpenAPI 追踪

**Files:**
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/audit/AuditLogWriter.java`
- Modify: `platform-services/platform-app/src/main/java/com/xingjing/platform/identity/IdentityService.java`
- Modify: `platform-services/platform-app/src/main/java/com/xingjing/platform/workspace/WorkspaceController.java`
- Modify: `docs/product/v9.2/06-api/openapi.yaml`
- Test: `platform-services/platform-app/src/test/java/com/xingjing/platform/audit/AuditLogIntegrationTest.java`

- [ ] **Step 1：写失败的审计测试**

注册、登录、刷新、登出和工作区选择后，断言对应记录包括 requestId、actorId、workspaceId（适用时）、action、target、result、before/after 摘要与 occurredAt；普通业务写入路径没有 UPDATE/DELETE `audit_logs` 的代码。

- [ ] **Step 2：实现只追加审计写入器**

审计摘要只存 SHA-256 digest 或非敏感结构摘要；不得存 Authorization、密码、token、验证码或完整个人信息。

- [ ] **Step 3：补充 OpenAPI**

新增 `/auth/register`、`/auth/login`、`/auth/refresh`、`/auth/logout`、`/session/context`，为每个 operation 配唯一 `operationId`、成功/错误 schema 与 Bearer security。执行现有 OpenAPI lint。

- [ ] **Step 4：复跑审计、OpenAPI 和全模块测试**

```powershell
.\gradlew.bat :platform-services:platform-app:test
npx --yes @redocly/cli lint .\docs\product\v9.2\06-api\openapi.yaml
```

Expected: Java 测试、迁移、OpenAPI 都通过。

### Task 6：A0 运行验收与台账更新

**Files:**
- Modify: `docs/project/requirements/modules/M01-账号、工作区与协作.md`
- Modify: `docs/project/requirements/shared/S02-身份租户权限与审计.md`
- Modify: `docs/project/requirements/shared/S07-API安全部署与恢复.md`
- Modify: `docs/project/requirements/04-验收与测试证据.md`
- Modify: `docs/project/requirements/05-变更与缺口清单.md`
- Modify: `docs/project/requirements/06-依赖关系与联合验收.md`

- [ ] **Step 1：在本地 PostgreSQL 运行真实 A0 流程**

创建两个用户和两个工作区，完成登录、选择、跨租户拒绝、刷新轮换、撤销即时生效和审计检索，保存请求/响应及日志摘要到 `docs/evidence/a0-identity-tenant.md`。

- [ ] **Step 2：按证据更新状态**

仅将实际完成并通过自动化与运行验收的 IDN/WSP/RBAC/AUD 条目更新为“待验收”或“已验证”；M01 只有 A0 全场景通过后才更新模块状态。未做的邀请码、MFA、OIDC、API Key 和 P1 页面保持未开始/开发中。

## 自检

- access/refresh 均不存明文、轮换后旧 refresh 被拒绝；
- 直接资源 ID、伪造 workspace header、撤权后旧 token 均无跨租户通路；
- 所有 API 返回规范 requestId 和统一错误 envelope；
- 审计没有秘密，且不能通过普通业务 API 修改/删除；
- A0 文档状态由实测证据推导，不把 P1 身份功能提前标为完成。
