import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";
import type { BillingWorkspace, PaymentCatalog, PaymentDetail, PaymentResult, PaymentsApi } from "./contracts";

export class PaymentApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) { super(code); }
}
function query(input: object) { return new URLSearchParams(Object.entries(input).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)])).toString(); }
export function createPaymentsApi(workspaceId: string, token: string | null): PaymentsApi {
  const fetcher = createAuthenticatedFetcher({ workspaceId });
  const base = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}`;
  const retryKeys = new Map<string, string>();
  let actorId = "";
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json"); headers.set("Accept-Language", i18n.resolvedLanguage ?? "zh");
    headers.set("X-Workspace-Id", workspaceId); headers.set("X-Request-Id", crypto.randomUUID());
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetcher(`${base}${path}`, { ...init, headers, credentials: "same-origin" });
    const body = await response.json().catch(() => null) as { data?: T; error?: { code?: string }; detail?: { code?: string }; meta?: { requestId?: string } } | null;
    if (!response.ok || !body || body.data === undefined) throw new PaymentApiError(response.status, body?.error?.code ?? body?.detail?.code ?? "PAYMENT_UNAVAILABLE", body?.meta?.requestId);
    return body.data;
  }
  function acceptScope(scope: { workspaceId: string; actorId: string }) {
    if (scope.workspaceId !== workspaceId || !scope.actorId || (actorId && actorId !== scope.actorId)) throw new PaymentApiError(502, "PAYMENT_SCOPE_MISMATCH");
    actorId = scope.actorId;
  }
  async function write(path: string, input: object, intentKey?: string): Promise<PaymentResult> {
    if (!actorId) throw new PaymentApiError(409, "PAYMENT_CONTEXT_REQUIRED");
    const body = JSON.stringify(input), signature = `${path}:${body}`;
    if (intentKey) {
      if (!/^[a-f0-9-]{36}$/i.test(intentKey)) throw new PaymentApiError(400, "PAYMENT_INTENT_INVALID");
      return request<PaymentResult>(path, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": intentKey }, body });
    }
    const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(signature)))].map((value) => value.toString(16).padStart(2, "0")).join("");
    const storageKey = `payment-command:${JSON.stringify([actorId, workspaceId, digest])}`;
    let retained: string | null = null;
    try { retained = sessionStorage.getItem(storageKey); if (retained && !/^[a-f0-9-]{36}$/i.test(retained)) retained = null; } catch { /* Immediate retries retain the in-memory receipt key. */ }
    const key = retryKeys.get(signature) ?? retained ?? crypto.randomUUID(); retryKeys.set(signature, key);
    try { sessionStorage.setItem(storageKey, key); } catch { /* The operation remains available when storage is disabled. */ }
    function forget() { retryKeys.delete(signature); try { sessionStorage.removeItem(storageKey); } catch { /* Storage may be disabled. */ } }
    try {
      const result = await request<PaymentResult>(path, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body });
      forget(); return result;
    } catch (error) {
      if (error instanceof PaymentApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) forget();
      throw error;
    }
  }
  return {
    async catalog(signal) { const value = await request<PaymentCatalog>("/payments/catalog", { signal }); acceptScope(value.scope); return value; },
    async billing(filters, signal) { const value = await request<BillingWorkspace>(`/billing/workspace?${query(filters)}`, { signal }); acceptScope(value.scope); return value; },
    orders: (filters, signal) => request(`/payments/orders?${query(filters)}`, { signal }),
    refunds: (filters, signal) => request(`/payments/refunds?${query(filters)}`, { signal }),
    async detail(id, signal) { const value = await request<PaymentDetail>(`/payments/orders/${encodeURIComponent(id)}`, { signal }); acceptScope(value.scope); return value; },
    create: (input, intentKey) => write("/payments/orders", input, intentKey),
    orderAction: (id, input, intentKey) => write(`/payments/orders/${encodeURIComponent(id)}/actions`, input, intentKey),
    refundAction: (id, input, intentKey) => write(`/payments/refunds/${encodeURIComponent(id)}/actions`, input, intentKey),
  };
}
