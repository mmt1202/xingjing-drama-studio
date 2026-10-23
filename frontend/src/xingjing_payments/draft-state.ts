export interface PaymentDraft {
  idempotencyKey: string; attempted: boolean;
  kind: "create" | "cancel" | "requestRefund" | "approve" | "reject" | "refundRefresh";
  targetId: string; orderId: string; expectedVersion: number; offerId: string; channel: string;
  amount: string; currency: string; reason: string; ticketId: string;
}
const storageKey = (actor: string, workspace: string) => `payment-draft:${JSON.stringify([actor, workspace])}`;
export function readPaymentDraft(actor: string, workspace: string): PaymentDraft | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(actor, workspace)) ?? "null") as Partial<PaymentDraft> | null;
    if (!value || typeof value.idempotencyKey !== "string" || !/^[a-f0-9-]{36}$/i.test(value.idempotencyKey) || typeof value.attempted !== "boolean"
      || !["create", "cancel", "requestRefund", "approve", "reject", "refundRefresh"].includes(value.kind ?? "")
      || !Number.isSafeInteger(value.expectedVersion) || (value.expectedVersion ?? -1) < 0
      || ![value.targetId, value.orderId, value.offerId, value.channel, value.amount, value.currency, value.reason, value.ticketId].every((item) => typeof item === "string")
      || (value.reason?.length ?? 0) > 1000 || (value.ticketId?.length ?? 0) > 128) return;
    return value as PaymentDraft;
  } catch { return; }
}
export function savePaymentDraft(actor: string, workspace: string, value?: PaymentDraft) {
  try { if (value) sessionStorage.setItem(storageKey(actor, workspace), JSON.stringify(value)); else sessionStorage.removeItem(storageKey(actor, workspace)); }
  catch { /* In-memory editing remains available when storage is disabled. */ }
}
