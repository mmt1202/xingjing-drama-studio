# v9.2 事件与异步协议

## 1. RabbitMQ 拓扑

| Exchange | 类型 | 用途 |
|---|---|---|
| `xj.domain.v1` | topic | 领域事件 |
| `xj.command.v1` | topic | worker 命令 |
| `xj.retry.v1` | topic | 分级延迟重试 |
| `xj.dead.v1` | topic | 死信与人工补偿 |

队列按消费者职责声明，例如 `generation.ai-worker.v1`、`delivery.media-worker.v1`、`billing.settlement.v1`。禁止多个不同业务职责共享一个队列并在消费者内过滤。

## 2. 事件 Envelope

```json
{
  "eventId": "019b...",
  "eventType": "generation.task.requested",
  "schemaVersion": 1,
  "occurredAt": "2026-07-13T08:00:00Z",
  "producer": "generation-service",
  "traceId": "00-...",
  "requestId": "019b...",
  "workspaceId": "019b...",
  "aggregateType": "GenerationTask",
  "aggregateId": "019b...",
  "aggregateVersion": 3,
  "payload": {}
}
```

必填：eventId、eventType、schemaVersion、occurredAt、producer、aggregateType、aggregateId、aggregateVersion、payload。平台级事件可以没有 workspaceId，但必须显式为 `null`。

## 3. 关键事件目录

| Routing key | 生产者 | 消费者 | 关键 payload |
|---|---|---|---|
| `generation.task.requested` | generation | ai/media worker | taskId、taskType、inputRef、modelRoute、deadline |
| `generation.task.started` | worker | generation | taskId、attemptNo、providerTaskId |
| `generation.task.progressed` | worker | generation | taskId、attemptNo、progress、stage |
| `generation.task.succeeded` | worker | generation | taskId、attemptNo、resultRefs、providerCost、evidence |
| `generation.task.failed` | worker | generation | taskId、attemptNo、normalizedError、providerError、retryable |
| `billing.hold.created` | billing | generation | holdId、businessId、amount、expiresAt |
| `billing.hold.failed` | billing | generation | businessId、reason、availableAmount |
| `billing.transaction.posted` | billing | analytics/admin | transactionId、type、amount、businessRef |
| `content.version.frozen` | content | project/export | objectType、objectId、versionId、digest |
| `compliance.check.completed` | export-compliance | export/project | checkId、snapshotId、status、riskSummary |
| `export.task.requested` | export-compliance | media worker | exportTaskId、snapshotRef、manifest、deadline |
| `export.task.succeeded` | media worker | export-compliance | exportTaskId、objectRef、sha256、mediaProbe |
| `review.action.recorded` | export-compliance | notification/analytics | linkId、versionId、action、timecode |
| `workspace.member.changed` | workspace | auth/project/admin | workspaceId、memberId、changeType |
| `admin.approval.decided` | admin | affected service | approvalId、target、decision、conditions |

## 4. 命令 payload 示例

```json
{
  "taskId": "019b...",
  "taskType": "videoGenerate",
  "attemptNo": 1,
  "inputRef": {
    "projectId": "019b...",
    "shotId": "019b...",
    "inputDigest": "sha256...",
    "payloadObjectKey": "workspaces/.../task-input.json"
  },
  "route": {
    "modelId": "019b...",
    "fallbackModelIds": ["019c..."],
    "qualityMode": "standard",
    "maxProviderCost": 1200
  },
  "deadline": "2026-07-13T08:10:00Z"
}
```

包含敏感原文或大量提示词时使用对象存储引用，不直接把内容放入消息体。

## 5. 发布一致性

- 业务服务在同一数据库事务写业务表和 outbox。
- publisher 使用 `FOR UPDATE SKIP LOCKED` 批量读取未发布记录。
- RabbitMQ confirm 成功后更新 `published_at`；更新失败会重复发布，消费者必须幂等。
- outbox 保留至少 7 天，归档后仍可按 eventId 追溯。

## 6. 消费幂等

1. 开始处理前尝试插入 `(consumer_name,event_id)`。
2. 唯一冲突表示已处理，直接 ack。
3. 业务写入与 receipt 在同一事务提交。
4. 外部副作用使用事件 ID 作为供应商幂等键；供应商不支持时保存调用锁和结果摘要。

## 7. 重试策略

| 错误类型 | 重试 | 策略 |
|---|---|---|
| 网络瞬断、429、部分 5xx | 是 | 30s、2m、10m，带 jitter，最多 3 次 |
| 模型排队超限 | 可切换 | 一次原模型重试，再按路由切备用模型 |
| 参数非法、素材缺失 | 否 | 立即失败，返回字段问题 |
| 内容违规 | 否 | 进入合规问题，不自动改写规避 |
| 余额不足 | 否 | 保持未执行，提示充值/降档 |
| 回调签名错误 | 否 | 拒绝并触发安全告警 |
| 数据库/对象存储短故障 | 是 | 服务级指数退避，超过阈值进入死信 |

重试前必须检查任务是否已取消、预算是否仍允许、项目是否被冻结、输入版本是否仍有效。

## 8. 死信与补偿

- 死信消息附带原 routing key、失败次数、最后错误和首次发生时间。
- P0/P1 告警按业务影响触发，不能只监控死信数量。
- 人工重放必须选择目标环境、验证当前对象状态并记录审批/工单。
- 已过终态的任务事件重放只允许修复缺失的读模型或审计，不覆盖业务终态。

## 9. 供应商回调转事件

回调接收器完成签名、nonce、时间窗和 providerTaskId 校验后，写入 `model_call_records` 与 outbox，再返回 200。供应商回调不能直接发布未落库事件，也不能直接调用 billing 结算。

## 10. Schema 演进

- 同一 schemaVersion 只追加可选字段。
- 删除、改名、类型变化发布新 schemaVersion，并让生产者双写至少一个发布周期。
- 消费者对未知字段容忍，对未知事件版本拒绝并进入隔离队列，不静默丢弃。
