import type { ApiEnvelope, ApiFailure, AudioActionResult, AudioTasksData, AudioTracksData, LipSyncVersionsData, TrackVersionsData } from "./contracts";

export class XingjingApiError extends Error {
  constructor(public detail: ApiFailure) { super(detail.message); }
}

export interface RequestOptions { signal?: AbortSignal; idempotencyKey?: string; version?: number }
export interface AudioListOptions {
  offset?: number;
  limit?: number;
  q?: string;
  trackKind?: string;
  state?: string;
  language?: string;
  status?: string;
  taskKind?: string;
}
interface GenerationAudioApiOptions {
  /** Legacy route option; scope is resolved by the trusted server session and this value is ignored. */
  workspaceId?: string;
  fetcher?: typeof fetch;
  getAccessToken?: () => string | null | Promise<string | null>;
}

function failureMessage(code: string) {
  const messages: Record<string, string> = {
    AUDIO_PROVIDER_NOT_CONFIGURED: "媒体服务尚未配置。请联系工作区管理员配置 Provider 后重试。",
    AUDIO_OBJECT_STORAGE_NOT_CONFIGURED: "对象存储尚未配置。请先完成对象存储配置后再写入媒体版本。",
    AUDIO_RUNTIME_UNAVAILABLE: "音频服务暂不可用，请稍后重试。",
    XINGJING_AUDIO_DATABASE_URL_REQUIRED: "音频服务尚未完成数据库配置，请联系管理员恢复服务后重试。",
    AUDIO_RUNTIME_DEPENDENCY_UNAVAILABLE: "音频服务依赖不可用，请联系管理员恢复服务后重试。",
    AUDIO_PROVIDER_CALLBACK_UNAVAILABLE: "媒体服务未配置回调密钥，无法安全接收生成结果。",
    AUDIO_PROVIDER_ARTIFACT_STORAGE_UNAVAILABLE: "媒体产物存储未配置，无法保存生成结果。",
    AUDIO_BILLING_NOT_CONFIGURED: "音频任务计费尚未配置，暂时不能提交付费任务。",
    AUDIO_BILLING_ACCOUNT_MISSING: "当前工作区尚未开通可用账务账户。",
    AUDIO_INSUFFICIENT_CREDITS: "当前工作区余额不足，补充额度后可重新提交。",
    AUDIO_TASK_PRICE_NOT_CONFIGURED: "该媒体任务类型尚未配置正式价格。",
    AUDIO_BILLING_SETTLEMENT_REQUIRED: "Provider 未返回可核验费用，本次产物未结算。",
    AUDIO_TASK_FAILURE_NOT_RETRYABLE: "该失败由 Provider 标记为不可重试，请保留原版本或更换输入。",
    PERMISSION_DENIED: "你没有执行此音频操作的权限。",
    AUDIO_VERSION_OR_IDEMPOTENCY_CONFLICT: "版本或幂等请求发生冲突，请刷新服务端数据后重新提交。",
  };
  return messages[code] ?? code;
}

export function createGenerationAudioApi({ fetcher, getAccessToken }: GenerationAudioApiOptions = {}) {
  function listPath(path: string, options: AudioListOptions = {}) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(options)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    const suffix = query.toString();
    return suffix ? `${path}?${suffix}` : path;
  }

  async function request<T>(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<ApiEnvelope<T>> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
    if (options.version !== undefined) headers.set("If-Match", String(options.version));
    const token = await getAccessToken?.();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await (fetcher ?? fetch)(path, { ...init, headers, signal: options.signal, credentials: "include" });
    const body = await response.json().catch(() => ({})) as { data?: T; meta?: { requestId?: string }; error?: { code?: string } };
    if (!response.ok) {
      const code = body.error?.code ?? (response.status === 409 ? "AUDIO_VERSION_OR_IDEMPOTENCY_CONFLICT" : "REQUEST_FAILED");
      throw new XingjingApiError({ status: response.status, code, message: failureMessage(code), requestId: body.meta?.requestId ?? response.headers.get("X-Request-Id") ?? undefined });
    }
    if (body.data === undefined) throw new XingjingApiError({ status: response.status, code: "INVALID_AUDIO_ENVELOPE", message: "服务返回了无效的音频数据协议。", requestId: body.meta?.requestId });
    return { data: body.data, meta: body.meta ?? {} };
  }

  return {
    audioTracks: (projectId: string, options?: AudioListOptions, signal?: AbortSignal) => request<AudioTracksData>(listPath(`/api/v1/projects/${encodeURIComponent(projectId)}/audio-tracks`, options), {}, { signal }),
    audioTasks: (projectId: string, options?: AudioListOptions, signal?: AbortSignal) => request<AudioTasksData>(listPath(`/api/v1/projects/${encodeURIComponent(projectId)}/audio-tasks`, options), {}, { signal }),
    lipSyncVersions: (projectId: string, signal?: AbortSignal) => request<LipSyncVersionsData>(`/api/v1/projects/${encodeURIComponent(projectId)}/lip-sync-versions`, {}, { signal }),
    trackVersions: (projectId: string, trackKind: "audio" | "subtitle", trackId: string, signal?: AbortSignal) => request<TrackVersionsData>(`/api/v1/projects/${encodeURIComponent(projectId)}/${trackKind}-tracks/${encodeURIComponent(trackId)}/versions`, {}, { signal }),
    audioObjectUrl: (projectId: string, objectKey: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/audio-objects/${objectKey.split("/").map(encodeURIComponent).join("/")}`,
    generatedAssetUrl: (projectId: string, assetId: string) => `/api/v1/generation-assets/${encodeURIComponent(assetId)}/content?projectId=${encodeURIComponent(projectId)}`,
    audioAction: (projectId: string, payload: object, key: string, version?: number) => request<AudioActionResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/audio-actions`,
      { method: "POST", body: JSON.stringify(payload) },
      { idempotencyKey: key, version },
    ),
  };
}

export const generationAudioApi = createGenerationAudioApi();
