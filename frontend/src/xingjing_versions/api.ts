import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";
import type { VersionApi, VersionCommandResult, VersionPage, VersionRef, VersionReport } from "./contracts";

export class VersionApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) { super(code); }
}
function query(values: object) { return new URLSearchParams(Object.entries(values).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)])).toString(); }
export function createVersionApi(workspaceId: string, projectId: string, token: string | null): VersionApi {
  const fetcher = createAuthenticatedFetcher({ workspaceId });
  const prefix = `/api/v1/projects/${encodeURIComponent(projectId)}/version-history`;
  const retries = new Map<string, string>();
  let actorId = "";
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json"); headers.set("Accept-Language", i18n.resolvedLanguage ?? "zh");
    headers.set("X-Workspace-Id", workspaceId); headers.set("X-Project-Id", projectId); headers.set("X-Request-Id", crypto.randomUUID());
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetcher(`${prefix}${path}`, { ...init, headers, credentials: "same-origin" });
    const body = await response.json().catch(() => null) as { data?: T; error?: { code?: string }; detail?: { code?: string }; meta?: { requestId?: string } } | null;
    if (!response.ok || !body || body.data === undefined) throw new VersionApiError(response.status, body?.error?.code ?? body?.detail?.code ?? "VERSION_HISTORY_UNAVAILABLE", body?.meta?.requestId);
    return body.data;
  }
  async function write<T>(path: string, input: object): Promise<T> {
    const body = JSON.stringify(input), signature = `${path}:${body}`;
    if (!actorId) throw new VersionApiError(409, "VERSION_CONTEXT_REQUIRED");
    const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(signature)))].map((value) => value.toString(16).padStart(2, "0")).join("");
    const storageKey = `version-command:${JSON.stringify([actorId, workspaceId, projectId, hash])}`;
    let retained: string | null = null;
    try { retained = sessionStorage.getItem(storageKey); if (retained && !/^[a-f0-9-]{36}$/i.test(retained)) retained = null; } catch { /* The in-memory key still protects immediate retries. */ }
    const key = retries.get(signature) ?? retained ?? crypto.randomUUID(); retries.set(signature, key);
    try { sessionStorage.setItem(storageKey, key); } catch { /* Retry remains available when session storage is disabled. */ }
    const forget = () => { retries.delete(signature); try { sessionStorage.removeItem(storageKey); } catch { /* Storage may be disabled. */ } };
    try {
      const result = await request<T>(path, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body });
      forget(); return result;
    } catch (error) {
      if (error instanceof VersionApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) forget();
      throw error;
    }
  }
  const comparisonQuery = (baseline: VersionRef, candidate: VersionRef) => query({ kind: baseline.kind, objectId: baseline.objectId, shotId: baseline.shotId, baselineVersionId: baseline.versionId, candidateVersionId: candidate.versionId });
  return {
    async list(filters, signal) {
      const value = await request<VersionPage>(`?${query(filters)}`, { signal });
      if (value.scope.workspaceId !== workspaceId || value.scope.projectId !== projectId || !value.scope.actorId) throw new VersionApiError(502, "VERSION_SCOPE_MISMATCH");
      actorId = value.scope.actorId;
      return value;
    },
    detail: (ref, signal) => request(`/detail?${query(ref)}`, { signal }),
    compare: (baseline, candidate, signal) => request(`/compare?${comparisonQuery(baseline, candidate)}`, { signal }),
    checkpoints: (offset, signal) => request(`/checkpoints?offset=${offset}&limit=10`, { signal }),
    command: (input) => write<VersionCommandResult>("/actions", input),
    report: (baseline, candidate) => write<VersionReport>("/reports", { baseline, candidate }),
  };
}
