# 专用设置与会话续期证据（2026-10-24）

## 范围与定位

本记录说明正式 CR-096 设置入口及会话续期修复的实现和验证边界，是非冻结的工程证据，不修改或替代 `docs/product/v9.2` 产品基线，也不代表全部设置原型或全部业务流程已经验收。对应需求为 [CR-096 设置](../project/requirements/appendix/pages/creator/CR-096-设置.md)。

设置页沿用当前星镜公共壳及紫橙主操作，以分组导航和专用表单替代正式入口的 JSON 配置器。仅提供已有服务端规则与实际页面消费者支持的偏好，没有新增未生效的模型、分辨率、音色或通知开关。旧封面、版本等非 CR-096 调用分支保留。

## 专用设置实现

| 分组 | 实际内容与接口 | 权限及边界 |
|---|---|---|
| 账号与工作区 | 显示经 `WorkspaceFrame` 验证的账号名称、账号 ID、工作区名称与 ID | 不伪造邮箱、联系方式、角色或套餐 |
| 创作首页 | `creator-home` 的 `showRecent`、`showTasks`、`showInspiration`、`defaultMode`、`recentDays`、`recentLimit` | 读取需 `settings.view`，保存需 `settings.manage`；值与现有首页消费者及后端 schema 对齐 |
| 数据看板 | `data-dashboard` 的 `range`、`groupBy` | 仅有 `analytics.view` 时读取，另需 `analytics.manage` 才能保存；不使用设置管理权限代替看板权限 |
| 安全与相关入口 | 现有安全总览、登录设备、风险记录，以及项目库、创作首页和看板入口 | 离开前检查各分组未保存内容和处理中状态 |

每个偏好分组通过既有 `GET/PUT /api/v1/account/preferences` 独立读取与保存，保留原版本 CAS、稳定幂等键和服务端审计。界面文本覆盖 zh/en/vi。

- 首页偏好合并保留既有未知字段；原 `settings` resource 不被新表单覆盖。
- 看板 schema 不允许扩展字段。历史记录含未知字段时保留原数据并暂停该组编辑，不静默删除后保存；已知字段按准确类型和枚举校验，不将字符串数字或其他错误类型强制转为有效值。
- 草稿按可信 actor、workspace、resource 隔离保存在当前标签的 sessionStorage，保留原始版本、值和写入意图。浏览器无法使用该存储时给出说明，不声称草稿已持久保留。
- 保存结果不确定时保留原键、版本和 payload，冻结该次意图内容并提供明确重试。409 不覆盖输入；读取失败的普通重试也不清草稿。
- 只有用户明确选择“放弃更改并重读”才清除该组草稿。提示说明放弃本地草稿不会撤销可能已经到达服务端的保存。
- 复用 `WorkspaceFrame` 导航 guard，并保留浏览器关闭保护。新安全入口也经过未保存检查。

主要源码为 `frontend/src/xingjing_creator_settings/WorkspaceSettingsPage.tsx`、`PreferenceGroup.tsx`、`preference-state.ts`、`api.ts`、`settings.css` 和 `i18n.ts`。`CreatorSettingsPage` 保持既有 props，只将 CR-096 分流至专用页面。

## 会话续期实现

原问题是正式页面长时间停留后，业务 raw fetch 遇到过期 access token 直接失败；简单接上续期又会因 React key 和 API memo 依赖 token 而重建页面、覆盖或卸载草稿。此次没有延长后端 token TTL。

认证层使用稳定的 `authStore.sessionEpoch`，并导出 `getSessionEpoch()`、`getTokenForSession(expectedEpoch)` 与 `createAuthenticatedFetcher({workspaceId?, actorId?, fetcher?})`：

- 真正登录、退出或失效清理更换 epoch；同会话 refresh 只更新凭据，保持 epoch。账号、工作区和项目变化仍保留各自隔离边界。
- refresh、initialize、logout 的异步结果提交前检查原会话；旧请求晚到不会覆盖新登录，也不会清除新会话。logout 先使本地会话失效，再尽力撤销捕获的旧服务端会话。
- 启动时已有持久凭据，但 profile 遇到网络、503 或 429 等临时故障，initialize 结束 loading 时会按同 epoch 下当前凭据是否仍存在保留认证入口，不将其误判为未登录。该状态不授予 actor、工作区或权限；后续业务仍必须取得可信 context，读取失败时提供重试。确定失效已清除会话或 epoch 已变更时，旧初始化收尾不提交状态。
- 同源 Web Locks 串行化跨标签刷新，锁内重新读取凭据；若其他标签已完成轮换则直接复用。锁等待最长 25 秒，刷新和可信 context 读取最长 12 秒，后者包含 JSON body 读取。
- GET/HEAD 在 401 后最多恢复一次，恢复前实际读取 `/api/v1/session/context` 核对 actor/workspace；AbortSignal、会话变化或范围不匹配会阻止重试。响应状态以及 JSON/Blob 等内容读取前后也有会话检查。
- POST/PUT/PATCH/DELETE 不自动重发业务操作。续期成功后抛出稳定的 `AUTH_WRITE_RETRY_REQUIRED`，用三语说明原操作未重发，要求用户核对保留输入后明确重试；原幂等键、版本和 payload 不因续期改写。
- 网络、5xx、429 和超时不直接登出。设置和通知的错误呈现保留上述明确提示，不仅显示内部错误码或泛化网络错误。
- API memo 与页面身份 key 使用稳定 epoch 和业务作用域；StoryStarter 不再捕获首次 token。封面、版本、首页、看板、支付、内部审片、通知、商单、模板及员工审片的内部 API 也不因同会话续期重建。媒体 Blob 仍可随 token 更新取消、释放和重新读取，不重建父表单。

REST transport 接入首页、项目/总控/分集、资产/分镜、生成、音频/剪辑、合规、设置、封面、版本、支付、看板、后台、模型、模板市场、商单、通知和员工审片。独立 identity memoryTokenStore 的 401 合同、客户外链认证及 SSE 合同保留。主要认证源码为 `frontend/src/utils/auth.ts`、`frontend/src/stores/auth-store.ts` 和 `frontend/src/api.ts`。

## 已执行的源码与静态验证

| 验证 | 实际结果 | 证明范围 |
|---|---|---|
| 设置模块 ESLint、前端类型检查 | 通过 | 设置源代码静态规则及类型 |
| 认证与业务 transport 的局部 ESLint、前端类型检查 | 通过 | 改动模块及公共壳接线的静态检查；不是浏览器续期验收 |
| 专项既有回归 | 实际命中 6 个文件、27 个用例，全部通过 | identity、项目、资产分镜、后期及协作已有接口/组件合同；未新增或修改测试 |
| 设置源码 stdin 边界核对 | 通过 | 严格字段类型、未知字段保留、账号/工作区/分组隔离、原意图恢复、CAS 和 409 解析 |
| 认证生产源码 stdin 边界核对 | 21 个场景通过（原 15 个加启动边界 6 个） | 下列可控时序及异常边界，不等同自然过期浏览器验证 |
| `git diff --check` | 通过 | 改动格式检查 |

专项既有回归实际命中的文件为 `xingjing_identity/api.test.ts`、`xingjing_identity/components.test.tsx`、`xingjing_creator_projects/api.test.ts`、`xingjing_assets_storyboard/api.test.ts`、`xingjing_postproduction/api.test.ts` 和 `xingjing_collaboration_commerce/api.test.ts`，均位于 `frontend/src/` 下。

认证 stdin 核对直接载入生产源码，在内存中控制请求完成顺序，覆盖：安全读取恢复一次；写请求不重放且明确重试保持原 body/key；同标签并发合并；503、429、离线不登出；旧 refresh 的成功/401、旧 initialize、旧 logout 晚到不覆盖新登录；两个独立模块共享 Web Locks 仅刷新一次；无锁分支保守保留会话；workspace 改变后拒绝重放读取；AbortSignal；以及切换登录后拒绝使用旧响应状态或内容。

初始化收尾补丁重新执行上述 15 个场景，并追加 6 个启动场景：初始 `isAuthenticated=false` 且已有持久凭据时，profile 的 503、429、离线三个分支均保留原 token、epoch 和认证入口并结束 loading；有 refresh token、无 refresh token的两种确定 401 分支均清理会话；旧 profile 503 在新账号登录后晚到，不覆盖新 token、epoch、名称或认证状态。确定 refresh 失效的场景使用串行刷新锁，未将无锁保守分支混称为确定失效。该补丁的 auth-store ESLint 和前端类型检查通过。

上述 stdin 核对没有生成测试脚本、夹具或快照文件。

## root 提供的正式 UI 与全量检查结果

以下为根代理在正常 React → Python → Java/PostgreSQL 链路上的正式浏览器操作结果，由根代理提供；本子任务未操作浏览器或重启服务：

1. 设置页将 `recentLimit` 从默认 6 改为 3，保存为 v1；整页 reload 后读取仍为 3。随后改回 6，保存为 v2。
2. 设置存在未保存更改时，点击项目库被导航 guard 拦截，输入保留。
3. 通知真实空状态、全部/未读筛选可用；临时通知偏好草稿会阻止关闭，取消草稿后可以关闭。
4. 全量 `pnpm check` 为 **1832 passed / 25 failed**，共 168 个文件中 165 通过、3 失败，与用户已接受的基线完全一致，无新增失败。25 项为 19 项旧品牌引导断言、4 项旧登录断言和 2 项旧根路由断言；命令整体退出码仍为 1，不记作全量通过。
5. 生产构建通过。

启动初始化收尾补丁完成后，根代理再次运行全量检查与生产构建，结果仍为 1832 通过／25 项既有失败以及构建成功；最终日志为 `final-check.log` 与 `final-build.log`。本子任务的局部 ESLint、类型检查和 21 场景 stdin 核对作为另外的边界证据保留。

本地日志与截图位于忽略的 `output/creator-consistency-20261024/`：`implementation-check.log`、`implementation-build.log`、`settings.png`、`settings-saved.png` 和 `notifications.png`。本记录不包含账号凭据、Authorization、refresh token 或其他认证秘密。

## 正式自然过期读取恢复

根代理于 15:07:33 通过正式登录入口建立会话，在首页保留未提交的项目名称与故事素材，没有延长 TTL 或修改凭据。15:22:50 打开通知时，正式服务日志依次记录通知 GET 401、refresh POST 200、可信 session/context GET 200、原通知 GET 200；页面持续停留首页，通知正常显示，原名称与全文仍在。随后点击设置仍被未保存 guard 拦截。最后清空临时输入并正常进入设置，未创建项目、任务或生成记录。

这证明自然到期后的真实 GET 恢复与当前首页草稿保留；不将它扩展为支付／生成写入过期重试或所有页面均已实测。截图为 `output/creator-consistency-20261024/session-renewed-draft.png`，服务日志不包含令牌正文。

## 尚未完成的验证与限制

- 支付／生成写入的过期明确重试与全浏览器矩阵尚未实测；写不自动重放、作用域及并发异常的可控证据来自 21 个 stdin 场景。
- 无 Web Locks 时仅有标签内 single-flight 和提交比较保护。该分支已验证不会仅凭一次 refresh 401 清除可能正在更新的凭据，但不保证旧浏览器跨标签刷新完全串行；明确失效与并发轮换无法确认时采用保守保留、提示重试。
- 新设置页只编辑真实消费者支持的偏好。模型、分辨率、音色、媒体展示或默认工作区等原型能力未因通用 JSON resource 能保存任意字段而被宣称生效；账号资料在此只读，安全操作继续由原有页面承载。
- 本轮没有修改冻结需求、roadmap 或测试，也未执行外部付费生成、收款、退款或生产部署。
