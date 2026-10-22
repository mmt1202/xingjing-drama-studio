# M12 审片批注与会话闭环实现计划

**目标：** 客户能可靠地提交批注、接收项目组回复、刷新同一审片会话并退出；验收与签收严格使用交付记录版本。

**设计依据：** 已有 M12/B3 需求与用户“继续做功能”授权。保留现有 SQLAlchemy 仓储、受控媒体和链接权限；以内联方式逐步实现，不新增或修改测试文件。

**方案：** 在现有评论模型中复用 `parent_comment_id` 实现员工回复，新增工作区内受信任回复入口；客户上下文支持携带现有会话刷新，退出后清除前端凭据并停留在退出页。前端将客户、员工和共用组件拆开，补齐 zh/en/vi 文本，保留现有视觉结构。

- [x] `server/xingjing_review/service.py`：员工回复按 `review.manage` 和工作区校验；回复绑定同一链接/成片；已签收交付拒绝改写验收结论。
- [x] `server/xingjing_review_runtime/runtime.py`：员工回复接入仓储与审计；增加会话刷新；退出可重试且只记录一次撤销；保持媒体及截图的会话授权。
- [x] `server/xingjing_review_http/router.py`：接通员工回复、会话刷新、明确口令验证状态、无缓存响应和有上限的截图读取；未配置依赖时对应路由返回 503。
- [x] `frontend/src/xingjing_review_client/api.ts`：区分口令验证与可用上下文；携带会话刷新；可取消读取；不确定失败重试复用幂等键；员工回复与既有接口共用错误解析。
- [x] `frontend/src/xingjing_review_client/`：客户提交成功才清空草稿，上传期间防止重复点击；验收使用交付版本；退出停止自动登录；员工能回复并处理批注；列表/详情丢弃过期响应；新旧文案统一三语。
- [x] 验证：运行已有 `tests/xingjing_review` 与全量前端测试；用内存数据库和实际 HTTP 直接验证客户批注、员工回复、作用域拒绝、验收版本、签收终态、刷新与退出后的媒体拒绝；使用 stdin 验证，不写测试脚本文件。执行 Ruff、专项 BasedPyright、import-linter、前端 lint/typecheck/build，并对已知全量失败单独记录。
- [x] 更新 M12/B3 台账和证据，审查改动范围及推送门禁。交付按此前授权提交并推送 `origin/main`，仅沿用用户已明确接受的相同基线问题。

**验证结果：** 审片已有测试 5/5，Ruff、专项 BasedPyright（0 errors）、import-linter（1 kept / 0 broken）、前端 lint/typecheck/build 通过。实际 HTTP 检查及本地生产构建的客户审片浏览器流程通过；完整记录见 `EV-M12-COLLAB-003`、`EV-M12-BROWSER-004`、`EV-M12-VALIDATION-005`。全量前端仍为 1852 项通过、5 项既有失败，全量后端类型仍为 69 个既有错误，未新增或修改测试文件。

**验收边界：** 本轮交付批注往返、会话和验收状态的代码闭环。真实 PostgreSQL/Java 部署、外链通知投递、对象存储部署和 B3 外部联合验收仍需单独完成；不发送客户通知。
