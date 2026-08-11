export const commercialClientIntegrationGuide = {
  importPath: "@/xingjing_commercial_client",
  endpointPrefix: "/api/v1/commercial-orders",
  requiredHeaders: [
    "Authorization",
    "X-Workspace-Id",
    "Idempotency-Key",
    "If-Match",
  ],
  readMethods: {
    list: "listOrders",
    detail: "getOrder",
    milestones: "listMilestones",
  },
  pageActions: {
    "AD-005": {},
    "AD-006": {
      resolveDispute: "resolve_dispute",
    },
    "AD-007": {
      approveSettlement: "pay_settlement",
    },
    "AD-008": {},
    "CR-012": {
      acceptDelivery: "accept_delivery",
      rejectDelivery: "return_delivery",
    },
    "CR-013": {
      deliver: "submit_delivery",
    },
    "CR-014": {
      submitQuote: "submit_quote",
      signContract: "record_contract",
    },
    "CR-015": {
      submitQuote: "submit_quote",
    },
    "CR-016": {
      openDispute: "open_dispute",
    },
  },
  requiredActionInputs: {
    submit_quote: ["orderId", "amount_minor", "currency", "proposal", "valid_until"],
    accept_quote: ["orderId", "quoteId"],
    record_contract: ["orderId", "content_ref", "content_digest", "amount_minor"],
    submit_delivery: [
      "orderId",
      "milestoneId",
      "artifact_version_id",
      "artifact_digest",
      "note",
    ],
    return_delivery: ["orderId", "deliveryId", "reason"],
    accept_delivery: ["orderId", "deliveryId", "evidence_ref"],
    open_dispute: ["orderId", "milestoneId", "kind", "reason"],
    resolve_dispute: ["orderId", "disputeId", "resolution"],
    freeze_settlement: ["orderId", "settlementId", "amount_minor", "currency"],
    resume_settlement: ["orderId", "settlementId", "amount_minor", "currency"],
    pay_settlement: ["orderId", "settlementId", "amount_minor", "currency"],
  },
  responseRules: [
    "列表响应为 { items, total, offset, limit }，不是 data/meta 信封",
    "详情和写操作返回裸 CommercialOrder，并从 ETag 读取乐观锁版本",
    "409 VERSION_CONFLICT 必须重新读取详情并使用新 ETag，不得盲重试写操作",
    "同一次用户写意图重试时必须复用 Idempotency-Key",
  ],
  unsupportedCurrentActions: [
    {
      pageId: "AD-005",
      actionId: "viewCommercialOrder",
      reason: "该能力是 getOrder 读取，不是写操作",
    },
    {
      pageId: "AD-008",
      actionId: "updateCommercialOrder",
      reason: "FastAPI 未提供通用商单更新端点",
    },
    {
      pageId: "CR-014",
      actionId: "updateMilestone",
      reason: "FastAPI 未提供里程碑更新端点",
    },
    {
      pageId: "CR-016",
      actionId: "requestSettlement",
      reason: "结算由验收成功自动创建，FastAPI 未提供手工申请端点",
    },
  ],
} as const;

export type CommercialClientIntegrationGuide = typeof commercialClientIntegrationGuide;
