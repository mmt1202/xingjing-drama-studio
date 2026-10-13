# M11 团队财务实现计划

**目标：** 补齐团队账单、成本、流水、套餐和项目成本的实际读取、筛选、导出及财务操作边界。

**设计依据：** `docs/project/requirements/modules/M11-团队计费、成本与权益.md` 与 B2 联合验收要求。沿用已有账本与可信身份，不另建余额真相源。用户已授权实现和推送；不创建或修改测试文件。

**实现步骤：**

- [x] 修复 `server/xingjing_billing_runtime/runtime.py` 的流水时间字段、归属维度、同一结算快照和成本查询；查询与 CSV 导出复用相同筛选规则。
- [x] 修复 `server/xingjing_billing_persistence/repository.py` 的支付订单幂等指纹、并发命令锁与流水标识作用域，补齐发票初始版本。保留真实支付渠道未验收的阻断，用户入口不能只改账本就宣称退款成功。
- [x] 完善 `server/xingjing_billing_http/router.py` 的过滤参数、财务错误响应与三语指标文本。
- [x] 完善 `frontend/src/xingjing_collaboration_commerce/` 的财务过滤、币种/套餐显示、游标隔离、过期响应保护和未就绪动作说明，五个既有页面使用同一契约。
- [x] 运行已有账务和前端测试、类型检查、lint、生产构建；以内存数据库直接验证读取、筛选、导出和重复命令，不写验证脚本文件。记录真实 PostgreSQL/Java/支付渠道尚未验收的边界。
- [x] 同步模块、缺口和验收证据，审查最终 diff，仅提交本任务文件。
- [ ] 推送跟踪的 `origin/main`。`AGENTS.md` 要求 `pnpm lint && pnpm check` 均通过；lint 已通过，check 的 5 项既有失败和全量后端类型检查的 69 个既有错误已登记为 `EV-M11-BASELINE-004`，尚未满足完整检查门禁。

**验证命令：** `uv run --no-sync python -m pytest --confcutdir tests/xingjing_billing tests/xingjing_billing -q -p no:cacheprovider`；修改 Python 文件的 Ruff；全量 BasedPyright 与 import-linter；前端 `pnpm lint`、`pnpm check`、`pnpm build`；`git diff --check`。默认 pytest 收集被现有测试缺少分类 marker 阻断，隔离执行结果不等同于全仓测试通过。

**交付边界：** 页面和本地代码验证不代替 B2 外部联合验收。未接支付渠道的创建支付/退款入口须拒绝执行并说明原因；支付回调凭证不代表渠道已获准上线。
