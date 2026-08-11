import type { AdminActionResult, AdminApi, AdminContext, AdminDomain, AdminRecord, PageResult } from "./types";

export class AdminApiError extends Error {
  constructor(public status: number, public code: string, public recoverable: boolean, public requestId?: string) {
    super(code);
    this.name = "AdminApiError";
  }
}

const paths: Record<AdminDomain, string> = {
  business: "/api/v1/admin/business-objects",
  finance: "/api/v1/admin/finance",
  models: "/api/v1/admin/models",
  review: "/api/v1/admin/compliance",
  security: "/api/v1/admin/security",
  operations: "/api/v1/admin/operations",
  operationsConfig: "/api/v1/admin/operations-config",
  notifications: "/api/v1/admin/notifications",
  support: "/api/v1/admin/support-tickets",
  api: "/api/v1/admin/api-clients",
};

interface ClientOptions { sessionToken: string; workspaceId: string; projectId?: string; fetcher?: typeof fetch }

export function createAdminApiClient(options: ClientOptions): AdminApi {
  const fetcher = options.fetcher ?? fetch;
  const headers = (): Record<string, string> => ({
    Accept: "application/json",
    "Content-Type": "application/json",
    Authorization: `Bearer ${options.sessionToken}`,
    "X-Workspace-Id": options.workspaceId,
    ...(options.projectId ? { "X-Project-Id": options.projectId } : {}),
  });

  async function parse<T>(response: Response): Promise<T> {
    const body = await response.json().catch(() => ({})) as { code?: string; requestId?: string; detail?: { code?: string } };
    if (!response.ok) throw new AdminApiError(response.status, body.code ?? body.detail?.code ?? "ADMIN_REQUEST_FAILED", response.status === 409 || response.status >= 500, body.requestId);
    return body as T;
  }

  return {
    async getContext() {
      return parse<AdminContext>(await fetcher("/api/v1/admin/session/context", { method: "GET", credentials: "include", headers: headers() }));
    },
    async list(domain, query) {
      const search = new URLSearchParams(Object.entries(query).filter((entry): entry is [string, string | number] => entry[1] !== undefined).map(([key, value]) => [key, String(value)]));
      return parse<PageResult<AdminRecord>>(await fetcher(`${paths[domain]}?${search}`, { method: "GET", credentials: "include", headers: headers() }));
    },
    async act(domain, action, requestOptions) {
      const idempotencyKey = crypto.randomUUID();
      const init: RequestInit = { method: "POST", credentials: "include", headers: { ...headers(), "Idempotency-Key": idempotencyKey }, body: JSON.stringify(action) };
      try {
        return await parse<AdminActionResult>(await fetcher(`${paths[domain]}/actions`, init));
      } catch (error) {
        if (requestOptions?.retryNetworkOnce && error instanceof TypeError) return parse<AdminActionResult>(await fetcher(`${paths[domain]}/actions`, init));
        throw error;
      }
    },
    async download(path) {
      const response = await fetcher(path, { method: "GET", credentials: "include", headers: headers() });
      if (!response.ok) await parse<never>(response);
      return response.blob();
    },
  };
}
