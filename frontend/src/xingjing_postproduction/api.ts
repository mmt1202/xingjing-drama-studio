import type { ComplianceRecord, ExportTask, Page, ProjectScope, SessionContext, TaskReceipt, Timeline } from "./types";

interface ErrorPayload { code?: string; message?: string; request_id?: string }

export class PostproductionApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) { super(message); this.name = "PostproductionApiError"; }
}

export interface PostproductionApiOptions {
  fetcher?: typeof fetch;
  createIdempotencyKey?: () => string;
}

const defaultKey = () => crypto.randomUUID();
const projectPath = (scope: ProjectScope) => `/api/v1/projects/${encodeURIComponent(scope.projectId)}`;

export function createPostproductionApi(options: PostproductionApiOptions = {}) {
  const fetcher = options.fetcher ?? fetch;
  const createKey = options.createIdempotencyKey ?? defaultKey;

  async function request<T>(scope: Pick<ProjectScope, "workspaceId">, path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetcher(path, {
      ...init,
      headers: { Accept: "application/json", "X-Workspace-Id": scope.workspaceId, ...init.headers },
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ErrorPayload;
      throw new PostproductionApiError(response.status, body.code ?? "REQUEST_FAILED", body.message ?? response.statusText, body.request_id);
    }
    return response.json() as Promise<T>;
  }

  function write<T>(scope: ProjectScope, path: string, body: unknown, signal?: AbortSignal) {
    return request<T>(scope, path, {
      method: "POST", signal,
      headers: { "Content-Type": "application/json", "Idempotency-Key": createKey() },
      body: JSON.stringify(body),
    });
  }

  return {
    getSessionContext: (workspaceId: string, signal?: AbortSignal) =>
      request<SessionContext>({ workspaceId }, "/api/v1/session/context", { signal }),
    listTimelines: (scope: ProjectScope, signal?: AbortSignal) =>
      request<Page<Timeline>>(scope, `${projectPath(scope)}/timelines${scope.episodeId ? `?episode_id=${encodeURIComponent(scope.episodeId)}` : ""}`, { signal }),
    createRenderTask: (scope: ProjectScope, input: { timelineId: string; version: number }, signal?: AbortSignal) =>
      write<TaskReceipt>(scope, `${projectPath(scope)}/render-tasks`, { timeline_id: input.timelineId, version: input.version, episode_id: scope.episodeId }, signal),
    listCompliance: (scope: ProjectScope, signal?: AbortSignal) =>
      request<Page<ComplianceRecord>>(scope, `${projectPath(scope)}/compliance`, { signal }),
    performComplianceAction: (scope: ProjectScope, input: { action: "check" | "appeal" | "add_evidence"; recordId?: string; version?: number; reason?: string }, signal?: AbortSignal) =>
      write<TaskReceipt>(scope, `${projectPath(scope)}/compliance/actions`, { action: input.action, record_id: input.recordId, version: input.version, reason: input.reason }, signal),
    listExports: (scope: ProjectScope, signal?: AbortSignal) =>
      request<Page<ExportTask>>(scope, `${projectPath(scope)}/exports`, { signal }),
    createExport: (scope: ProjectScope, input: { projectVersion: number; target: string; format: string }, signal?: AbortSignal) =>
      write<TaskReceipt>(scope, `${projectPath(scope)}/exports`, { project_version: input.projectVersion, target: input.target, format: input.format }, signal),
  };
}

export type PostproductionApi = ReturnType<typeof createPostproductionApi>;
