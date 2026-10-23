export type PaymentTab = "ledger" | "orders" | "refunds";
export interface PaymentScope { workspaceId: string; actorId: string }
export interface PaymentPermissions { canView: boolean; canManage: boolean; canApprove: boolean }
export interface PaymentOffer { id: string; label: string; amountMinor: string; creditMinor: string; currency: string }
export interface PaymentCatalog {
  scope: PaymentScope; permissions: PaymentPermissions; configured: boolean; reasonCode?: string;
  offers: PaymentOffer[]; channels: { id: string; label: string }[];
  policy: { accountUnit: "currency_minor"; fourEyes: true; refundMfaThresholdMinor: string | null; mfaVerifierAvailable: boolean };
}
export type OrderStatus = "creating" | "pending" | "processing" | "unknown" | "paid" | "closed" | "failed" | "refunded";
export interface PaymentOrder {
  id: string; offerId: string; channel: string; provider: string; amountMinor: string; creditMinor: string | null;
  currency: string; status: OrderStatus; version: number; createdAt: string; updatedAt: string;
  expiresAt: string; paidAt: string | null; refundedMinor: string; refundableMinor: string; refundReasonCode?: string;
  checkout: { url: string | null; expiresAt: string | null };
  actions: { canRefresh: boolean; canCancel: boolean; canRequestRefund: boolean };
}
export type RefundStatus = "pending_approval" | "processing" | "unknown" | "succeeded" | "failed" | "rejected";
export interface PaymentRefund {
  id: string; orderId: string; amountMinor: string; approvalBasisMinor: string; currency: string; status: RefundStatus; version: number;
  requestedBy: string; approvedBy: string | null; reason: string; ticketId: string;
  createdAt: string; updatedAt: string; completedAt: string | null; reasonCode?: string;
  actions: { canApprove: boolean; canReject: boolean; canRefresh: boolean };
}
export interface PaymentDetail {
  scope: PaymentScope; permissions: PaymentPermissions; order: PaymentOrder; refunds: PaymentRefund[];
  history: { action: string; actorId: string; createdAt: string; note?: string }[];
}
export interface PaymentPage<T> { items: T[]; total: number; offset: number; limit: number }
export interface PaymentPageQuery { q: string; status: string; offset: number; limit: number }
export interface BillingQuery {
  query?: string; status?: string; source?: string; projectId?: string; dateFrom?: string; dateTo?: string;
  pageSize: number; pageToken?: string;
}
export interface BillingLedgerEntry {
  id: string; source: string; action: string; amountMinor: string; availableDeltaMinor: string | null;
  currency: string; occurredAt: string; projectId: string | null; taskId: string | null; reference: string;
}
export interface BillingWorkspace {
  scope: PaymentScope & { tenantId: string }; moneyUnit: { unit: "minor"; currency: string | null };
  account: { availableMinor: string; heldMinor: string; spentMinor: string; version: number; updatedAt: string | null } | null;
  consumption: {
    month: { from: string; to: string; settledMinor: string | null };
    last7Days: { date: string; settledMinor: string | null; settlementCount: number }[];
    bySource: { source: string; settledMinor: string | null; settlementCount: number }[];
    budgetMinor: null; estimatedDaysRemaining: null;
  };
  ledger: { items: BillingLedgerEntry[]; nextToken: string | null; pageSize: number };
  plan: { planId: string; status: string; seatLimit: number; features: unknown; quotas: unknown; quotaRemaining: unknown; version: number; updatedAt: string } | null;
  permissions: { canView: boolean; canManage: boolean }; payment: { configured: boolean; reason: string | null };
  coverage: { accountPresent: boolean; excludedCurrencyCount: number; ledgerDirectionUnknownCount: number; notes: string[] };
}
export interface CreateOrderInput { offerId: string; channel: string; reason: string }
export interface OrderActionInput { action: "refresh" | "cancel" | "requestRefund"; expectedVersion: number; amountMinor?: string; reason?: string; ticketId?: string }
export interface RefundActionInput { action: "approve" | "reject" | "refresh"; expectedVersion: number; reason: string }
export interface PaymentResult { order?: PaymentOrder; refund?: PaymentRefund }
export interface PaymentsApi {
  catalog(signal?: AbortSignal): Promise<PaymentCatalog>;
  billing(query: BillingQuery, signal?: AbortSignal): Promise<BillingWorkspace>;
  orders(query: PaymentPageQuery, signal?: AbortSignal): Promise<PaymentPage<PaymentOrder>>;
  refunds(query: PaymentPageQuery, signal?: AbortSignal): Promise<PaymentPage<PaymentRefund>>;
  detail(orderId: string, signal?: AbortSignal): Promise<PaymentDetail>;
  create(input: CreateOrderInput, intentKey?: string): Promise<PaymentResult>;
  orderAction(orderId: string, input: OrderActionInput, intentKey?: string): Promise<PaymentResult>;
  refundAction(refundId: string, input: RefundActionInput, intentKey?: string): Promise<PaymentResult>;
}
