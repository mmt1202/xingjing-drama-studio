import type { CreatorProjectsPort, PageResult, ProjectAction, ProjectDetail, ProjectSummary, ScriptAction, ScriptActionResult, ScriptDocument, SessionContext } from "./types";

interface Envelope<T> { data: T; meta?: { requestId?: string; total?: number; page?: { nextToken?: string | null } } }
interface ErrorEnvelope { error?: { code?: string; message?: string; retryable?: boolean; details?: Record<string, unknown> }; meta?: { requestId?: string } }
interface IdentitySessionContext {
  user: { id: string; displayName?: string; email?: string };
  currentWorkspace: { id: string; name: string; role: string } | null;
}
type ScriptActionEnvelope = ScriptActionResult | { script: ScriptDocument } | { comparison: ScriptActionResult };

export class CreatorApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string, public retryable = false, public details?: Record<string, unknown>) {
    super(message);
    this.name = "CreatorApiError";
  }
}

// workspaceId is intentionally retained only for route-bridge compatibility. Identity and scope come from the host session.
interface ApiOptions {
  workspaceId?: string;
  fetcher?: typeof fetch;
  createId?: () => string;
  baseUrl?: string;
  getAccessToken?: () => string | null | Promise<string | null>;
}

export function createCreatorProjectsApi({ fetcher = fetch, createId = () => crypto.randomUUID(), baseUrl = "/api/v1", getAccessToken }: ApiOptions = {}): CreatorProjectsPort {
  const retryKeys = new Map<string, string>();

  async function request<T>(path: string, init: RequestInit = {}, revision?: number): Promise<{ value: T; nextToken: string | null; total?: number }> {
    const headers: Record<string, string> = { Accept: "application/json", ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}) };
    const token = await getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (revision !== undefined) headers["If-Match"] = `"${revision}"`;
    const response = await fetcher(`${baseUrl}${path}`, { ...init, credentials: "same-origin", headers: { ...headers, ...(init.headers as Record<string, string> | undefined) } });
    const body = await response.json().catch(() => ({})) as Envelope<T> & ErrorEnvelope;
    if (!response.ok) {
      throw new CreatorApiError(response.status, body.error?.code ?? "REQUEST_FAILED", body.error?.message ?? "请求失败", body.meta?.requestId, body.error?.retryable, body.error?.details);
    }
    return { value: body.data, nextToken: body.meta?.page?.nextToken ?? null, total: body.meta?.total };
  }

  const action = async <T>(path: string, name: ScriptAction | ProjectAction, payload: unknown, revision?: number, targetId?: string): Promise<T> => {
    const body = JSON.stringify({ action: name, ...(targetId ? { targetId } : {}), payload: payload ?? {} });
    const operation = `${path}:${revision ?? "new"}:${body}`;
    const key = retryKeys.get(operation) ?? createId();
    retryKeys.set(operation, key);
    try {
      const result = await request<T>(path, { method: "POST", body, headers: { "Idempotency-Key": key } }, revision);
      retryKeys.delete(operation);
      return result.value;
    } catch (error) {
      // Retain the key only for a safe retry of an interrupted write; a response proves the request reached the server.
      if (error instanceof CreatorApiError) retryKeys.delete(operation);
      throw error;
    }
  };

  return {
    getSessionContext: () => request<IdentitySessionContext>("/session/context").then((r): SessionContext => {
      if (!r.value.currentWorkspace) {
        throw new CreatorApiError(403, "WORKSPACE_CONTEXT_REQUIRED", "请先选择工作区");
      }
      const role = r.value.currentWorkspace.role.toUpperCase();
      const canManage = role === "OWNER" || role === "ADMIN" || role === "PLATFORM_ADMIN";
      return {
        user: { id: r.value.user.id, name: r.value.user.displayName || r.value.user.email || r.value.user.id },
        workspace: { id: r.value.currentWorkspace.id, name: r.value.currentWorkspace.name },
        permissions: canManage
          ? ["project.view", "project.manage", "script.view", "script.manage"]
          : ["project.view", "script.view"],
        featureFlags: {},
      };
    }),
    listProjects: (params = {}) => {
      const q = new URLSearchParams({ sort: params.sort ?? "updatedAt:desc,id:desc" });
      if (params.archived !== undefined) q.set("archived", String(params.archived));
      if (params.pageToken) q.set("pageToken", params.pageToken);
      return request<ProjectSummary[]>(`/projects?${q}`).then((r): PageResult<ProjectSummary> => ({ items: r.value, nextToken: r.nextToken }));
    },
    getProject: (id) => request<ProjectDetail>(`/projects/${encodeURIComponent(id)}`).then((r) => r.value),
    projectAction: (id, name: ProjectAction, payload, version) => action(`/projects/${encodeURIComponent(id)}/actions`, name, payload, version),
    listScripts: (id, params = {}) => {
      const offset = params.offset ?? 0;
      const limit = params.limit ?? 50;
      const q = new URLSearchParams({ offset: String(offset), limit: String(limit) });
      if (params.query) q.set("q", params.query);
      if (params.locked !== undefined) q.set("locked", String(params.locked));
      return request<{ scripts: ScriptDocument[] }>(`/projects/${encodeURIComponent(id)}/scripts?${q}`).then((r): PageResult<ScriptDocument> => ({
        items: r.value.scripts,
        nextToken: offset + r.value.scripts.length < (r.total ?? 0) ? String(offset + r.value.scripts.length) : null,
        total: r.total,
        offset,
        limit,
      }));
    },
    importScriptFile: async (id, title, file) => {
      const form = new FormData();
      form.set("title", title);
      form.set("file", file);
      const operation = `script-import:${id}:${file.name}:${file.size}:${file.lastModified}`;
      const key = retryKeys.get(operation) ?? createId();
      retryKeys.set(operation, key);
      try {
        const result = await request<{ script: ScriptDocument }>(
          `/projects/${encodeURIComponent(id)}/scripts/imports`,
          { method: "POST", body: form, headers: { "Idempotency-Key": key } },
        );
        retryKeys.delete(operation);
        return result.value.script;
      } catch (error) {
        if (error instanceof CreatorApiError) retryKeys.delete(operation);
        throw error;
      }
    },
    scriptAction: async (id, name: ScriptAction, payload, revision, targetId) => {
      const result = await action<ScriptActionEnvelope>(`/projects/${encodeURIComponent(id)}/scripts/actions`, name, payload, revision, targetId);
      if ("script" in result) return result.script;
      if ("comparison" in result) return result.comparison;
      return result;
    },
  };
}
