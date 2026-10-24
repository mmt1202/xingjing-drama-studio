import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";
import type { CoverAction, CoverApi } from "./contracts";

export class CoverApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) { super(code); }
}
export function createCoverApi(workspaceId: string, projectId: string, token: string | null): CoverApi {
  const fetcher = createAuthenticatedFetcher({ workspaceId });
  const prefix = `/api/v1/projects/${encodeURIComponent(projectId)}/cover-plans`;
  const retries = new Map<string, string>();
  function headers(init?: HeadersInit) {
    const result = new Headers(init);
    result.set("Accept", "application/json");
    result.set("Accept-Language", i18n.resolvedLanguage ?? "zh");
    result.set("X-Workspace-Id", workspaceId);
    result.set("X-Request-Id", crypto.randomUUID());
    if (token) result.set("Authorization", `Bearer ${token}`);
    return result;
  }
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetcher(`${prefix}${path}`, { ...init, credentials: "include", headers: headers(init.headers) });
    const body = await response.json().catch(() => null) as { data?: T; error?: { code?: string }; detail?: { code?: string }; meta?: { requestId?: string } } | null;
    if (!response.ok || !body || body.data === undefined) throw new CoverApiError(response.status, body?.error?.code ?? body?.detail?.code ?? "COVER_UNAVAILABLE", body?.meta?.requestId);
    return body.data;
  }
  async function write(path: string, body: object): Promise<CoverAction> {
    const content = JSON.stringify(body);
    const signature = `${path}:${content}`;
    const idempotencyKey = retries.get(signature) ?? crypto.randomUUID();
    retries.set(signature, idempotencyKey);
    try {
      const result = await request<CoverAction>(path, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: content });
      retries.delete(signature);
      return result;
    } catch (error) {
      if (error instanceof CoverApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) retries.delete(signature);
      throw error;
    }
  }
  return {
    catalog: (signal) => request("/catalog", { signal }),
    list: (filters, signal) => request(`?${new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== "").map(([key, value]) => [key, String(value)]))}`, { signal }),
    detail: (id, signal) => request(`/${encodeURIComponent(id)}`, { signal }),
    create: (input) => write("", input),
    action: (plan, action, payload = {}) => write(`/${encodeURIComponent(plan.id)}/actions`, { action, version: plan.version, payload }),
    async media(planId, assetId, signal) {
      const response = await fetcher(`${prefix}/${encodeURIComponent(planId)}/candidates/${encodeURIComponent(assetId)}/content`, { credentials: "include", signal, headers: headers() });
      if (!response.ok) throw new CoverApiError(response.status, "COVER_MEDIA_UNAVAILABLE");
      if (!(response.headers.get("Content-Type") ?? "").startsWith("image/")) throw new CoverApiError(502, "COVER_MEDIA_UNAVAILABLE");
      return response.blob();
    },
  };
}
