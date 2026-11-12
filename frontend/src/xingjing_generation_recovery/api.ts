import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";

export interface TaskSummary {
  id: string; projectId: string; projectName: string; status: string; version: number;
  promptExcerpt: string; mediaType: string; executionState: string | null; currency: string | null;
  heldMinor: number; receiptCount: number; conflict: boolean; attention: boolean; updatedAt: string;
}
export interface RecoveryContext { actorId: string; workspaceId: string; role: string; canManage: boolean }
export interface RecoveryList {
  items: TaskSummary[]; total: number; offset: number; limit: number; context: RecoveryContext;
  receiptChannelConfigured: boolean;
}
export interface Receipt {
  id: string; status: "succeeded" | "failed"; source: "signed_adapter"; verifiedAt: string;
  upstreamRequestId: string; providerReceiptRef: string; evidenceKind: "provider_terminal" | "provider_not_accepted";
  providerId: string; modelId: string; providerJobId: string | null; completedAt: string; observedAt: string;
  currency: string; estimatedMinor: number; actualMinor: number; pricingVersion: string;
  artifacts: Array<{ asset_id: string; media_type: "image" | "video"; content_sha256: string; size_bytes: number }>;
}
export interface RecoveryResult {
  commandId: string; receiptId: string; projectId: string; taskId: string; status: "succeeded" | "failed";
  taskVersion: number; currency: string; chargedMinor: number; releasedMinor: number; completedAt: string;
}
export interface RecoveryDetail {
  task: {
    id: string; projectId: string; projectName: string; status: string; version: number; prompt: string; capability: string;
    mediaType: string; requestFingerprint: string; providerId: string | null; modelId: string | null;
    providerJobId: string | null; attempt: number; createdAt: string; timeoutAt: string;
    inputs: Array<{ assetId: string; versionId: string | null; sha256: string; mediaType: string }>;
    budgetScope: { kind: string; episode_id?: number } | null;
  };
  execution: { kind: string; state: string; id: string | null; upstreamRequestId: string | null; submittedAt: string | null; capacityReserved: boolean | null };
  billing: { currency: string; heldMinor: number; estimatedMinor: number; chargedMinor: number; releasedMinor: number; pricingVersion: string; status: string } | null;
  receipts: Receipt[]; conflict: boolean; blockedCode: string | null; canManage: boolean;
  history: Array<{ id: string; actorId: string; reason: string; receiptId: string; result: RecoveryResult; createdAt: string }>;
}
export interface RecoveryPreview {
  previewId: string; expiresAt: string; projectId: string; taskId: string; taskVersion: number; receiptId: string;
  action: "restore_result" | "confirm_failure"; currency: string; heldMinor: number; chargedMinor: number;
  releasedMinor: number; pricingVersion: string; artifactCount: number; newProviderCalls: number;
}
export interface ResolvePayload { previewId: string; reason: string }
export class RecoveryApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) { super(message); }
}
export function createRecoveryApi(workspaceId: string, token: string) {
  const fetcher = createAuthenticatedFetcher({ workspaceId });
  const base = "/api/v1/admin/generation-recovery";
  const target = (project: string, task: string) => `${base}/projects/${encodeURIComponent(project)}/tasks/${encodeURIComponent(task)}`;
  async function response(path: string, init: RequestInit = {}) {
    const result = await fetcher(path, { ...init, credentials: "same-origin", cache: "no-store", headers: {
      "Content-Type": "application/json", "Accept-Language": i18n.language, "X-Workspace-Id": workspaceId,
      ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers,
    } });
    if (!result.ok) {
      const body = await result.json().catch(() => ({})) as { error?: { code?: string; message?: string }; meta?: { requestId?: string } };
      throw new RecoveryApiError(result.status, body.error?.code ?? "RECOVERY_UNAVAILABLE", body.error?.message ?? "", body.meta?.requestId);
    }
    return result;
  }
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    return ((await (await response(path, init)).json()) as { data: T }).data;
  }
  return {
    list: (query: string, signal: AbortSignal) => request<RecoveryList>(`${base}?${query}`, { signal }),
    detail: (project: string, task: string, signal: AbortSignal) => request<RecoveryDetail>(target(project, task), { signal }),
    preview: (project: string, task: string, receiptId: string, expectedVersion: number) => request<RecoveryPreview>(`${target(project, task)}/preview`, { method: "POST", body: JSON.stringify({ receiptId, expectedVersion }) }),
    resolve: (project: string, task: string, payload: ResolvePayload, key: string) => request<RecoveryResult>(`${target(project, task)}/resolve`, { method: "POST", body: JSON.stringify(payload), headers: { "Idempotency-Key": key } }),
    artifact: async (project: string, task: string, receipt: string, index: number, signal: AbortSignal) => (await response(`${target(project, task)}/receipts/${encodeURIComponent(receipt)}/artifacts/${index}`, { signal })).blob(),
  };
}
export type RecoveryApi = ReturnType<typeof createRecoveryApi>;
