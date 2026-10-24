import type { SessionContext } from "@/xingjing_identity/api";
import { createAuthenticatedFetcher } from "@/utils/auth";

export interface WorkspaceAccess { workspaceId: string; actorId: string; permissions: string[]; workspaceName: string; userName: string }
export interface RecentProject { id: string; name: string; type: string; targetPlatform: string; setupState: "draft" | "ready"; productionStatus: string; version: number; updatedAt: string }
export interface WorkspaceTask { id: string; projectId: string; projectName: string; status: string; version: number; updatedAt: string }
export interface HomePage<T> { workspaceId: string; items: T[]; total: number; offset: number; limit: number }
export class HomeApiError extends Error {
  constructor(public status: number, public code: string, public requestId?: string) { super(code); }
}
export function createHomeApi(workspaceId: string, token: string | null) {
  const fetcher = createAuthenticatedFetcher({ workspaceId });
  async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetcher(`/api/v1${path}`, { credentials: "include", signal, headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
    const body = await response.json() as { data: T; error?: { code: string }; meta?: { requestId: string } };
    if (!response.ok) throw new HomeApiError(response.status, body.error?.code ?? "REQUEST_FAILED", body.meta?.requestId);
    return body.data;
  }
  const base = `/workspaces/${encodeURIComponent(workspaceId)}`;
  return {
    async context(signal?: AbortSignal): Promise<WorkspaceAccess> {
      const [access, identity] = await Promise.all([request<Omit<WorkspaceAccess, "workspaceName" | "userName">>(`${base}/creator-context`, signal), request<SessionContext>("/session/context", signal)]);
      if (access.workspaceId !== workspaceId || identity.currentWorkspace?.id !== workspaceId || identity.user.id !== access.actorId) throw new HomeApiError(404, "WORKSPACE_NOT_FOUND");
      return { ...access, workspaceName: identity.currentWorkspace.name, userName: identity.user.displayName || identity.user.email };
    },
    projects: (query: URLSearchParams, signal?: AbortSignal) => request<HomePage<RecentProject>>(`${base}/creator-home?${query}`, signal),
    tasks: (query: URLSearchParams, signal?: AbortSignal) => request<HomePage<WorkspaceTask>>(`${base}/generation-tasks?${query}`, signal),
  };
}
export type HomeApi = ReturnType<typeof createHomeApi>;
