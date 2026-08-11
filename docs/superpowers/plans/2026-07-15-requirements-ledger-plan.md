# 星镜剧创全需求与进度台账 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立覆盖 197 个页面和全部共享平台能力、可自动校验且必须关联验收证据的需求与开发进度台账。

**Architecture:** 以 `docs/product/v9.2` 为需求唯一事实源，在 `docs/project/requirements` 下按治理、总览、页面、共享能力、证据、变更六类文件拆分。页面台账由追踪矩阵确定性生成，手工维护状态与证据的文件使用稳定 ID；校验脚本检查数量、状态枚举、证据门禁和文档互链。

**Tech Stack:** Markdown、PowerShell 7、Git、现有 v9.2 追踪矩阵与逐页规格。

---

### Task 1：建立台账治理与手工维护文件

**Files:**
- Create: `docs/project/requirements/00-使用与状态规则.md`
- Create: `docs/project/requirements/01-总览与里程碑.md`
- Create: `docs/project/requirements/03-平台共享能力台账.md`
- Create: `docs/project/requirements/04-验收与测试证据.md`
- Create: `docs/project/requirements/05-变更与缺口清单.md`
- Modify: `docs/project/XINGJING_REQUIREMENTS_PROJECT.md`

- [ ] **Step 1：写明唯一状态模型与证据门禁**

状态仅允许 `未开始/开发中/待验收/已验证/阻断`；完成率只统计“已验证”。

- [ ] **Step 2：建立总览、共享能力、验收和变更台账**

每条共享能力必须有稳定 ID、优先级、需求、验收条件、状态、证据和阻断原因。

- [ ] **Step 3：把旧总进度表改为新台账入口**

旧文件保留项目背景和权威来源，但删除“小需求不更新”的规则，链接到六份新台账。

### Task 2：从追踪矩阵生成 197 页需求台账

**Files:**
- Create: `scripts/Sync-RequirementsLedger.ps1`
- Create: `docs/project/requirements/02-页面需求台账.md`

- [ ] **Step 1：解析追踪矩阵的九列结构**

脚本读取 `页面ID/系统/路由/阶段/业务域/主要对象/权限/API/测试`，拒绝重复 ID 和非法阶段。

- [ ] **Step 2：生成逐页台账**

每行保留完整需求映射，并增加 `状态/实现证据/测试证据/运行证据/阻断原因/最后更新` 字段；初始状态统一为“未开始”，避免未经审计即报完成。

- [ ] **Step 3：生成按端、阶段和业务域的统计**

输出必须复核总数 197、四端数量 124/18/50/5、阶段数量 56/121/20。

### Task 3：增加自动校验并复验

**Files:**
- Create: `scripts/Test-RequirementsLedger.ps1`
- Modify: `docs/project/requirements/00-使用与状态规则.md`

- [ ] **Step 1：校验文件完整性与页面覆盖**

运行：`pwsh -NoProfile -File .\scripts\Test-RequirementsLedger.ps1`

预期：输出 `requirements ledger verified: pages=197; P0=56; P1=121; P2=20`。

- [ ] **Step 2：校验状态和已验证证据**

发现非法状态，或“已验证”条目缺少实现、测试、运行证据时必须失败。

- [ ] **Step 3：执行现有产品基线校验**

运行：`pwsh -NoProfile -File .\scripts\Test-ProductBaseline.ps1`

预期：输出 `v9.2 baseline verified: 197 pages; P0=56, P1=121, P2=20`。

