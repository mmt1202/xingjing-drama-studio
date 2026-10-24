import { createAuthenticatedFetcher } from "@/utils/auth";
export interface ModelDefinition {
  registryKey: string; providerId: string; providerModelName: string; displayName: string; modelVersion: string; region: string;
  mediaTypes: string[]; capabilities: string[]; parameterSchema: Record<string, { type: string; enum?: Array<string | number | boolean> }>;
  credentialConfigured: boolean; maxConcurrency: number; active: boolean; version: number; updatedAt?: string;
}
export interface Price { id: string; pricingVersion: string; currency: string; estimatedMinor: number; effectiveAt: string; retiredAt: string | null }
export interface ModelRecord extends ModelDefinition {
  health: { status: string; activeCalls: number; consecutiveFailures: number; latencyMs: number | null; circuitOpenUntil: string | null; checkedAt: string | null };
  pricing: Price | null; priceHistory?: Price[];
}
export interface RoutingOptions { mode?: "priority" | "weighted"; weights?: Record<string, number>; currency?: string | null; maxLatencyMs?: number | null; maxConsecutiveFailures?: number | null; minSuccessRate?: number | null }
export interface Policy { id: string; capability: string; mediaType: string; allowedRegistryKeys: string[]; preferredRegistryKeys: string[]; maxEstimatedMinor: number | null; allowFallback: boolean; routingOptions: RoutingOptions; version: number; updatedAt: string }
export interface ModelAudit { id: string; action: string; objectId: string; actorId: string; requestId: string; reason: string; before: Record<string, unknown>; after: Record<string, unknown>; createdAt: string }
export interface OperationsPage {
  context: { actorId: string; workspaceId: string; permissions: string[]; role: string };
  items: ModelRecord[]; total: number; offset: number; limit: number;
  summary: { total: number; healthy: number; abnormal: number; monthlyCalls: number };
  catalog: ModelDefinition[]; policies: Policy[]; audit: ModelAudit[];
  routingLimits: { failureCeiling: number; successSampleSize: number; minimumSuccessSamples: number };
}
export interface ModelAction { action: "upsert_model" | "publish_pricing" | "set_model_active" | "upsert_policy"; registryKey?: string; version: number; reason: string; payload: Record<string, unknown> }
export class ModelApiError extends Error {
  constructor(public status: number, public code: string, public requestId?: string) { super(code); }
}
export function createModelOperationsApi(workspaceId: string, token: string) {
  const fetcher = createAuthenticatedFetcher({ workspaceId });
  const base = "/api/v1/admin/model-control";
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetcher(path, { ...init, credentials: "include", headers: { "Content-Type": "application/json", "X-Workspace-Id": workspaceId, ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers } });
    const body = await response.json().catch(() => ({})) as { data: T; error?: { code?: string }; detail?: { code?: string }; meta?: { requestId?: string } };
    if (!response.ok) throw new ModelApiError(response.status, body.error?.code ?? body.detail?.code ?? "MODEL_REQUEST_FAILED", body.meta?.requestId);
    return body.data;
  }
  return {
    list: (query: string, signal: AbortSignal) => request<OperationsPage>(`${base}?${query}`, { signal }),
    detail: (key: string, signal: AbortSignal) => request<ModelRecord>(`${base}/detail?registryKey=${encodeURIComponent(key)}`, { signal }),
    audit: (query: string, signal: AbortSignal) => request<{ items: ModelAudit[]; total: number; offset: number; limit: number }>(`${base}/audit?${query}`, { signal }),
    save: (command: ModelAction, key: string) => request<{ object: ModelRecord | Policy; requestId: string }>(`${base}/actions`, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(command) }),
  };
}
export type ModelOperationsApi = ReturnType<typeof createModelOperationsApi>;
