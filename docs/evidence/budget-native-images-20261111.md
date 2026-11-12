# 预算限制、原生图片与未知请求恢复

范围：项目和单集消费限制、M06/M07/M08 预算准入、OpenAI 原生图片执行、音频请求恢复。实现和隔离验证始于 2026-10-28，交付检查更新至 2026-11-12。完整产品目标仍以 [验收状态](../project/product-acceptance-status.md) 为准。

## 产品行为

- 项目预算及经确认的单集预算存入 SQL 策略。新任务在同一事务内校验项目、剧集、币种、实际消费、有效预留和本次最大消费，再建立任务与 hold。三类生产费用合并计算，素材被后续成片引用不再重复记费。
- 预算 `0` 表示不允许新增付费任务，空值表示未设上限。预算变化需要明确保存，不参与剧集信息自动保存；旧文件中的单集计划预算要显式核验才能启用。降到已消费与有效预留之下会拒绝，并保留用户正在编辑的值。
- 单集身份由固定来源证明：M05 固定分镜、M08 固定时间线、M18 固定封面计划关联的成片与时间线。普通生成及首次音频任务明确选择剧集或有权限的项目共有制作。音轨首次付费绑定后保持归属，重试不得换集。
- 历史未归属费用继续计入项目总额，不能自动归到当前集；有正金额的未归属历史费用时，不能直接开启单集限制。历史零金额、已释放记录不再误触发未归属提示。
- 预算修改必须同时具备账单读取和预算管理权限。仅有管理权限的自定义角色不能把隐藏金额当成空预算覆盖；不携带预算的普通信息保存仍可执行。迁移只升级仍保持旧默认权限的内置 owner/admin，自定义角色保持原样。确定性审计记录使重复迁移保留管理员后续主动撤销的权限。
- 总控刷新后重新读取预算余额，加载期间隐藏旧额度；迟到的旧响应不能覆盖最新数据。预算权限、对象缺失、超限及依赖不可用分别保留 403/404/409/503，并给出三语错误原因。
- 剧集文件写入和 SQL 提交之间发生异常时，原请求回放必须匹配保存时的预算前置版本。旧回执不能覆盖后来保存的新预算，既有成功幂等请求也不会重复修改预算。
- M07 供应商结果未知时继续保留预留金额；同一请求只由一个持久化领取者发出，未知状态不会因 TTL 或取消自动释放。浏览器在发送付费请求前保存精确 key、payload 和版本；401 不自动重发写入，手动核对和刷新恢复沿用原请求，跨登录会话隔离。
- 生成和音频表单从真实项目剧集读取费用归属，读取失败时阻止新任务提交。模型成功价按所选模型自己的币种展示；模型或币种缺失时不借用默认模型价格。

## 原生图片执行

- 新增默认关闭的 OpenAI 文生图及精确参考图编辑执行器，复用底层图片后端，直接落入 M06 任务、产物和账本。视频、局部重绘、超分不属于本通道。
- 精确授权绑定工作区与已存在的加密凭据 ID。主凭据库、模型库、生成库必须相同；不根据 active 状态猜另一把密钥，不接受用户请求扩展授权。
- 队列、领取、发送、已取得文件及本地发布状态持久化。准备阶段再次核验输入字节与凭据版本；发送后不确定结果保留费用，不自动重新调用供应商；已取得文件但发布失败时仅恢复本地发布。
- 报价必须显式提供版本化成功单价，范围为零至最大预留金额。成功只结算一次并释放差额，不将预估金额或供应商美元成本当成用户实际消费。
- 原生执行 ID、上游请求追踪号与供应商 job ID 分开保存。旧报价命令仍按原始 payload 计算幂等指纹，新可选字段不改变旧请求。
- 配置见 [本地部署说明](../local-platform.md)。实际部署开关、密钥和真实付费均未自动启用。

## 可追溯验证

以下为直接执行验证，未新增或修改测试文件、测试脚本、夹具或快照；外部响应使用替身，不代表实际付费供应商验收。

| 验证范围 | 结果 | 本地证据 |
|---|---|---|
| 项目/单集准入、费用聚合、迁移、可信来源 | PostgreSQL 19 + 6 + 7 项及来源 SQL 9 项通过 | `.tmp/budget-core-postgres.log`、`budget-aggregate-postgres.log`、`budget-migration-postgres.log`、`budget-source-sql.log` |
| M06/M07/M08 跨模块并发、重试及未知结果 | PostgreSQL 33 项通过 | `.tmp/budget-audio-editing-pg-final.log` |
| 文件成功但 SQL 回滚、旧请求与后来预算冲突 | PostgreSQL + 临时真实文件 10 项通过 | `.tmp/budget-file-recovery-verified.log` |
| 内置角色、自定义角色、重复迁移和后续撤权 | PostgreSQL 14 项通过 | `.tmp/budget-builtin-permissions-postgres.log` |
| 封面固定成片归属、伪造参数、旧请求回放 | PostgreSQL 12 项通过 | `.tmp/budget-cover-lineage-postgres.log` |
| 原生图片双 worker、未知结果、本地发布恢复 | 隔离 SQL 30 项通过 | `.tmp/native-image-joint-20261028.log` |
| 实际 SDK 请求构造及加密精确凭据 | MockTransport/SQL 16 项通过 | `.tmp/native-sdk-credentials-20261028.log` |
| 原生文生图→产物→参考编辑、审计与报价命令 | 隔离 SQL 30 项通过 | `.tmp/native-exact-input-audit-final-20261028.log` |
| 生产装配、同库与授权拒绝 | 5 项通过 | `.tmp/native-factory-final-20261028.log` |
| 预算选择、生成请求恢复、剧集明确保存、多币种 | React 17 + 18 + 5 项通过 | `.tmp/budget-picker-generation-behavior.log`、`budget-episode-behavior.log`、`budget-generation-currency-behavior.log` |
| 音频认证及原请求恢复 | auth/API 10 项、付费恢复 React 11 项、预算 UI 10 项通过 | `.tmp/budget-audio-auth-behavior.log`、`budget-audio-paid-ui-behavior.log`、`budget-audio-ui-behavior-final.log` |
| 预算与未知状态错误多语言 | 15 项错误投影及 2 项真实换行诊断通过 | `.tmp/budget-errors-newlines-20261028.log` |
| 隐藏金额角色、普通信息保存、零预算与解除上限 | 真实 SQL/服务 9 项通过 | `.tmp/budget-final-review-hidden-cap-fixed.log` |
| 预算刷新、加载遮蔽旧值及迟到响应 | React 5 项通过 | `.tmp/budget-final-review-usage-refresh-fixed.log` |
| 预算错误 HTTP 状态、翻译及既有接口语义 | ASGI 120 + 15 项及既有接口 18 项通过 | `.tmp/budget-http-status-final-20261111.log`、`budget-http-management-final-20261111.log`、`budget-http-existing-final-20261111.log` |

2026-11-12 的全量前端检查仍为 1832 通过、25 失败，失败集合位于已有 OnboardingTour、LoginPage、App 三个文件；与上一批逐项比较失败标题一致，Python 类型错误的文件/规则/消息集合也一致。TypeScript、ESLint、生产构建通过。最终证据为 `.tmp/budget-release-final-check-20261112.log` 与 `.tmp/budget-release-final-build-20261112.log`。

2026-11-12 Python 全库扫描为 69 errors / 1079 warnings，修改的 36 个 Python 文件单独检查无错误，Ruff 检查与格式检查通过，import 分层契约通过。既有生成/持久化 36 项、原生图片/生成 56 项、音频 53 项、剪辑持久化 12 项、音频前端 6 项均通过，存在重叠，不相加作为唯一测试总数。分集及三语相关已有检查为 105 通过、2 项 Windows 环境失败：一项由测试 `write_text` 将已有 CRLF 再转换成双 CR，另一项缺创建符号链接权限；实际按字节保存 CRLF/LF 的源文指纹一致。三语一致性 12 项在独立临时目录重跑通过；默认 pytest 临时目录因 Windows 权限报错，没有更改系统权限或测试文件。证据为 `.tmp/budget-release-types-all-20261112.json`、`budget-release-python-20261112.log`、`budget-release-imports-20261112.log`、`budget-release-i18n-isolated-20261112.log`。

## 本地运行证据与未完成项

2026-10-28 本地备份 `postgres-0873cb76e51a5ca2.dump` 后，迁移到 `f4c9cc34000e`，正式 API 验证 7 份剧本完整内容不变，112 集原投影不变，预算选项和权限已读取；模型目录仍为空、提交不可用。证据 `.tmp/budget-native-live-readonly-20261028.json`。这是当时的运行证据，不能推断后续服务一直运行。

2026-11-12 重新启动前确认 Java、FastAPI、Vite 均已停止；正常启动 Docker 后，由现有启动器完成数据库备份 `postgres-2f1698e15f5a6e39.dump`、归档读取检查和服务启动。当前迁移仍为 `f4c9cc34000e`。正式只读 API 再次确认 7 份剧本、112 集原投影及原始内容摘要完全一致，预算选项为 112 集，预算权限有效。证据 `.tmp/budget-release-start-ready-20261112.log`、`budget-release-live-readonly-20261112.json`。归档检查不代表完成备份恢复演练。

正式浏览器验证了总控的消费限制与剩余额度、手动刷新后的更新时间，以及生成表单的 112 集费用归属选项和项目共有制作。选择第一集不会自动提交；缺少真实模型配置时提交按钮保持禁用。已恢复未选择状态，没有创建任务或修改项目预算。截图 `output/budget-release-20261112/generation-budget.png`。同时复现并修正 404“返回首页”误入旧大厅的问题，点击后进入当前工作区的创作首页，截图 `output/budget-release-20261112/home-return.png`。25 份 v9.2 冻结文件摘要不变，没有创建或修改测试文件。

仍未完成：原生视频；未知结果的管理员证据核验与恢复；路由取得并发额度后、任务事务提交前崩溃造成的额度残留；历史未归属费用的受控核验界面；真实首集生成、音频、渲染、结算与导出联合验收。长文本语义映射、局部恢复、实际支付和发布仍在完整需求范围内。
