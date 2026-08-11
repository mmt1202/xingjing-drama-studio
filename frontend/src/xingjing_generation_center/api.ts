import type { GeneratedCandidateSelection, GenerationCandidatePage, GenerationCostEvidence, GenerationModelAvailability, GenerationModelUsageReport, GenerationSubmission, GenerationTask, GenerationTaskDetail, GenerationTaskFilters, GenerationTaskPage } from "./contracts";

export class GenerationCenterApiError extends Error {
  constructor(
    public readonly status: number | null,
    public readonly code: string,
    public readonly requestId: string | null,
    message: string,
  ) {
    super(message);
    this.name = "GenerationCenterApiError";
  }
}

interface Envelope<T> { readonly data: T; readonly meta?: { readonly requestId?: string; readonly total?: number } }
interface ErrorEnvelope { readonly error?: { readonly code?: string; readonly message?: string; readonly details?: unknown }; readonly meta?: { readonly requestId?: string } }
export interface GenerationCenterApi { list(scope: string, offset?: number, limit?: number, signal?: AbortSignal, filters?: GenerationTaskFilters): Promise<GenerationTaskPage>; models(scope: string, signal?: AbortSignal): Promise<GenerationModelAvailability>; usage(scope: string, signal?: AbortSignal): Promise<readonly GenerationModelUsageReport[]>; candidates(scope: string, options?: { readonly mediaType?: "image" | "video"; readonly capability?: string; readonly offset?: number; readonly limit?: number; readonly signal?: AbortSignal }): Promise<GenerationCandidatePage>; detail(scope: string, taskId: string, signal?: AbortSignal): Promise<GenerationTaskDetail>; submit(scope: string, payload: GenerationSubmission, idempotencyKey: string): Promise<GenerationTask>; cancel(scope: string, task: GenerationTask, idempotencyKey: string): Promise<GenerationTask>; retry(scope: string, task: GenerationTask, idempotencyKey: string): Promise<GenerationTask>; selectCandidate(scope: string, taskId: string, assetId: string, expectedVersion: number, idempotencyKey: string): Promise<GeneratedCandidateSelection> }

export interface GenerationCenterApiOptions {
  readonly fetcher?: typeof fetch;
  readonly getAccessToken?: () => string | null | Promise<string | null>;
}

function key(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function createGenerationCenterApi({ fetcher = fetch, getAccessToken }: GenerationCenterApiOptions = {}): GenerationCenterApi {
  async function request<T>(path: string, init: RequestInit = {}): Promise<{ value: T; requestId: string | null; total: number | undefined }> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("X-Request-Id", key());
    if (init.body) headers.set("Content-Type", "application/json");
    const token = await getAccessToken?.();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    let response: Response;
    try { response = await fetcher(`/api/v1${path}`, { ...init, headers, credentials: "include" }); }
    catch (cause) { throw new GenerationCenterApiError(null, "NETWORK_ERROR", null, cause instanceof Error ? cause.message : "网络请求失败"); }
    const body = await response.json().catch(() => ({})) as Envelope<T> & ErrorEnvelope;
    const requestId = body.meta?.requestId ?? response.headers.get("X-Request-Id");
    if (!response.ok) throw new GenerationCenterApiError(response.status, body.error?.code ?? "GENERATION_REQUEST_FAILED", requestId, body.error?.message ?? "生成服务暂不可用");
    return { value: body.data, requestId, total: body.meta?.total };
  }
  const query = (projectId: string) => `projectId=${encodeURIComponent(projectId)}`;
  return {
    async list(projectId, offset = 0, limit = 50, signal, filters = {}) {
      const params = new URLSearchParams({ projectId, offset: String(offset), limit: String(limit) });
      if (filters.status) params.set("status", filters.status);
      if (filters.mediaType) params.set("mediaType", filters.mediaType);
      if (filters.capability) params.set("capability", filters.capability);
      const response = await request<{ tasks: readonly GenerationTask[] }>(`/generation-tasks?${params.toString()}`, { signal });
      return { tasks: response.value.tasks, total: response.total ?? response.value.tasks.length, requestId: response.requestId };
    },
    async models(projectId, signal) {
      const response = await request<{ model: GenerationModelAvailability }>(`/generation-models?${query(projectId)}`, { signal });
      return response.value.model;
    },
    async usage(projectId, signal) {
      const response = await request<{ usage: readonly GenerationModelUsageReport[] }>(`/generation-model-usage?${query(projectId)}`, { signal });
      return response.value.usage;
    },
    async candidates(projectId, options = {}) {
      const params = new URLSearchParams({ projectId, offset: String(options.offset ?? 0), limit: String(options.limit ?? 20) });
      if (options.mediaType) params.set("mediaType", options.mediaType);
      if (options.capability) params.set("capability", options.capability);
      const response = await request<{ candidates: GenerationCandidatePage["candidates"] }>(`/generation-candidates?${params.toString()}`, { signal: options.signal });
      return { candidates: response.value.candidates, total: response.total ?? response.value.candidates.length, requestId: response.requestId };
    },
    async detail(projectId, taskId, signal) {
      const response = await request<{ task: GenerationTask; generatedAssets: GenerationTaskDetail["generatedAssets"]; selectedCandidate: GeneratedCandidateSelection | null; costEvidence: readonly GenerationCostEvidence[]; billing: GenerationTaskDetail["billing"]; progress: GenerationTaskDetail["progress"] }>(`/generation-tasks/${encodeURIComponent(taskId)}?${query(projectId)}`, { signal });
      return { ...response.value, requestId: response.requestId };
    },
    async submit(projectId, payload, idempotencyKey) {
      const response = await request<{ task: GenerationTask }>(`/generation-tasks?${query(projectId)}`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload) });
      return response.value.task;
    },
    async cancel(projectId, task, idempotencyKey) {
      const response = await request<{ task: GenerationTask }>(`/generation-tasks/${encodeURIComponent(task.task_id)}/actions?${query(projectId)}`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey, "If-Match": String(task.version) }, body: JSON.stringify({ action: "cancel" }) });
      return response.value.task;
    },
    async retry(projectId, task, idempotencyKey) {
      const response = await request<{ task: GenerationTask }>(`/generation-tasks/${encodeURIComponent(task.task_id)}/retry?${query(projectId)}`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey } });
      return response.value.task;
    },
    async selectCandidate(projectId, taskId, assetId, expectedVersion, idempotencyKey) {
      const response = await request<{ selectedCandidate: GeneratedCandidateSelection }>(`/generation-tasks/${encodeURIComponent(taskId)}/candidates/${encodeURIComponent(assetId)}/selection?${query(projectId)}`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey, "If-Match": String(expectedVersion) } });
      return response.value.selectedCandidate;
    },
  };
}

export const generationCenterApi = createGenerationCenterApi();
