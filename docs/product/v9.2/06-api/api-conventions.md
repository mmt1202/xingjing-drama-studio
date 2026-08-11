# v9.2 API 约定

## 1. 基础规则

- 外部业务 API 前缀：`/api/v1`。
- 协议：HTTPS；媒体上传/下载使用对象存储预签名 URL。
- 内容类型：`application/json; charset=utf-8`；文件导出返回任务，不同步传大文件。
- JSON 字段使用 `camelCase`；数据库字段使用 `snake_case`。
- ID 在 JSON 中使用字符串形式 UUIDv7。
- 时间使用 RFC 3339 UTC，例如 `2026-07-13T08:00:00Z`。
- 金额和算力使用整数最小单位，并同时返回 `unit`。

## 2. 请求上下文

| Header | 要求 | 说明 |
|---|---|---|
| `Authorization` | 创作者 API 必填 | `Bearer <access_token>` |
| `X-Workspace-Id` | 租户业务 API 必填 | 当前工作区，服务端仍校验对象归属 |
| `X-Request-Id` | 可选 | 客户端可提供 UUID；服务端缺失时生成 |
| `Idempotency-Key` | 所有产生副作用的 POST 必填 | 同工作区、同操作 24 小时内唯一 |
| `If-Match` | 更新已有对象时必填 | 值为当前版本，例如 `"17"` |
| `traceparent` | 可选 | W3C Trace Context |

后台 API 使用独立员工会话，路径在 `/api/v1/admin` 下。客户审片使用外链 token 和短期 review session，不接受工作区 Bearer token 替代。

## 3. 成功响应

单对象：

```json
{
  "data": {
    "id": "019b...",
    "version": 3
  },
  "meta": {
    "requestId": "019b...",
    "serverTime": "2026-07-13T08:00:00Z"
  }
}
```

异步创建返回 HTTP 202：

```json
{
  "data": {
    "taskId": "019b...",
    "status": "queued",
    "statusUrl": "/api/v1/generation-tasks/019b..."
  },
  "meta": {
    "requestId": "019b..."
  }
}
```

## 4. 分页、筛选和排序

- 默认使用游标分页：`pageSize` 1–100，默认 20；`pageToken` 为不透明字符串。
- 响应 `meta.page.nextToken`；没有下一页时为 `null`。
- 后台导出不得通过放大 pageSize 实现，必须创建导出任务。
- 排序参数 `sort=updatedAt:desc,id:desc`；服务端始终补唯一键保证稳定。
- 筛选使用显式参数，不接受任意 SQL 风格表达式。

```json
{
  "data": [],
  "meta": {
    "requestId": "019b...",
    "page": {
      "size": 20,
      "nextToken": "eyJ1cGRhdGVkQXQiOi..."
    }
  }
}
```

## 5. 错误响应

所有错误使用：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "对象已被其他成员更新",
    "details": [
      {"field": "version", "reason": "expected 6, actual 7"}
    ],
    "retryable": false
  },
  "meta": {
    "requestId": "019b..."
  }
}
```

| HTTP | 错误码 | 使用场景 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 字段格式或组合非法 |
| 401 | `UNAUTHENTICATED` | 登录态无效或外链未验证 |
| 403 | `PERMISSION_DENIED` | 权限不足，不泄露敏感对象 |
| 404 | `RESOURCE_NOT_FOUND` | 对象不存在或不可见 |
| 409 | `VERSION_CONFLICT` | 乐观锁冲突 |
| 409 | `INVALID_STATE_TRANSITION` | 当前状态不允许操作 |
| 409 | `COMPLIANCE_BLOCKED` | 正式导出被合规阻断 |
| 409 | `INSUFFICIENT_CREDIT` | 可用算力不足 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一幂等键携带了不同请求 |
| 410 | `RESOURCE_GONE` | 外链撤销、对象永久不可用 |
| 413 | `UPLOAD_TOO_LARGE` | 上传超过配置限制 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 文件格式不支持 |
| 422 | `BUSINESS_RULE_VIOLATION` | 语法正确但违反业务规则 |
| 429 | `RATE_LIMITED` | 限流；返回 `Retry-After` |
| 500 | `INTERNAL_ERROR` | 未分类服务端错误 |
| 502 | `PROVIDER_ERROR` | 第三方模型/支付/消息异常 |
| 503 | `SERVICE_UNAVAILABLE` | 服务或模型暂不可用 |
| 504 | `UPSTREAM_TIMEOUT` | 上游超时 |

## 6. 幂等

- 服务端保存 `workspace_id + operation_id + idempotency_key + request_digest + response_snapshot`。
- 相同 key、相同请求返回第一次结果；相同 key、不同请求返回 409。
- 幂等覆盖任务创建、冻结、导出、评论提交、审批、退款、邀请和外链创建。
- 消费者幂等不复用 HTTP key，使用事件 `eventId`。

## 7. 并发与版本

- 可编辑资源返回 `ETag: "<version>"`。
- PUT/PATCH 携带 `If-Match`；不匹配返回 409 和当前版本摘要。
- 批量编辑提交 `baseVersion` 和完整操作数组，事务内全部成功或全部失败。
- 状态转换使用动作接口，不允许客户端直接 PATCH 任意状态值。

## 8. 上传与媒体

流程：

1. `POST /uploads` 创建上传会话，声明文件名、大小、MIME、SHA-256 和业务用途。
2. 服务端返回临时对象键和预签名 URL/分片参数。
3. 客户端上传到对象存储。
4. `POST /uploads/{uploadId}/complete` 校验大小、摘要和媒体探测结果。
5. 业务接口引用已完成的 `uploadId`；未绑定临时对象按生命周期清理。

下载地址默认 10 分钟有效；客户审片、合规证据和后台导出按权限使用不同签名策略。

## 9. SSE

端点：`GET /api/v1/task-events?projectId=&lastEventId=`。

事件格式：

```text
id: 019b-event-id
event: generation.task.updated
data: {"taskId":"019b...","status":"running","progress":42,"occurredAt":"2026-07-13T08:00:00Z"}
```

- 客户端断线后使用 `Last-Event-ID` 重连。
- SSE 只作通知，页面恢复时查询详情确认最终状态。
- 心跳 15 秒；连接上限和用户/工作区并发数由网关控制。

## 10. 第三方回调

- 回调路径不暴露业务 token，使用供应商专属 endpoint、时间戳、nonce 和 HMAC 签名。
- 服务端校验时间窗、签名、nonce 重放和供应商任务 ID。
- 原始回调存受限对象存储，数据库保留摘要和解析结果。
- 回调 HTTP 200 只表示已接收；重复回调不重复结算。

## 11. API 安全与审计

- 响应按角色脱敏手机号、邮箱、身份证明、支付和密钥信息。
- 管理员跨租户查询必须提供业务原因或工单号，高风险写操作绑定审批单。
- 日志禁止记录 Authorization、验证码、密码、供应商密钥、完整提示词中的敏感内容和预签名 URL。
- 写 API 审计记录包含 requestId、actor、workspace、operationId、target、result 和变更摘要。

## 12. 兼容策略

- `/api/v1` 内允许新增可选字段和枚举；客户端必须忽略未知响应字段。
- 删除字段、改变语义、收紧必填或改变错误码属于破坏性变更，进入 `/api/v2` 或提供迁移期。
- 事件 schema 只允许追加可选字段；消费者按 `schemaVersion` 解析。
