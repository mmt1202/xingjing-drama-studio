# 星镜剧创平台地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ArcReel 代码基线上建立可重复构建、可迁移、可测试的星镜平台地基，并把 v9.2 权威规格纳入主仓。

**Architecture:** 保留 ArcReel 的 Python/FastAPI 创作与 AI 能力，新建 Java 21 + Spring Boot 4.1 平台多模块工程。平台业务使用 PostgreSQL 和 Flyway；本地基础设施使用 Docker Compose；产品规格、OpenAPI 和阶段状态进入主仓并由自动脚本校验。

**Tech Stack:** Java 21、Spring Boot 4.1.0、Gradle 9.5 Kotlin DSL、PostgreSQL 17、Flyway、JUnit 5、Testcontainers、PowerShell、现有 Python 3.12/FastAPI、React 19。

---

## 文件结构

- Create: `settings.gradle.kts` — Gradle 多项目声明与中央仓库配置
- Create: `build.gradle.kts` — Java 21、测试和通用构建约定
- Create: `gradle/libs.versions.toml` — Spring Boot 与插件版本目录
- Create: `gradlew`, `gradlew.bat`, `gradle/wrapper/*` — Gradle 9.5 Wrapper
- Create: `platform-services/platform-core/build.gradle.kts` — 无框架平台核心类型
- Create: `platform-services/platform-core/src/main/java/com/xingjing/platform/core/RequestMetadata.java`
- Create: `platform-services/platform-app/build.gradle.kts` — Spring Boot 可运行服务
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/PlatformApplication.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/system/ReadinessController.java`
- Create: `platform-services/platform-app/src/main/resources/application.yml`
- Create: `platform-services/platform-app/src/main/resources/db/migration/V1__create_identity_foundation.sql`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/system/ReadinessControllerTest.java`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/db/DatabaseMigrationTest.java`
- Create: `infra/compose/dev.compose.yml` — PostgreSQL、RabbitMQ、MinIO 本地依赖
- Create: `infra/compose/.env.example` — 非敏感本地配置示例
- Create: `scripts/Import-V92Baseline.ps1` — 导入权威规格并生成 SHA-256 清单
- Create: `scripts/Test-ProductBaseline.ps1` — 校验规格数量、哈希和占位符
- Create: `scripts/Write-FoundationEvidence.ps1` — 执行并落盘平台地基验证结果
- Create: `.github/workflows/xingjing-platform.yml` — 平台构建与基线校验
- Create: `docs/product/v9.2/` — 仓内权威规格副本
- Create: `docs/evidence/foundation-baseline.md` — 平台地基验证证据
- Modify: `.gitignore` — 忽略本地基础设施数据和秘密配置

## Task 1：把 v9.2 权威规格纳入主仓

**Files:**
- Create: `scripts/Import-V92Baseline.ps1`
- Create: `scripts/Test-ProductBaseline.ps1`
- Create: `docs/product/v9.2/**`
- Test: `scripts/Test-ProductBaseline.ps1`

- [ ] **Step 1：先写会失败的基线校验脚本**

创建 `scripts/Test-ProductBaseline.ps1`：

```powershell
[CmdletBinding()]
param(
    [string]$BaselineRoot = (Join-Path $PSScriptRoot '..\docs\product\v9.2')
)

$ErrorActionPreference = 'Stop'
$required = @(
    'README.md',
    '00-governance\source-of-truth.md',
    '01-product\scope-and-priority.md',
    '02-pages\creator-pages.md',
    '02-pages\team-pages.md',
    '02-pages\admin-pages.md',
    '02-pages\client-pages.md',
    '06-api\openapi.yaml',
    '11-quality\traceability-matrix.md',
    'baseline.sha256'
)

$missing = $required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $BaselineRoot $_)) }
if ($missing) { throw "v9.2 基线缺少文件: $($missing -join ', ')" }

$matrix = Get-Content -Raw -LiteralPath (Join-Path $BaselineRoot '11-quality\traceability-matrix.md')
$rows = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ ')
if ($rows.Count -ne 197) { throw "追踪矩阵应为 197 行，实际为 $($rows.Count)" }

$stageCounts = @{
    P0 = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ .*\| P0 \|').Count
    P1 = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ .*\| P1 \|').Count
    P2 = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ .*\| P2 \|').Count
}
if ($stageCounts.P0 -ne 56 -or $stageCounts.P1 -ne 121 -or $stageCounts.P2 -ne 20) {
    throw "阶段数量错误: P0=$($stageCounts.P0), P1=$($stageCounts.P1), P2=$($stageCounts.P2)"
}

$forbidden = Get-ChildItem -LiteralPath $BaselineRoot -Recurse -File |
    Where-Object Extension -in '.md', '.yaml', '.yml' |
    Select-String -Pattern '\bTBD\b|\bTODO\b|待补充'
if ($forbidden) { throw "基线包含占位内容: $((($forbidden.Path | Sort-Object -Unique) -join ', '))" }

$manifestPath = Join-Path $BaselineRoot 'baseline.sha256'
$manifestLines = Get-Content -LiteralPath $manifestPath | Where-Object { $_.Trim() }
foreach ($line in $manifestLines) {
    $hash, $relative = $line -split '  ', 2
    $target = Join-Path $BaselineRoot $relative
    if (-not (Test-Path -LiteralPath $target)) { throw "哈希清单文件不存在: $relative" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
    if ($actual -ne $hash) { throw "哈希不一致: $relative" }
}

Write-Output "v9.2 baseline verified: 197 pages; P0=56, P1=121, P2=20"
```

- [ ] **Step 2：运行脚本并确认因基线尚未导入而失败**

Run:

```powershell
pwsh -NoProfile -File .\scripts\Test-ProductBaseline.ps1
```

Expected: FAIL，错误包含 `v9.2 基线缺少文件`。

- [ ] **Step 3：实现确定性的基线导入脚本**

创建 `scripts/Import-V92Baseline.ps1`：

```powershell
[CmdletBinding()]
param(
    [string]$Source = 'D:\aivideo\docs\v9.2-development-baseline',
    [string]$Destination = (Join-Path $PSScriptRoot '..\docs\product\v9.2')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Source)) { throw "找不到 v9.2 规格源: $Source" }

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$sourceRoot = (Resolve-Path -LiteralPath $Source).Path
$destinationRoot = [IO.Path]::GetFullPath($Destination)

Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
    $relative = [IO.Path]::GetRelativePath($sourceRoot, $_.FullName)
    if ($relative -like 'tools\*') { return }
    $target = Join-Path $destinationRoot $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
}

$files = Get-ChildItem -LiteralPath $destinationRoot -Recurse -File |
    Where-Object Name -ne 'baseline.sha256' |
    Sort-Object FullName
$manifest = foreach ($file in $files) {
    $relative = [IO.Path]::GetRelativePath($destinationRoot, $file.FullName).Replace('\', '/')
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    "$hash  $relative"
}
Set-Content -LiteralPath (Join-Path $destinationRoot 'baseline.sha256') -Value $manifest -Encoding utf8NoBOM
Write-Output "Imported $($files.Count) v9.2 specification files"
```

- [ ] **Step 4：导入并验证基线**

Run:

```powershell
pwsh -NoProfile -File .\scripts\Import-V92Baseline.ps1
pwsh -NoProfile -File .\scripts\Test-ProductBaseline.ps1
```

Expected: PASS，输出 `v9.2 baseline verified: 197 pages; P0=56, P1=121, P2=20`。

- [ ] **Step 5：提交权威规格基线**

```bash
git add scripts/Import-V92Baseline.ps1 scripts/Test-ProductBaseline.ps1 docs/product/v9.2
git commit -m "docs(product): 纳入 v9.2 权威开发基线"
```

## Task 2：建立 Gradle 9.5 多模块构建

**Files:**
- Create: `settings.gradle.kts`
- Create: `build.gradle.kts`
- Create: `gradle/libs.versions.toml`
- Create: `platform-services/platform-core/build.gradle.kts`
- Create: `platform-services/platform-app/build.gradle.kts`
- Create: `gradlew`, `gradlew.bat`, `gradle/wrapper/*`
- Test: Gradle `projects` 与 `test` 任务

- [ ] **Step 1：创建版本目录**

创建 `gradle/libs.versions.toml`：

```toml
[versions]
spring-boot = "4.1.0"
dependency-management = "1.1.7"

[plugins]
spring-boot = { id = "org.springframework.boot", version.ref = "spring-boot" }
dependency-management = { id = "io.spring.dependency-management", version.ref = "dependency-management" }
```

- [ ] **Step 2：创建多项目声明**

创建 `settings.gradle.kts`：

```kotlin
import org.gradle.api.initialization.resolve.RepositoriesMode

rootProject.name = "xingjing-platform"

dependencyResolutionManagement {
    repositoriesMode = RepositoriesMode.FAIL_ON_PROJECT_REPOS
    repositories { mavenCentral() }
}

include("platform-services:platform-core")
include("platform-services:platform-app")
```

- [ ] **Step 3：创建根构建约定**

创建 `build.gradle.kts`：

```kotlin
import org.gradle.api.plugins.JavaPluginExtension

plugins {
    java
    alias(libs.plugins.spring.boot) apply false
    alias(libs.plugins.dependency.management) apply false
}

allprojects {
    group = "com.xingjing"
    version = "0.1.0-SNAPSHOT"
}

subprojects {
    apply(plugin = "java")

    extensions.configure<JavaPluginExtension> {
        toolchain { languageVersion = JavaLanguageVersion.of(21) }
    }

    tasks.withType<Test>().configureEach {
        useJUnitPlatform()
        testLogging { events("failed", "skipped") }
    }
}
```

- [ ] **Step 4：创建两个模块构建文件**

创建 `platform-services/platform-core/build.gradle.kts`：

```kotlin
plugins { `java-library` }

dependencies {
    testImplementation(platform("org.junit:junit-bom:6.0.3"))
    testImplementation("org.junit.jupiter:junit-jupiter")
}
```

创建 `platform-services/platform-app/build.gradle.kts`：

```kotlin
plugins {
    alias(libs.plugins.spring.boot)
    alias(libs.plugins.dependency.management)
}

dependencies {
    implementation(project(":platform-services:platform-core"))
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-validation")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    implementation("org.flywaydb:flyway-core")
    implementation("org.flywaydb:flyway-database-postgresql")
    runtimeOnly("org.postgresql:postgresql")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.springframework.boot:spring-boot-testcontainers")
    testImplementation("org.testcontainers:junit-jupiter")
    testImplementation("org.testcontainers:postgresql")
}
```

- [ ] **Step 5：使用官方 Gradle 镜像生成 Wrapper**

Run:

```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace gradle:9.5.0-jdk21 gradle wrapper --gradle-version 9.5.0
.\gradlew.bat projects
```

Expected: PASS，项目列表包含 `platform-core` 和 `platform-app`。

- [ ] **Step 6：提交构建骨架**

```bash
git add settings.gradle.kts build.gradle.kts gradle gradlew gradlew.bat platform-services/*/build.gradle.kts
git commit -m "build(platform): 建立 Java 21 多模块构建"
```

## Task 3：建立平台核心请求元数据

**Files:**
- Create: `platform-services/platform-core/src/main/java/com/xingjing/platform/core/RequestMetadata.java`
- Create: `platform-services/platform-core/src/test/java/com/xingjing/platform/core/RequestMetadataTest.java`

- [ ] **Step 1：先写失败的不可变性测试**

```java
package com.xingjing.platform.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.UUID;
import org.junit.jupiter.api.Test;

class RequestMetadataTest {
    @Test
    void requiresRequestUserAndWorkspaceIdentifiers() {
        var metadata = new RequestMetadata(
                "req-01",
                UUID.fromString("018f0000-0000-7000-8000-000000000001"),
                UUID.fromString("018f0000-0000-7000-8000-000000000002"));

        assertEquals("req-01", metadata.requestId());
        assertThrows(NullPointerException.class, () -> new RequestMetadata(null, metadata.userId(), metadata.workspaceId()));
    }
}
```

- [ ] **Step 2：运行测试确认类尚不存在**

Run:

```powershell
.\gradlew.bat :platform-services:platform-core:test --tests '*RequestMetadataTest'
```

Expected: FAIL，编译错误包含 `cannot find symbol RequestMetadata`。

- [ ] **Step 3：实现不可变请求元数据**

```java
package com.xingjing.platform.core;

import java.util.Objects;
import java.util.UUID;

public record RequestMetadata(String requestId, UUID userId, UUID workspaceId) {
    public RequestMetadata {
        requestId = Objects.requireNonNull(requestId, "requestId");
        userId = Objects.requireNonNull(userId, "userId");
        workspaceId = Objects.requireNonNull(workspaceId, "workspaceId");
        if (requestId.isBlank()) {
            throw new IllegalArgumentException("requestId must not be blank");
        }
    }
}
```

- [ ] **Step 4：运行核心模块测试**

Run:

```powershell
.\gradlew.bat :platform-services:platform-core:test
```

Expected: PASS。

- [ ] **Step 5：提交核心类型**

```bash
git add platform-services/platform-core/src
git commit -m "feat(platform): 建立请求租户元数据"
```

## Task 4：建立可运行平台服务和健康端点

**Files:**
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/PlatformApplication.java`
- Create: `platform-services/platform-app/src/main/java/com/xingjing/platform/system/ReadinessController.java`
- Create: `platform-services/platform-app/src/main/resources/application.yml`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/system/ReadinessControllerTest.java`

- [ ] **Step 1：先写失败的 HTTP 测试**

```java
package com.xingjing.platform.system;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.autoconfigure.exclude=org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration,org.springframework.boot.autoconfigure.flyway.FlywayAutoConfiguration"
})
class ReadinessControllerTest {
    @Autowired MockMvc mvc;

    @Test
    void exposesVersionedReadinessEndpoint() throws Exception {
        mvc.perform(get("/api/v1/system/readiness"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.service").value("xingjing-platform"))
                .andExpect(jsonPath("$.status").value("UP"));
    }
}
```

- [ ] **Step 2：运行测试确认应用类和控制器不存在**

Run:

```powershell
.\gradlew.bat :platform-services:platform-app:test --tests '*ReadinessControllerTest'
```

Expected: FAIL，Spring 上下文找不到应用配置或路由返回 404。

- [ ] **Step 3：实现应用入口和只读端点**

创建 `PlatformApplication.java`：

```java
package com.xingjing.platform;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class PlatformApplication {
    public static void main(String[] args) {
        SpringApplication.run(PlatformApplication.class, args);
    }
}
```

创建 `ReadinessController.java`：

```java
package com.xingjing.platform.system;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/system")
public class ReadinessController {
    @GetMapping("/readiness")
    public Map<String, String> readiness() {
        return Map.of("service", "xingjing-platform", "status", "UP");
    }
}
```

- [ ] **Step 4：配置端口、数据库、Flyway 和 Actuator**

创建 `application.yml`：

```yaml
server:
  port: ${PLATFORM_PORT:8080}

spring:
  application:
    name: xingjing-platform
  datasource:
    url: ${DATABASE_URL:jdbc:postgresql://127.0.0.1:5432/xingjing}
    username: ${DATABASE_USER:xingjing}
    password: ${DATABASE_PASSWORD:xingjing-local}
  jpa:
    open-in-view: false
    hibernate:
      ddl-auto: validate
  flyway:
    enabled: true

management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus
  endpoint:
    health:
      probes:
        enabled: true
```

- [ ] **Step 5：运行端点测试和生产构建**

Run:

```powershell
.\gradlew.bat :platform-services:platform-app:test --tests '*ReadinessControllerTest'
.\gradlew.bat :platform-services:platform-app:bootJar
```

Expected: PASS，并生成 `platform-app-0.1.0-SNAPSHOT.jar`。

- [ ] **Step 6：提交可运行平台服务**

```bash
git add platform-services/platform-app/src
git commit -m "feat(platform): 建立平台服务健康基线"
```

## Task 5：建立 PostgreSQL 和 Flyway 基线

**Files:**
- Create: `platform-services/platform-app/src/main/resources/db/migration/V1__create_identity_foundation.sql`
- Create: `platform-services/platform-app/src/test/java/com/xingjing/platform/db/DatabaseMigrationTest.java`

- [ ] **Step 1：先写失败的 Testcontainers 迁移测试**

```java
package com.xingjing.platform.db;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

@Testcontainers
@SpringBootTest
class DatabaseMigrationTest {
    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:17.6-alpine");

    @Autowired JdbcTemplate jdbc;

    @Test
    void createsIdentityFoundationTables() {
        var names = jdbc.queryForList(
                "select table_name from information_schema.tables where table_schema = 'public'",
                String.class);
        assertThat(names).contains("users", "workspaces", "workspace_members", "flyway_schema_history");
    }
}
```

- [ ] **Step 2：运行测试确认迁移文件缺失**

Run:

```powershell
.\gradlew.bat :platform-services:platform-app:test --tests '*DatabaseMigrationTest'
```

Expected: FAIL，断言缺少 `users`、`workspaces` 和 `workspace_members`。

- [ ] **Step 3：实现首个向前迁移**

创建 `V1__create_identity_foundation.sql`：

```sql
create table users (
    id uuid primary key,
    email varchar(320) not null,
    display_name varchar(120) not null,
    status varchar(32) not null,
    created_at timestamptz not null,
    created_by uuid not null,
    updated_at timestamptz not null,
    updated_by uuid not null,
    version bigint not null default 0,
    constraint uq_users_email unique (email),
    constraint ck_users_status check (status in ('ACTIVE', 'DISABLED', 'CANCELLED'))
);

create table workspaces (
    id uuid primary key,
    name varchar(160) not null,
    slug varchar(80) not null,
    status varchar(32) not null,
    created_at timestamptz not null,
    created_by uuid not null references users(id),
    updated_at timestamptz not null,
    updated_by uuid not null references users(id),
    version bigint not null default 0,
    constraint uq_workspaces_slug unique (slug),
    constraint ck_workspaces_status check (status in ('ACTIVE', 'SUSPENDED', 'CLOSED'))
);

create table workspace_members (
    workspace_id uuid not null references workspaces(id),
    user_id uuid not null references users(id),
    role_key varchar(80) not null,
    status varchar(32) not null,
    joined_at timestamptz not null,
    created_at timestamptz not null,
    created_by uuid not null references users(id),
    updated_at timestamptz not null,
    updated_by uuid not null references users(id),
    version bigint not null default 0,
    primary key (workspace_id, user_id),
    constraint ck_workspace_members_status check (status in ('INVITED', 'ACTIVE', 'SUSPENDED', 'REMOVED'))
);

create index ix_workspace_members_user on workspace_members(user_id, status);
```

- [ ] **Step 4：运行迁移测试和重复启动测试**

Run:

```powershell
.\gradlew.bat :platform-services:platform-app:test --tests '*DatabaseMigrationTest'
.\gradlew.bat :platform-services:platform-app:test --tests '*DatabaseMigrationTest'
```

Expected: 两次均 PASS，证明迁移可重复启动且不会重复建表。

- [ ] **Step 5：提交数据库基线**

```bash
git add platform-services/platform-app/src/main/resources/db platform-services/platform-app/src/test/java/com/xingjing/platform/db
git commit -m "feat(platform): 建立身份与工作区数据库基线"
```

## Task 6：建立本地基础设施

**Files:**
- Create: `infra/compose/dev.compose.yml`
- Create: `infra/compose/.env.example`
- Modify: `.gitignore`
- Test: Docker Compose config 与服务健康检查

- [ ] **Step 1：创建非敏感配置示例**

```dotenv
POSTGRES_DB=xingjing
POSTGRES_USER=xingjing
POSTGRES_PASSWORD=xingjing-local
RABBITMQ_DEFAULT_USER=xingjing
RABBITMQ_DEFAULT_PASS=xingjing-local
MINIO_ROOT_USER=xingjing
MINIO_ROOT_PASSWORD=xingjing-local-secret
```

- [ ] **Step 2：创建带健康检查的 Compose 文件**

```yaml
name: xingjing-dev
services:
  postgres:
    image: postgres:17.6-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports: ["15432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 20
    volumes: ["xingjing-postgres:/var/lib/postgresql/data"]

  rabbitmq:
    image: rabbitmq:4.1-management
    environment:
      RABBITMQ_DEFAULT_USER: ${RABBITMQ_DEFAULT_USER}
      RABBITMQ_DEFAULT_PASS: ${RABBITMQ_DEFAULT_PASS}
    ports: ["15672:5672", "15673:15672"]
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes: ["xingjing-rabbitmq:/var/lib/rabbitmq"]

  minio:
    image: minio/minio:RELEASE.2025-04-22T22-12-26Z
    command: server /data --console-address :9001
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    ports: ["19000:9000", "19001:9001"]
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:9000/minio/health/live || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes: ["xingjing-minio:/data"]

volumes:
  xingjing-postgres:
  xingjing-rabbitmq:
  xingjing-minio:
```

- [ ] **Step 3：补充本地秘密与运行数据忽略规则**

向 `.gitignore` 追加：

```gitignore
# Xingjing local platform
infra/compose/.env
.xingjing-data/
platform-services/**/build/
.gradle/
```

- [ ] **Step 4：验证 Compose 并启动依赖**

Run:

```powershell
Copy-Item infra\compose\.env.example infra\compose\.env
docker compose --env-file infra\compose\.env -f infra\compose\dev.compose.yml config --quiet
docker compose --env-file infra\compose\.env -f infra\compose\dev.compose.yml up -d --wait
docker compose --env-file infra\compose\.env -f infra\compose\dev.compose.yml ps
```

Expected: PostgreSQL、RabbitMQ、MinIO 均为 `healthy`。

- [ ] **Step 5：使用本地 PostgreSQL 启动平台服务**

Run:

```powershell
$env:DATABASE_URL='jdbc:postgresql://127.0.0.1:15432/xingjing'
$env:DATABASE_USER='xingjing'
$env:DATABASE_PASSWORD='xingjing-local'
.\gradlew.bat :platform-services:platform-app:bootRun
```

Expected: 服务监听 `8080`，`GET http://127.0.0.1:8080/api/v1/system/readiness` 返回 `status=UP`。

- [ ] **Step 6：提交本地基础设施**

```bash
git add infra/compose/dev.compose.yml infra/compose/.env.example .gitignore
git commit -m "build(platform): 建立本地平台基础设施"
```

## Task 7：建立平台 CI 门禁

**Files:**
- Create: `.github/workflows/xingjing-platform.yml`
- Test: GitHub Actions 语法与本地等价命令

- [ ] **Step 1：创建独立平台工作流**

```yaml
name: Xingjing Platform

on:
  pull_request:
  push:
    branches: [xingjing/platform-main]

jobs:
  product-baseline:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - shell: pwsh
        run: .\scripts\Test-ProductBaseline.ps1

  platform-java:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "21"
          cache: gradle
      - uses: gradle/actions/setup-gradle@v4
      - run: ./gradlew test
      - run: ./gradlew :platform-services:platform-app:bootJar
      - uses: actions/upload-artifact@v4
        with:
          name: xingjing-platform-app
          path: platform-services/platform-app/build/libs/*.jar
```

- [ ] **Step 2：运行 CI 等价命令**

Run:

```powershell
pwsh -NoProfile -File .\scripts\Test-ProductBaseline.ps1
.\gradlew.bat test
.\gradlew.bat :platform-services:platform-app:bootJar
```

Expected: 全部 PASS。

- [ ] **Step 3：提交 CI 门禁**

```bash
git add .github/workflows/xingjing-platform.yml
git commit -m "ci(platform): 建立规格与 Java 构建门禁"
```

## Task 8：记录平台地基验证证据

**Files:**
- Create: `scripts/Write-FoundationEvidence.ps1`
- Create: `docs/evidence/foundation-baseline.md`
- Test: 文档证据命令复跑

- [ ] **Step 1：创建自动生成真实证据的脚本**

创建 `scripts/Write-FoundationEvidence.ps1`：

```powershell
[CmdletBinding()]
param([string]$Output = (Join-Path $PSScriptRoot '..\docs\evidence\foundation-baseline.md'))

$ErrorActionPreference = 'Continue'

function Invoke-Check([string]$Name, [string]$Command, [scriptblock]$Action) {
    $captured = & $Action 2>&1 | Out-String
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    [pscustomobject]@{
        Name = $Name
        Command = $Command
        Code = $code
        Result = if ($code -eq 0) { 'PASS' } else { 'FAIL' }
        Summary = (($captured.Trim() -replace '\|', '\|' -replace "`r?`n", '<br>'))
    }
}

$results = @()
$results += Invoke-Check 'v9.2 规格' 'scripts/Test-ProductBaseline.ps1' {
    pwsh -NoProfile -File (Join-Path $PSScriptRoot 'Test-ProductBaseline.ps1')
}
$results += Invoke-Check 'Java 测试' 'gradlew clean test' {
    & (Join-Path $PSScriptRoot '..\gradlew.bat') clean test
}
$results += Invoke-Check 'Java 构建' 'gradlew platform-app:bootJar' {
    & (Join-Path $PSScriptRoot '..\gradlew.bat') ':platform-services:platform-app:bootJar'
}
$results += Invoke-Check 'ArcReel 后端' 'uv run python -m pytest -q' {
    Push-Location (Join-Path $PSScriptRoot '..')
    try { uv run python -m pytest -q } finally { Pop-Location }
}
$results += Invoke-Check 'ArcReel 前端' 'pnpm lint && pnpm check && pnpm build' {
    Push-Location (Join-Path $PSScriptRoot '..\frontend')
    try {
        pnpm lint
        if ($LASTEXITCODE -eq 0) { pnpm check }
        if ($LASTEXITCODE -eq 0) { pnpm build }
    } finally { Pop-Location }
}
$results += Invoke-Check '本地基础设施' 'docker compose ps' {
    Push-Location (Join-Path $PSScriptRoot '..')
    try {
        docker compose --env-file infra/compose/.env -f infra/compose/dev.compose.yml ps
    } finally { Pop-Location }
}

$jar = Get-ChildItem (Join-Path $PSScriptRoot '..\platform-services\platform-app\build\libs\*.jar') |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$jarEvidence = if ($jar) {
    "$($jar.Name): $((Get-FileHash -Algorithm SHA256 $jar.FullName).Hash.ToLowerInvariant())"
} else {
    '未生成 JAR；Java 构建结果为 FAIL'
}
$commit = (git -C (Join-Path $PSScriptRoot '..') rev-parse HEAD).Trim()
$verifiedAt = [DateTimeOffset]::UtcNow.ToString('O')
$blocking = $results | Where-Object Result -eq 'FAIL'
$blockingText = if ($blocking) { ($blocking.Name -join '、') } else { '无' }

$lines = @(
    '# 平台地基验证证据',
    '',
    '## 基线',
    "- 产品基线：XJ-WEB-9.2",
    "- 代码提交：``$commit``",
    "- 验证时间：$verifiedAt",
    "- 构建产物：$jarEvidence",
    '',
    '## 验证结果',
    '| 检查 | 命令 | 结果 | 证据摘要 |',
    '|---|---|---|---|'
)
foreach ($result in $results) {
    $summary = if ($result.Summary.Length -gt 2000) { $result.Summary.Substring(0, 2000) + '…' } else { $result.Summary }
    $lines += "| $($result.Name) | ``$($result.Command)`` | $($result.Result) | $summary |"
}
$lines += @('', '## 阻断项', $blockingText)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
Set-Content -LiteralPath $Output -Value $lines -Encoding utf8NoBOM

if ($blocking) { exit 1 }
Write-Output "Foundation evidence written: $Output"
```

- [ ] **Step 2：运行全部地基验证并生成证据文档**

Run:

```powershell
pwsh -NoProfile -File .\scripts\Write-FoundationEvidence.ps1
```

Expected: PASS，生成 `docs/evidence/foundation-baseline.md`；任一检查失败时脚本仍写入真实 FAIL 结果并以退出码 1 结束。

- [ ] **Step 3：确认项目总文档仍保持初始化里程碑**

Run:

```powershell
git diff -- docs/project/XINGJING_REQUIREMENTS_PROJECT.md
```

Expected: 无差异。平台地基是 P0 内部工作包，不触发总项目文档的里程碑更新。

- [ ] **Step 4：提交证据生成器和验证证据**

```bash
git add scripts/Write-FoundationEvidence.ps1 docs/evidence/foundation-baseline.md
git commit -m "test(platform): 记录平台地基验证基线"
```

## 自检标准

- v9.2 规格在仓内可重复校验，页数为 197，阶段为 56/121/20；
- Gradle Wrapper 固定为 9.5，Java toolchain 固定为 21；
- Spring Boot 平台服务能构建、启动并返回版本化 readiness；
- PostgreSQL Testcontainers 证明 Flyway 创建身份与工作区基础表；
- PostgreSQL、RabbitMQ、MinIO 本地服务具有健康检查；
- CI 同时验证产品基线和 Java 平台构建；
- ArcReel 原有后端和前端基线结果被如实记录；
- 总需求项目文档不因小任务更新；
- 工作树无未解释的生成文件或秘密配置。

## 计划完成后的下一计划

平台地基通过后，编写并执行 `P0-1 身份、工作区、多租户、RBAC 与审计` 实施计划。该计划以本计划创建的数据库表、请求元数据、CI 和本地基础设施为输入，不重复搭建工程。
