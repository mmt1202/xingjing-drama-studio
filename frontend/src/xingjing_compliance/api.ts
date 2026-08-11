import type {
  ApiEnvelope,
  ApiErrorEnvelope,
  ComplianceEvidence,
  ComplianceExportPort,
  ComplianceReviewResult,
  ComplianceScope,
  DeliveryRecord,
  ExportContextOption,
  ExportPreflight,
} from "./contracts";

export class ComplianceApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = "ComplianceApiError";
  }
}

export interface ComplianceApiOptions {
  readonly fetcher?: typeof fetch;
  readonly baseUrl?: string;
  /** The route layer supplies the active session; server-side RBAC remains authoritative. */
  readonly getAccessToken?: () => string | null | Promise<string | null>;
  readonly createIdempotencyKey?: () => string;
}

function projectPath(projectId: string) {
  return `/projects/${encodeURIComponent(projectId)}`;
}

function scopeQuery(scope: ComplianceScope) {
  return new URLSearchParams({
    projectVersion: scope.projectVersion,
    target: scope.target,
  }).toString();
}

export function createComplianceExportApi({
  fetcher = fetch,
  baseUrl = "/api/v1",
  getAccessToken,
  createIdempotencyKey = () => crypto.randomUUID(),
}: ComplianceApiOptions = {}): ComplianceExportPort {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      const headers = new Headers(init.headers);
      headers.set("Accept", "application/json");
      if (init.body) headers.set("Content-Type", "application/json");
      const token = await getAccessToken?.();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        credentials: "same-origin",
        headers,
      });
    } catch {
      throw new ComplianceApiError(
        0,
        "NETWORK_ERROR",
        "网络连接失败，请检查网络后重试",
      );
    }

    const body = (await response.json().catch(() => null)) as
      (ApiEnvelope<T> & ApiErrorEnvelope) | null;
    if (!response.ok) {
      throw new ComplianceApiError(
        response.status,
        body?.error?.code ?? "REQUEST_FAILED",
        body?.error?.message ?? "服务未返回可用结果",
        body?.meta?.requestId,
      );
    }
    if (!body || !("data" in body)) {
      throw new ComplianceApiError(
        response.status,
        "INVALID_RESPONSE",
        "服务响应不符合数据契约",
      );
    }
    return body.data;
  }

  async function requestBlob(
    path: string,
    signal?: AbortSignal,
  ): Promise<Blob> {
    const headers = new Headers({ Accept: "application/octet-stream" });
    const token = await getAccessToken?.();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetcher(`${baseUrl}${path}`, {
      credentials: "same-origin",
      headers,
      signal,
    });
    if (!response.ok) {
      throw new ComplianceApiError(
        response.status,
        "DOWNLOAD_FAILED",
        "正式交付文件下载失败",
      );
    }
    return response.blob();
  }

  return {
    getCompliance: (scope, signal) =>
      request<ComplianceEvidence>(
        `${projectPath(scope.projectId)}/compliance?${scopeQuery(scope)}`,
        { signal },
      ),
    listExportContexts: (projectId, signal) =>
      request<{ contexts: readonly ExportContextOption[] }>(
        `${projectPath(projectId)}/export-contexts`,
        { signal },
      ).then((data) => data.contexts),
    listDeliveries: (projectId, signal) =>
      request<{ deliveries: readonly DeliveryRecord[] }>(
        `${projectPath(projectId)}/exports`,
        { signal },
      ).then((data) => data.deliveries),
    preflightExport: (scope, signal) =>
      request<{ preflight: ExportPreflight }>(
        `${projectPath(scope.projectId)}/exports/preflight?${scopeQuery(scope)}`,
        { method: "POST", signal },
      ).then((data) => data.preflight),
    applyReviewAction: (scope, input, signal) =>
      request<{ review: ComplianceReviewResult }>(
        `${projectPath(scope.projectId)}/compliance/actions`,
        {
          method: "POST",
          signal,
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            projectVersion: scope.projectVersion,
            action: input.action,
            reason: input.reason,
            expectedVersion: input.expectedVersion,
          }),
        },
      ).then((data) => data.review),
    submitExport: (scope, outputFormat, signal) =>
      request<{ delivery: DeliveryRecord }>(
        `${projectPath(scope.projectId)}/exports`,
        {
          method: "POST",
          signal,
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            projectVersion: scope.projectVersion,
            target: scope.target,
            format: outputFormat,
          }),
        },
      ).then((data) => data.delivery),
    recordAuthorization: (scope, input, signal) =>
      request<{ authorization: Readonly<Record<string, unknown>> }>(
        `${projectPath(scope.projectId)}/compliance/actions`,
        {
          method: "POST",
          signal,
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            projectVersion: scope.projectVersion,
            action: "record_authorization",
            ...input,
          }),
        },
      ).then((data) => data.authorization),
    downloadDelivery: (projectId, requestId, signal) =>
      requestBlob(
        `${projectPath(projectId)}/exports/${encodeURIComponent(requestId)}/download`,
        signal,
      ),
    runComplianceCheck: (scope, signal) =>
      request<{ assessment: Readonly<Record<string, unknown>> }>(
        `${projectPath(scope.projectId)}/compliance/actions`,
        {
          method: "POST",
          signal,
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            projectVersion: scope.projectVersion,
            target: scope.target,
            action: "run_check",
          }),
        },
      ).then((data) => data.assessment),
  };
}
