# 分镜确定性编辑检查实现记录

日期：2026-10-24。证据 ID：EV-M05-QUALITY-POLICY-004。范围：CR-098 分镜质量检测、故事板冻结与 CR-115 统一版本锁定的同源策略装配。状态：代码及专项验证完成，正常应用 API 已读到实际策略；不扩大为模型媒体质量、商用合规或全部 A4/D4 联合验收完成。

## 产品规则与真实边界

经本轮产品决定采用“确定性编辑检查 v1”。没有从测试常量推断生产质量，也没有伪造模型评分。冻结的 `docs/product/v9.2` 没有改动。

| 字段 | 默认值 | 结果级别 |
|---|---|---|
| `minimum_duration_ms` | 1000 | 超出建议区间 WARN |
| `maximum_duration_ms` | 30000 | 超出建议区间 WARN |
| `max_adjacent_duration_delta_ms` | 10000 | 超过建议差值 WARN |
| `max_dialogue_characters_per_second` | 12 | 超过建议文本密度 WARN |
| `require_scene_reference` | `true` | 缺场景引用 ERROR |
| `require_character_for_dialogue` | `true` | 有对白却缺角色引用 ERROR |
| `compliance_terms` | 空集合 | 未执行额外关键词筛查 |

数值只提示编辑风险，WARN 不阻断冻结。它们不是视频模型支持时长、AI 质量分数或真实口播测试结果；文本密度可能受语言影响。实际视频模型的能力范围仍由真实模型配置及执行入口校验，不通过编辑策略自动修改时长或替换模型。

空故事板拒绝冻结，不要求先生成图片或视频。缺场景、对白缺角色或配置关键词命中会阻断冻结。关键词为空不等于审核通过；正式发布仍需独立的内容、版权和授权核验。

## 配置、装配与审计

单一可信来源为 `server/xingjing_storyboard/policy_config.py`。默认 `policyId=deterministic-editorial`、`version=1`，摘要：

```text
sha256:95bfbea49570fc74c7ba4db9543943ccef1e3e16a68b47b92a6ae1c671049497
```

主应用加载一次，把同一不可变策略对象注入 M05 `StoryboardService` 和统一版本 `VersionActions`；两个入口继续调用真实领域冻结服务，保留项目权限、真实资产引用、版本 CAS、幂等及冻结后不可写。统一版本读取对空故事板返回 `canLock=false`、`EMPTY_STORYBOARD`，写入口再次拒绝。

质检和冻结的成功、失败审计均保存 `quality_policy`，含 schema、策略 ID、版本、摘要、完整规则及筛查覆盖说明。已发布策略的 ID／版本进入领域命令指纹，避免将旧版本命令当作新策略执行；旧无发布身份的规则保留原有回执合同。统一版本已完成回执继续按既有持久机制重放，不创建第二次领域冻结。

部署可通过 `XINGJING_STORYBOARD_QUALITY_POLICY_PATH` 指向可信 UTF-8 JSON 覆盖；完整格式、类型和大小约束见 [本地平台配置说明](../local-platform.md#分镜确定性编辑检查)。显式无效覆盖记录 `STORYBOARD_QUALITY_POLICY_INVALID`，不会退回宽松默认；读取及一般保存保留，质量动作失败关闭。配置只在启动时读取，不接受业务请求提供文件路径。本轮没有创建部署覆盖文件或修改运行服务配置。

`GET /api/v1/projects/{projectId}/shots?includeQualityPolicy=true` 返回 `meta.qualityPolicy`，质检动作返回相同描述。原列表请求不增加新元数据，保持旧调用合同。前端按服务端实际数值显示策略说明、版本与摘要，以及“未配置额外关键词筛查”；新增说明及阻断恢复提示有 zh/en/vi 三语。

## 验证证据

没有创建、生成或修改测试文件、测试脚本、夹具或快照，没有调用外部模型、支付、浏览器或执行服务重启。直接行为通过标准输入运行，使用隔离 SQLite 验收数据库及实际领域／仓储／ASGI 路由。

| 检查 | 结果及范围 |
|---|---|
| 直接领域＋SQL＋ASGI 断言 | **42 通过、0 失败**：严格配置、真实资产引用、WARN 冻结、缺引用阻断、空板拒绝、权限／工作区隔离、冻结后拒写、CAS／幂等、审计回读、服务重建回读、统一版本真实冻结及重放、新策略投影与旧列表兼容 |
| 既有后端回归 | **30 通过**：storyboard 领域、持久化 repository/schema、HTTP、runtime；未改测试文件。既有未分类用例在 stdin 中补本次执行分类，不写入测试源码 |
| 既有前端回归 | **14 通过，4 个文件**：StoryboardWorkspace、API、contracts、routes |
| Python 静态 | 修改范围 Ruff／format-check 通过；storyboard 与相关版本代码 basedpyright **0 errors / 0 warnings**；import-linter **1 kept / 0 broken** |
| 前端静态 | 修改范围 ESLint、完整 TypeScript 检查通过 |
| 主应用类型限制 | 单独 `basedpyright server/app.py` 仍有 **1 项既有错误**：`StoryboardRepository` 传给要求 `SqlAlchemyStoryboardRepository` 的 `audit_repository`，本轮未改该调用；不声称整个后端类型清零 |
| 差异检查 | `git diff --check` 通过，冻结产品文档未改 |

后端既有回归另有一条 FastAPI／Starlette `httpx` 弃用 warning，不是业务断言失败。最初的精确列表响应断言曾因新增元数据失败，随后把策略元数据改为显式请求投影，最终 30 项全部通过；没有修改旧断言。

最终隔离 SQL 证据：`.tmp/storyboard-policy-sql-9697rs01/runtime.db`。数据库副本包含构造的验收输入，证明实际持久化、权限、规则与回执链路，不代表真实模型生成或真实媒体质量。

主代理另从正在运行的正式项目 API 读取并确认上述策略摘要，并完成前端生产构建；没有为该核验注入预览策略。主代理的本轮全量前端 `check` 结果为 **1832 通过 / 25 失败**，保持其记录的原基线，不将专项通过表述为全仓全绿。

## 保留的验收边界

此记录更新了 [统一版本操作实现记录](unified-versions-20261023.md) 中“默认应用尚未注入生产 QualityPolicy”这一具体缺口；旧记录的其他验收结论仍属于当时范围。

尚未完成真实模型／媒体质量评估、真实口播适配、正式内容合规结论、PostgreSQL 并发下的专项演练、浏览器端逐项交互验收及完整 A4/D4 联合验收。项目预算硬限制仅完成调研，本切片没有增加预算扣减或冻结规则。
