# 生成任务核验接入与操作约定

专用核验流程用于恢复已有生成结果或依据确定的供应商结果结束原任务，不发起新的付费生成。实现位置为 `server/xingjing_generation_recovery/` 与 `frontend/src/xingjing_generation_recovery/`。管理员页面：`/app/workspaces/{workspaceId}/admin/admin-tasks`。

## 角色与操作

- 查看任务、费用、回执和媒体需要同时具备 `admin.business.view`、`admin.finance.view`。
- 预览和执行还需要 `admin.business.manage`、`admin.finance.manage`。页面刷新、媒体读取、原请求重发均重新校验当前身份与工作区；工作区所有者不自动获得平台管理权限。
- 管理员先查看固定输入版本、执行身份、占用费用和回执，选择回执后预览影响，填写 5–1000 字处理说明，再确认处理。没有可信回执时不能直接填写“成功”“失败”或退款金额。
- 预览有效期为 10 分钟，绑定操作者、任务版本、账务状态和回执。任务变化、文件变化、相反回执、新执行认领或预览过期时须重新读取和预览。
- 响应丢失时，页面保留该登录会话、工作区及操作者范围内的原请求；重新打开页面不会自动提交。用户选择“重发原请求核对结果”后发送完全相同的处理说明、预览 ID 和幂等键。服务端返回已入库结果，不再结算。
- 旧任务缺少可靠执行身份但仍占用费用时进入“待关注”，详情明确阻止推测性退款。历史预算归属和旧模型计数修复仍需独立的证据核对流程。

## HTTP 入口

管理接口使用现有登录认证，必须携带与真实会话一致的 `X-Workspace-Id`。可用 `Accept-Language: zh / en / vi` 获取错误文案。

| 方法 | 路径（均以 `/api/v1` 开始） | 用途 |
|---|---|---|
| GET | `/admin/generation-recovery?state=attention&q=&offset=0&limit=20` | 当前工作区任务；state 支持 attention、handled、all，limit 为 1–50 |
| GET | `/admin/generation-recovery/projects/{projectId}/tasks/{taskId}` | 原请求、固定输入、执行身份、费用、回执、处理记录及阻止原因 |
| POST | `/admin/generation-recovery/projects/{projectId}/tasks/{taskId}/preview` | 请求体为 `receiptId` 与 `expectedVersion`；只保存预览，不结算 |
| POST | `/admin/generation-recovery/projects/{projectId}/tasks/{taskId}/resolve` | 请求体为 `previewId` 与 `reason`，必带 `Idempotency-Key` |
| GET | `/admin/generation-recovery/projects/{projectId}/tasks/{taskId}/receipts/{receiptId}/artifacts/{index}` | 鉴权读取已核验媒体，响应为 private/no-store |
| POST | `/internal/generation-recovery/receipts` | 只接收可信部署适配器的签名回执；不使用普通管理员页面输入作为证据 |

管理接口使用现有 `data` / `meta.requestId` 响应格式；失败返回 `error.code` 和三语 `error.message`。旧通用后台任务补偿入口返回 `RECOVERY_WORKFLOW_REQUIRED`，不能绕过本流程改写任务快照。

## 可信适配器的责任

签名仅证明报文来自持有部署密钥的核验适配器，**不等于供应商原生签名，也不证明人工填写的内容真实**。生产接入方必须先查询、导出或核对供应商原始记录，固定原请求、模型、作业和实际费用，再生成回执。该供应商查询/核对适配器及真实故障演练尚未完成部署验收。

`provider_not_accepted` 必须表示确定没有受理、没有收费，并确保原请求及其重试不会随后被受理，例如由网关持久拒绝该原请求身份。一次“查无记录”、网络超时、暂时不可见或本地没有收到响应均不满足条件。不能在上游请求仍可能执行时凭此释放费用。

已受理任务使用 `provider_terminal`，必须取得确定终态。运行中或结果未知不提交终态回执；保持原任务费用和模型占用。恢复不提供自动切换供应商、换键再生成或替管理员猜测费用的能力。

签名设置：

- 密钥读取部署侧现有 `XINGJING_M06_PROVIDER_CALLBACK_SECRET`，不得下发到浏览器。
- 请求体为 UTF-8 JSON 原始字节，最大 64,000 字节。先序列化，再对这些实际发送的字节签名；不能签名后重新序列化。
- `HMAC-SHA256(secret, b"generation-recovery/v1\n" + raw_body)`，其中 `\n` 为单个换行字节。独立域阻止普通生成回调签名重放。
- `X-Generation-Recovery-Signature: sha256=<64 位小写十六进制摘要>`。未配置密钥返回 503；签名错误返回 401。
- 内部入口应只开放给可信核验适配器。适配器只能读写其部署授权的结果目录，不能接受普通用户指定服务器文件路径。

## 回执字段与文件

字段采用 camelCase，额外字段会被拒绝。

| 字段 | 约束 |
|---|---|
| version / receiptId | version 为 1；receiptId 是固定 UUID，相同回执重试沿用原 ID |
| workspaceId / projectId / taskId | 精确匹配原任务及项目所属工作区；不能跨范围引用 |
| executionId / kind | 原生图片执行 ID 或已绑定网关执行 ID；kind 为 native_image / gateway |
| requestFingerprint | 原始制作请求的 SHA-256，不根据当前编辑中的表单重新计算 |
| providerId / modelId | 创建任务时固定的供应商和模型 |
| providerJobId | 网关已受理终态必须有真实作业 ID；原生图片及未受理回执不得编造作业 ID |
| upstreamRequestId / providerReceiptRef | 可追溯的供应商请求及查询/回执引用，不能用本地随机值冒充供应商记录 |
| evidenceKind / status / attempt | 确定证据类型、succeeded 或 failed、匹配原调用次数 |
| currency / estimatedMinor / actualMinor / pricingVersion | 原币种与价格版本，整数最小货币单位；实际费用不得超过原预留 |
| completedAt / observedAt | 带时区的时间；创建时间 ≤ 完成时间 ≤ 观察时间，观察时间不能超前服务端 5 分钟以上 |
| outputs | 成功必须提供 1–8 个已有文件；失败不得携带文件 |

原生图片成功只接受 1 张图片，并按原执行固定成功价结算；失败实际费用必须为 0。网关失败可携带原合同允许且不超过预留的已确认费用，预览会明确展示收费与释放额。未受理回执必须失败、零费用且无作业 ID。

每个 output 包含 `sourcePath`（执行目录内相对路径）、`sha256`、`sizeBytes`（1–512 MiB）及 `mimeType`。允许 PNG/JPEG/WebP 或 MP4/WebM/QuickTime，且须匹配原任务媒体类型。网关根目录为配置的供应商结果根目录下 `{workspaceId}/{projectId}/{taskId}`；原生图片为其执行根目录下 `{executionId}`。不允许越界、绝对路径或目录代替文件。

接收端将文件复制到独立对象名，校验实际字节摘要、大小和媒体格式；图片实际解码验证，视频通过 ffprobe 检查容器、视频流及有效时长。预览、执行和预览媒体读取时再次核对。失败或并发导入可能留下未引用对象，当前没有自动垃圾回收；部署方需制定保留和受控清理方案，不得删除仍被回执或正式产物引用的文件。

## 一致性与冲突

回执不可修改。同一 ID、相同内容的重试返回原回执；同一 ID 换内容返回冲突。不同回执 ID 的结果比较忽略查询时间和回执引用，保留执行身份、终态、费用和产物摘要；相反结果或不同产物/费用阻止处理。页面没有“忽略冲突强制退款”入口。

执行在原任务上完成。任务状态、产物、供应商证据、结算、处理结果和审计使用同一事务；任一步失败整体回滚。原生执行认领同时失效，晚到 worker 不能覆盖处理结果。模型名额在事务提交后幂等释放，中断后由现有终态恢复机制补齐。终态任务及其他幂等键无法再次结算，重复供应商回调与人工处理竞争也只能完成一次。

常见拒绝码：`RECOVERY_PERMISSION_DENIED`、`RECOVERY_LEGACY_IDENTITY_REQUIRED`、`RECOVERY_EVIDENCE_CONFLICT`、`RECOVERY_VERSION_CHANGED`、`RECOVERY_PREVIEW_EXPIRED`、`RECOVERY_BILLING_CHANGED`、`RECOVERY_ARTIFACT_CHANGED`。处理记录保留原请求、回执、操作者及原因；不会暴露服务器文件路径、供应商凭据或签名。

实施与验证见 [实施方案](generation-recovery-implementation.md) 和 [验收证据](../evidence/generation-recovery-20261112.md)。隔离验证不替代真实供应商故障与首集成片联合验收。
