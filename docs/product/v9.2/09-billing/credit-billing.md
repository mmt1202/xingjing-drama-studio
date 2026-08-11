# v9.2 算力计费与账务规则

## 1. 目标

让用户在提交前看见预计消耗，任务执行前冻结额度，成功后按实际规则结算，失败、取消和超时有确定处理，并保证任何重试或重复回调都不会重复扣费。

## 2. 计量单位

- 平台账本单位：`credit_micros`，1 平台积分 = 1,000,000 credit_micros。
- 供应商成本使用 `provider_cost_minor`，按结算币种最小单位记录。
- 展示层可以换算积分和人民币估值，但账本只用整数。
- 模型价格规则版本化，任务创建时保存 `pricing_rule_version` 快照。

## 3. 账户与余额桶

额度来源按到期和用途分桶：赠送、订阅、充值、补偿、企业合同。消耗顺序：最早到期赠送 → 订阅 → 充值 → 补偿，但退款必须追溯原桶。

```text
available = granted_total - spent_total - active_hold_total
```

账户状态 active、frozen、closed。frozen 账户允许查看和退款处理，不允许新冻结。

## 4. 预估

预估输入：模型、任务类型、数量、时长/分辨率、质量档位、增强项、候选数和路由不确定性。

响应包含：

- `estimatedCredit`：最可能消耗；
- `maxCredit`：允许冻结上限；
- `pricingRuleVersion`；
- `expiresAt`：报价有效期；
- `breakdown`：基础、候选、增强、导出等分项。

用户确认的是 maxCredit 上限。路由切换导致预估超过上限时暂停并重新确认，不允许静默超扣。

## 5. 冻结

1. generation/export 服务发送业务单据和 maxCredit。
2. billing 以账户行锁检查 available。
3. 创建 hold 和 `HOLD` 流水，增加 active_hold_total。
4. 返回 holdId 后业务任务才能 queued。
5. 相同 businessType+businessId 重复请求返回同一 hold。

冻结默认到期时间根据任务类型配置；到期由对账任务处理，不能仅靠定时删除。

## 6. 结算场景

| 场景 | 结算 | 释放 | 说明 |
|---|---:|---:|---|
| 成功，实际≤冻结 | 实际额 | 剩余额 | 正常路径 |
| 成功，实际>冻结 | 冻结额 | 0 | 任务标记差额待平台承担或人工复核，不负余额 |
| 明确失败且供应商未计费 | 0 | 全部 | 用户不承担 |
| 失败但供应商产生可归因成本 | 按产品规则允许额 | 剩余 | 必须有证据且页面提交前已披露 |
| 用户排队中取消 | 0 | 全部 | worker 未接收 |
| 用户运行中取消且供应商确认 | 实际发生额 | 剩余 | 展示取消成本 |
| 取消不被供应商支持 | 最终按结果 | 最终按结果 | 状态保持 cancel_requested |
| 超时无最终证据 | 暂不结算 | 保持或到期转复核 | 晚回调进入对账 |
| 批量部分成功 | 成功子项实际额 | 未执行/失败子项 | 子任务逐笔汇总 |

## 7. 重试计费

- 平台故障、结果损坏、供应商可恢复错误触发的自动重试，默认不额外向用户计费，成本记平台补偿成本。
- 用户主动“再生成”视为新任务，重新预估和冻结。
- 用户选择“失败重试”时，页面明确本次是否免费；由 `retryBillingPolicy` 决定。
- 切换模型若预计超过剩余冻结额，必须追加冻结并再次确认。

## 8. 流水

流水类型：GRANT、HOLD、SETTLE、RELEASE、EXPIRE、REFUND、ADJUST_DEBIT、ADJUST_CREDIT、TRANSFER。

每笔包含 transactionNo、accountId、direction、amount、businessRef、relatedTransactionId、pricingVersion、actor、requestId 和 postedAt。已过账流水不可修改；冲正创建反向流水。

## 9. 退款和调账

- 用户退款：引用订单、原入账和未消耗余额，按支付渠道规则处理。
- 异常扣费：客服建工单，财务或授权角色创建补偿/冲正；超过阈值进入双人审批。
- 赠送额度不提现；充值额度退款时扣除已使用部分和明确费用。
- 后台不得直接修改余额字段，所有变化必须过账。

## 10. 对账

每日执行：

1. 供应商模型账单 vs model_call_record；
2. model_call_record vs generation task；
3. generation task vs hold/transaction；
4. 账户汇总字段 vs 不可变流水重算；
5. 支付渠道订单/退款 vs 平台订单；
6. 商单验收/结算 vs 分账流水。

差异进入 reconciliation_record，按金额和影响分级。账本不平或重复结算触发 P1 告警并暂停相关自动结算。

## 11. 权限

| 操作 | 权限 | 额外控制 |
|---|---|---|
| 查看个人流水 | `billing.view` | 仅本人/授权工作区 |
| 查看团队成本 | `billing.team.view` | 团队财务或所有者 |
| 设置预算 | `billing.budget.manage` | 不得超过工作区策略 |
| 创建退款 | `admin.finance.refund.create` | 工单和原因必填 |
| 批准大额退款 | `admin.finance.refund.approve` | 与创建人分离、MFA |
| 调账 | `admin.finance.adjust` | 双人审批、完整审计 |

## 12. 核心验收用例

1. 同一幂等键并发提交十次，只创建一个任务、一个 hold。
2. 成功任务只产生一次 SETTLE，重复回调不改变余额。
3. 明确失败任务释放未消耗冻结额，可用余额恢复。
4. 部分成功批次按子项结算，汇总等于子项之和。
5. 实际成本超过冻结上限不会产生负可用余额。
6. 退款通过反向流水完成，原流水保持不变。
7. 任意账户从流水重算结果等于汇总字段。
8. 普通管理员不能直接修改余额或删除流水。
9. 供应商晚回调不会把已退款任务重复扣费，而是进入对账。
10. 项目、剧集、镜头、模型和成员成本报表总额与账本一致。
