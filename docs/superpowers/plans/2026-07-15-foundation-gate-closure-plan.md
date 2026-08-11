# 平台地基门禁闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用当前提交上的真实 Java、Ubuntu/Python、React 和本地基础设施结果替换过期失败证据，关闭 GAP-001/BLK-001，但不提前完成尚未验收的 S01 能力。

**Architecture:** 继续以 `scripts/Write-FoundationEvidence.ps1` 作为唯一证据汇总器；Ubuntu/Python 基线由 `scripts/Test-ArcReelBackendBaseline.ps1` 生成 JUnit，汇总器可复用该 JUnit，避免重复安装容器依赖。前端始终由 Corepack 按 `frontend/package.json#packageManager` 固定 pnpm 10.33.2，避免机器全局 pnpm 11 改变配置语义。

**Tech Stack:** PowerShell 7、Gradle 9.5/Java 21、Docker、uv/Python 3.12/pytest、Corepack/pnpm 10.33.2、React/Vitest/Vite。

---

### Task 1：确认失败证据能够被当前门禁复现和纠正

**Files:**
- Read: `docs/evidence/foundation-baseline.md`
- Read: `.xingjing-data/arcreel-backend-pytest.xml`
- Test: `scripts/Test-RequirementsLedger.ps1`
- Test: `scripts/Test-ProductBaseline.ps1`

- [ ] **Step 1：记录 RED 基线**

确认旧证据仍包含 `Java 测试`、`ArcReel 后端`、`ArcReel 前端` 三项 FAIL，且 `GAP-001`、`BLK-001` 仍为生效状态。

- [ ] **Step 2：校验 Ubuntu JUnit 不是伪造通过**

Run:

```powershell
[xml]$x = Get-Content .\.xingjing-data\arcreel-backend-pytest.xml -Raw
$s = @($x.testsuites.testsuite)
($s | Measure-Object tests -Sum).Sum
($s | Measure-Object failures -Sum).Sum
($s | Measure-Object errors -Sum).Sum
```

Expected: `tests=5879`、`failures=0`、`errors=0`。

- [ ] **Step 3：复验需求范围门禁**

Run:

```powershell
pwsh -NoProfile -File .\scripts\Test-RequirementsLedger.ps1
pwsh -NoProfile -File .\scripts\Test-ProductBaseline.ps1
```

Expected: 19 个模块、197 个页面、197 个附录、97 项共享能力、19 个联合验收包全部通过；阶段数量为 56/121/20。

### Task 2：生成同一工作树上的完整平台地基证据

**Files:**
- Modify: `docs/evidence/foundation-baseline.md`
- Test: `scripts/Write-FoundationEvidence.ps1`

- [ ] **Step 1：用现有 Ubuntu JUnit 运行证据汇总器**

Run:

```powershell
pwsh -NoProfile -File .\scripts\Write-FoundationEvidence.ps1 `
  -ExistingArcReelJunit .\.xingjing-data\arcreel-backend-pytest.xml
```

Expected: 产品规格、Java 测试、Java 构建、ArcReel Ubuntu 后端、ArcReel 前端和本地基础设施全部 PASS，脚本退出码为 0。

- [ ] **Step 2：校验前端使用项目锁定版本**

Run:

```powershell
Set-Location .\frontend
corepack pnpm --version
corepack pnpm install --frozen-lockfile
corepack pnpm check
corepack pnpm build
```

Expected: pnpm 为 `10.33.2`；冻结安装、103 个测试文件、912 项测试和生产构建通过。

- [ ] **Step 3：检查证据没有把 Windows 结果冒充 Linux 结果**

Run:

```powershell
Select-String -Path .\docs\evidence\foundation-baseline.md `
  -Pattern 'Linux/Python 3.12 JUnit|5879|FAIL|阻断项'
```

Expected: 后端证据明确标注 Linux/Python 3.12 JUnit；验证表无 FAIL；阻断项为“无”。

### Task 2.5：消除前端脚本对机器全局 pnpm 的隐式依赖

**Files:**
- Modify: `frontend/package.json`
- Test: `frontend/package.json` scripts

- [ ] **Step 1：确认 RED 输出**

运行 `corepack pnpm check`，确认嵌套的裸 `pnpm typecheck`/`pnpm lint` 会在当前 Windows 环境调用全局 pnpm 11，并输出 `package.json#pnpm is no longer read` 警告。

- [ ] **Step 2：让聚合脚本直接调用本地二进制**

将脚本调整为：

```json
"build": "tsc --noEmit && vite build",
"check": "tsc --noEmit && eslint . && vitest run"
```

单项 `typecheck`、`lint`、`test` 脚本保持不变；依赖版本、overrides 和锁文件不变。

- [ ] **Step 3：验证 GREEN 且无 pnpm 配置警告**

Run:

```powershell
Set-Location .\frontend
$output = corepack pnpm check 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { throw $output }
if ($output -match 'pnpm.*field.*no longer read') { throw '仍调用了机器全局 pnpm' }
corepack pnpm build
```

Expected: 103 个测试文件、912 项测试和生产构建通过，且不存在 pnpm 配置位置警告。

### Task 3：同步需求状态、缺口和验收证据

**Files:**
- Modify: `docs/project/requirements/04-验收与测试证据.md`
- Modify: `docs/project/requirements/05-变更与缺口清单.md`
- Modify: `docs/project/requirements/shared/S01-工程规格与可观测地基.md`

- [ ] **Step 1：更新 FOUND-002 和 FOUND-003 的真实证据**

将 Java 工程和 ArcReel 创作基线的状态更新为“待验收”，证据引用 `docs/evidence/foundation-baseline.md`。不得把 FOUND-004～FOUND-010 或 S01 整体标记为已验证。

- [ ] **Step 2：关闭 GAP-001 和解除 BLK-001**

把 GAP-001 状态改为“已关闭”，关闭证据指向最新平台地基证据；把 BLK-001 状态改为“已解除”。保留历史描述，不删除记录。

- [ ] **Step 3：更新证据台账**

将 `EV-FOUND-001-GATE-001` 的范围纠正为平台地基门禁，记录 Java、Ubuntu/Python、React、构建和基础设施全部通过；同时明确该证据只关闭基线阻断，不代表 19 个业务模块完成。

### Task 4：执行回归并进入 A0 实施计划

**Files:**
- Verify: `docs/project/requirements/`
- Create later: `docs/superpowers/plans/2026-07-15-a0-identity-tenant-foundation-plan.md`

- [ ] **Step 1：运行台账回归**

Run:

```powershell
pwsh -NoProfile -File .\scripts\Test-RequirementsLedger.ps1
pwsh -NoProfile -File .\scripts\Test-ProductBaseline.ps1
```

Expected: 两项均 PASS。

- [ ] **Step 2：检查工作树边界**

Run:

```powershell
git status --short
git diff --check
```

Expected: 没有秘密、构建产物或无关文件进入改动；现有用户改动保持不变。

- [ ] **Step 3：进入下一批**

基于 M01、S02、S07 和 A0 联合场景编写身份、工作区、多租户、RBAC、会话撤销与审计的 TDD 实施计划。不得因平台基线转绿而提高 197 个页面的完成度。

## 自检

- 每项 PASS 都有可复跑命令和机器生成证据；
- Ubuntu 后端 5879 项测试与 Windows 平台差异分开记录；
- 前端通过 Corepack 使用 pnpm 10.33.2；
- GAP-001/BLK-001 的关闭不会误关合规、支付等外部门禁；
- S01 仍保持“开发中”，直到 FOUND-004～FOUND-010 及 A0 联合验收完成。
