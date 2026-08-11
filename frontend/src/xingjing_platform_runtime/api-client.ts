import { ApiError, errorKind } from "./errors";
import type { SessionPort } from "./session";
import type { WorkspaceContext } from "./workspace";

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  readonly json?: unknown;
  readonly body?: BodyInit | null;
  readonly idempotencyKey?: string;
  readonly expectedVersion?: number;
}

export interface ApiClient {
  request<T = unknown>(path: string, options?: ApiRequestOptions): Promise<T>;
}

export interface ApiClientOptions {
  readonly baseUrl?: string;
  readonly fetcher?: typeof fetch;
  readonly session: SessionPort;
  readonly workspace: WorkspaceContext;
  readonly createId?: () => string;
  readonly onError?: (error: ApiError) => void;
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function fallbackId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const contentType = response.headers.get("content-type") ?? "";
  return contentType.includes("json") ? response.json().catch(() => null) : response.text().catch(() => "");
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  const fetcher = options.fetcher ?? fetch;
  const makeId = options.createId ?? fallbackId;
  return {
    async request<T>(path: string, request: ApiRequestOptions = {}): Promise<T> {
      const requestId = makeId();
      const method = (request.method ?? "GET").toUpperCase();
      const credential = await options.session.get();
      const headers = new Headers(request.headers);
      headers.set("Accept", "application/json");
      headers.set("X-Request-ID", requestId);
      headers.set("X-Workspace-ID", options.workspace.get().id);
      if (credential && credential.expiresAt > Date.now()) headers.set("Authorization", `Bearer ${credential.accessToken}`);
      if (request.json !== undefined) headers.set("Content-Type", "application/json");
      if (WRITE_METHODS.has(method)) headers.set("Idempotency-Key", request.idempotencyKey ?? requestId);
      if (request.expectedVersion !== undefined) headers.set("If-Match", String(request.expectedVersion));
      const { json, idempotencyKey: _key, expectedVersion: _version, ...init } = request;
      let response: Response;
      try {
        init.signal?.throwIfAborted();
        response = await fetcher(`${options.baseUrl ?? "/api/v1"}${path}`, {
          ...init,
          method,
          headers,
          body: json === undefined ? init.body : JSON.stringify(json),
        });
      } catch (cause) {
        if (request.signal?.aborted || (cause instanceof Error && cause.name === "AbortError")) throw cause;
        const error = new ApiError("网络请求失败", "network", null, null, requestId, true, null, cause);
        options.onError?.(error);
        throw error;
      }
      const payload = await parseBody(response);
      if (response.ok) return payload as T;
      if (response.status === 401) await options.session.clear();
      const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
      const retryAfter = Number(response.headers.get("retry-after"));
      const error = new ApiError(
        typeof record.message === "string" ? record.message : typeof record.detail === "string" ? record.detail : "请求失败",
        errorKind(response.status),
        response.status,
        typeof record.code === "string" ? record.code : null,
        response.headers.get("x-request-id") ?? requestId,
        response.status === 409 || response.status === 429 || response.status >= 500,
        Number.isFinite(retryAfter) ? retryAfter * 1_000 : null,
        payload,
      );
      options.onError?.(error);
      throw error;
    },
  };
}
