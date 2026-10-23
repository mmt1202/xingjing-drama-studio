# 统一支付网关接入合同

本地应用负责订单、余额、退款预留和审计；商户侧统一支付网关负责具体渠道。此合同是本项目自定义边界，不是支付宝或微信的原生协议。具体渠道适配、商户授权和真实通道验收需在网关侧完成。配置存在不代表渠道已连接或已获准上线。

## 启用前置

应用支付数据库必须与项目、团队账务和生成账本指向同一权威物理数据库。先应用正式迁移，服务不会自动创建缺失表。所有读取来自当前可信租户/工作区；客户端 header 不能选择别人的工作区。

网关 URL 是服务端配置的固定 HTTPS 地址；应用只发送 `POST {base}/v1/commands`，不跟随重定向。付款页面地址必须是 HTTPS、无用户凭据、无片段，且精确匹配配置的 checkout host 白名单。商品、渠道、金额与币种来自服务端配置；客户端仅提交 offerId 和 channel，不决定价格。本轮商品只接受同一种币种；现有账户币种不匹配时不允许创建支付。现台账单位为货币 minor，商品应付与入账金额必须相同，不伪造积分兑换或赠送额度。

配置项和示例见 `.env.example`。除支付数据库外，`XINGJING_PAYMENT_GATEWAY_URL`、`XINGJING_PAYMENT_MERCHANT_ID`、`XINGJING_PAYMENT_PROVIDER`、`XINGJING_PAYMENT_GATEWAY_SECRET`、`XINGJING_PAYMENT_CHECKOUT_HOSTS`、`XINGJING_PAYMENT_OFFERS_JSON`、`XINGJING_PAYMENT_CHANNELS_JSON` 七项必须全部非空，缺任一项时 `configured=false`。Secret 至少 32 字符；秘密只在服务端环境提供，不写入订单详情、浏览器、审计、URL 或报告。留空时账务仍可读取，创建订单及通道操作明确不可用。首次创建订单会在事务内打开零额账户以固定工作区币种，不视为收款或入账；已有账户币种与商品不一致时拒绝充值。

## 签名与消息

请求、响应和事件使用相同签名头：

| Header | 说明 |
|---|---|
| `X-Payment-Timestamp` | Unix 秒，接收方校验与当前时间差不超过 300 秒 |
| `X-Payment-Nonce` | 每次传输的非空随机串，最长 128 字符 |
| `X-Payment-Signature` | HMAC-SHA256 十六进制，可带 `sha256=` 前缀 |
| `Idempotency-Key` | 命令请求的稳定键，由应用持久派生，网关必须按该键幂等 |

签名输入为三个原始字节段，以换行分隔：

```text
timestamp + "\n" + nonce + "\n" + rawBody
```

请求 JSON 为 UTF-8，应用使用排序 key、无额外空格、保留 Unicode 的规范序列化。接收方必须对实际收到的原始 body 验签，不能先解析再序列化。响应即使 HTTP 200，也必须验签、核对 requestKey 和业务事实后才被应用接受。

## 命令

固定端点 `POST /v1/commands`，body：

```json
{
  "operation": "create_order",
  "merchantId": "configured-merchant",
  "provider": "configured-gateway",
  "orderId": "application-order-id",
  "channel": "configured-channel",
  "amountMinor": "10000",
  "currency": "CNY",
  "expiresAt": "2026-10-23T12:00:00+00:00"
}
```

上面金额仅展示 JSON 格式，不是默认商品或报价。有效 operation 为 `create_order`、`get_order`、`cancel_order`、`create_refund`、`get_refund`。退款命令另携带明确 refundId，并按该退款金额构造 amountMinor。应用保持稳定订单/退款身份及网关幂等键；超时或未知状态不会换新键盲目再下单。

响应 body（同时按上述方式签名）：

```json
{
  "requestKey": "same-idempotency-key",
  "event": {
    "eventId": "globally-stable-event-id",
    "merchantId": "configured-merchant",
    "provider": "configured-gateway",
    "orderId": "application-order-id",
    "amountMinor": "10000",
    "currency": "CNY",
    "state": "pending",
    "checkoutUrl": "https://configured-checkout-host.example/session",
    "expiresAt": "2026-10-23T12:00:00+00:00"
  }
}
```

支付 state 为 pending / processing / unknown / paid / closed / failed；paid 必须有非空 transactionId。所有退款事件携带 refundId 和原支付 transactionId，后者必须匹配原订单。退款 state 为 processing / unknown / succeeded / failed，succeeded 必须有非空 refundTransactionId。金额为不带符号、无前导零的正十进制字符串，不超过有符号 64 位整数上限；币种、商户、供应商及对象必须与已保存快照精确匹配。

## 事件回调与重放

网关可向应用 `POST /api/v1/payment-gateway/events` 发送上面的 event 对象本身，带相同的签名头。该入口不依靠浏览器会话授权资金变更；验签及保存的商户/供应商/订单/退款绑定是必要条件。

同一个 provider + merchant + eventId 永久绑定同一事实；同 ID 改变事实拒绝。transactionId 和 refundTransactionId 防止跨单复用。网关对已持久事件重试时保留 eventId 和业务内容，重新生成有效的传输时间/nonce/签名。查询响应和回调可返回同一个明确事件，重复事件不重复入账。查询同一状态但新 eventId 的结果仍受终态与金额校验。

网络超时、5xx、坏签名、对象/金额不匹配不能证明支付或退款失败。应用保留待核对状态和退款预留，通过同一业务身份继续查询，不能先释放额度再收到成功退款。过期关单也须以渠道证实关闭为依据；本地过期不覆盖晚到的实际支付事实。

## 退款及生产边界

退款申请引用原订单、原入账与说明，先把可用额度转入退款预留。所有交互写操作要求当前可信 `billing.manage`；批准另须财务审批权限，且申请人与批准人必须不同。申请人可以拒绝自己的待审批申请以撤销预留，其他人拒绝须有财务审批权限。处理期间额度不能再用于生成任务。已证实失败或拒绝才释放预留，已证实成功才扣减预留、累计原单退款额并追加守恒流水。原支付流水不修改或删除。

现有共享余额没有完整来源桶。自动退款保守要求新网关订单的可核验付款/账户依据；其他账户变更使原充值未消费无法证明时拒绝自动退款，进入人工核对，而不把工作区总余额等同于原单本金。历史未关联订单不因页面可见就获得自动退款资格，其 `creditMinor` 返回 `null` 表示缺少可信入账额度事实，不能显示为 0，也不能用应付金额推断入账额度。

退款审批须明确配置正数 `XINGJING_PAYMENT_REFUND_MFA_THRESHOLD_MINOR`；缺失时所有自动批准均不可执行，但不单独禁用已配置的充值通道。阈值按原充值订单总额判断，并在退款记录中保存为 `approvalBasisMinor`。达到或超过阈值时，即使本次退款金额较小，也必须经过可信 `RefundApprovalVerifier`；验证器同时接收本次金额 `amount_minor` 和原单金额 `approval_basis_minor`。当前应用组合没有生产 MFA 适配，这类批准保持不可执行。口令再验证或客户端布尔值不能冒充 MFA；已持久认领的同一批准命令重试不重复消费验证凭据。

旧仅凭 body HMAC 的支付回调及旧后台直接改账本的退款入口不能绕过统一事件和审批流程。任务失败补偿仍是独立账本补偿，不等同于现金退款。

真实商户/通道、MFA、PostgreSQL 行锁并发、Java 身份、对账及 D2 联合验收另行完成；本地注入端口和 SQLite 验证不替代生产收退款验收。
