import type {
  CollaborationCommerceApi,
  CommerceActionFailure,
  CommerceActionResult,
  CommercePageContext,
  CommercePageQuery,
  CommerceRecord,
  CommerceRelatedSnapshot,
  CommerceSummaryMetric,
  CommerceWorkflowNode,
  SessionContext,
} from "./types";

type ApiErrorKind = "unauthenticated" | "forbidden" | "not_found" | "gone" | "conflict" | "rate_limited" | "server" | "network" | "contract";

export class CollaborationCommerceApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind,
    public readonly status: number | null,
    public readonly code: string,
    public readonly requestId: string | null,
    public readonly retryable: boolean,
    public readonly details: unknown = null,
  ) {
    super(message);
    this.name = "CollaborationCommerceApiError";
  }
}

interface ApiMeta {
  readonly requestId?: string;
  readonly serverTime?: string;
  readonly page?: { readonly size?: number; readonly nextToken?: string | null };
}

interface ApiEnvelope<T> {
  readonly data: T;
  readonly meta: ApiMeta;
}

interface ClientOptions {
  readonly baseUrl?: string;
  readonly fetcher?: typeof fetch;
  readonly accessToken?: string;
  readonly createId?: () => string;
}

const clientSafeActions = new Set(["verify", "comment", "approve", "reject"]);

function fallbackId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function camelKey(value: string): string {
  return value.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
}

function normalizePayload(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalizePayload);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [camelKey(key), normalizePayload(item)]),
  );
}

function errorKind(status: number): ApiErrorKind {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 410) return "gone";
  if (status === 409) return "conflict";
  if (status === 429) return "rate_limited";
  return "server";
}

function resolveEndpoint(endpoint: string, context: CommercePageContext, targetId?: string): string {
  const values: Record<string, string | undefined> = {
    workspaceId: context.workspaceId,
    projectId: context.projectId,
    reviewToken: context.reviewToken,
    targetId,
  };
  return endpoint.replace(/\{(workspaceId|projectId|reviewToken|targetId)\}/g, (_match, key: string) => {
    const value = values[key];
    if (!value?.trim()) {
      throw new CollaborationCommerceApiError("请求上下文不完整", "contract", null, "CONTEXT_REQUIRED", null, false, { key });
    }
    return encodeURIComponent(value);
  });
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const contentType = response.headers.get("content-type") ?? "";
  return contentType.includes("json") ? response.json().catch(() => null) : response.text().catch(() => "");
}

function summaryFrom(data: unknown): readonly CommerceSummaryMetric[] {
  if (!isRecord(data) || !Array.isArray(data.summary)) return [];
  return data.summary.filter(isRecord).flatMap((metric) => {
    if (typeof metric.key !== "string" || typeof metric.label !== "string" || (typeof metric.value !== "string" && typeof metric.value !== "number")) return [];
    return [{
      key: metric.key,
      label: metric.label,
      value: metric.value,
      format: typeof metric.format === "string" ? metric.format as CommerceSummaryMetric["format"] : undefined,
      currency: typeof metric.currency === "string" ? metric.currency : undefined,
    }];
  });
}

function workflowFrom(data: unknown): readonly CommerceWorkflowNode[] {
  if (!isRecord(data) || !Array.isArray(data.workflow)) return [];
  return data.workflow.filter(isRecord).flatMap((node) => {
    if (typeof node.id !== "string" || typeof node.label !== "string" || typeof node.status !== "string") return [];
    return [{ id: node.id, label: node.label, status: node.status, occurredAt: typeof node.occurredAt === "string" ? node.occurredAt : undefined }];
  });
}

function itemsFrom(data: unknown): readonly CommerceRecord[] {
  const normalized = normalizePayload(data);
  if (Array.isArray(normalized)) return normalized.filter(isRecord);
  if (!isRecord(normalized)) return [];
  if (Array.isArray(normalized.items)) return normalized.items.filter(isRecord);
  const wrapped = Object.values(normalized).find(Array.isArray);
  if (Array.isArray(wrapped)) return wrapped.filter(isRecord);
  return [];
}

function detailFrom(data: unknown): CommerceRecord | undefined {
  const normalized = normalizePayload(data);
  if (!isRecord(normalized)) return undefined;
  const values = Object.values(normalized);
  if (values.length === 1 && isRecord(values[0])) return values[0];
  return normalized;
}

function normalizeActionResult(data: unknown, meta: ApiMeta): CommerceActionResult {
  const record = isRecord(data) ? data : {};
  const failures: CommerceActionFailure[] = Array.isArray(record.failures)
    ? record.failures.filter(isRecord).flatMap((failure) => typeof failure.id === "string" && typeof failure.code === "string" && typeof failure.message === "string"
      ? [{ id: failure.id, code: failure.code, message: failure.message }]
      : [])
    : [];
  const retryableIds = Array.isArray(record.retryableIds) ? record.retryableIds.filter((id): id is string => typeof id === "string") : [];
  const status = record.status === "processing" || record.status === "partial" || record.status === "failed" || record.status === "succeeded" ? record.status : "succeeded";
  const nestedObject = Object.values(record).find(isRecord);
  return {
    requestId: typeof record.requestId === "string" ? record.requestId : meta.requestId ?? "",
    status,
    failures,
    retryableIds,
    object: isRecord(record.object) ? record.object : nestedObject,
  };
}

export function createCollaborationCommerceApi(options: ClientOptions = {}): CollaborationCommerceApi {
  const fetcher = options.fetcher ?? fetch;
  const baseUrl = options.baseUrl ?? "/api/v1";
  const createId = options.createId ?? fallbackId;
  const reviewAccessSecrets = new Map<string, string>();
  const reviewSessions = new Map<string, { id: string; ticket: string; deliveryVersion: number }>();

  function headers(context?: CommercePageContext, body = false, idempotencyKey?: string, version?: number): Record<string, string> {
    const result: Record<string, string> = { Accept: "application/json" };
    if (body) result["Content-Type"] = "application/json";
    if (options.accessToken) result.Authorization = `Bearer ${options.accessToken}`;
    if (context?.workspaceId) result["X-Workspace-Id"] = context.workspaceId;
    if (context?.projectId) result["X-Project-Id"] = context.projectId;
    if (context?.reviewToken) {
      const accessSecret = reviewAccessSecrets.get(context.reviewToken);
      const reviewSession = reviewSessions.get(context.reviewToken);
      if (accessSecret) result["X-Review-Access-Secret"] = accessSecret;
      if (reviewSession) result["X-Review-Session-Id"] = reviewSession.id;
    }
    if (idempotencyKey) result["Idempotency-Key"] = idempotencyKey;
    if (version !== undefined) result["If-Match"] = `"${version}"`;
    return result;
  }

  async function parse<T>(response: Response): Promise<ApiEnvelope<T>> {
    const payload = await readBody(response);
    if (!response.ok) {
      const envelope = isRecord(payload) ? payload : {};
      const error = isRecord(envelope.error) ? envelope.error : envelope;
      const meta = isRecord(envelope.meta) ? envelope.meta : {};
      throw new CollaborationCommerceApiError(
        typeof error.message === "string" ? error.message : "请求失败",
        errorKind(response.status),
        response.status,
        typeof error.code === "string" ? error.code : "REQUEST_FAILED",
        typeof meta.requestId === "string" ? meta.requestId : response.headers.get("x-request-id"),
        typeof error.retryable === "boolean" ? error.retryable : response.status === 409 || response.status === 429 || response.status >= 500,
        error.details,
      );
    }
    if (!isRecord(payload) || !("data" in payload) || !isRecord(payload.meta)) {
      throw new CollaborationCommerceApiError("服务响应格式无效", "contract", response.status, "INVALID_RESPONSE", response.headers.get("x-request-id"), false, payload);
    }
    return payload as unknown as ApiEnvelope<T>;
  }

  async function get<T>(endpoint: string, context: CommercePageContext, query: CommercePageQuery = {}, signal?: AbortSignal): Promise<ApiEnvelope<T>> {
    const search = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value !== undefined && value !== "") search.set(key, String(value)); });
    const suffix = search.size ? `?${search.toString()}` : "";
    try {
      return await parse<T>(await fetcher(`${baseUrl}${resolveEndpoint(endpoint, context)}${suffix}`, {
        method: "GET",
        credentials: "include",
        headers: headers(context),
        signal,
      }));
    } catch (cause) {
      if (cause instanceof CollaborationCommerceApiError || signal?.aborted || (cause instanceof Error && cause.name === "AbortError")) throw cause;
      throw new CollaborationCommerceApiError("网络请求失败", "network", null, "NETWORK_ERROR", null, true, cause);
    }
  }

  return {
    async getSessionContext(signal) {
      const envelope = await get<SessionContext>("/session/context", {}, {}, signal);
      return envelope.data;
    },

    async loadPage(route, context, query = {}, signal) {
      route.requiredContext.forEach((key) => {
        if (!context[key]?.trim()) throw new CollaborationCommerceApiError("页面上下文不完整", "contract", null, "CONTEXT_REQUIRED", null, false, { key });
      });
      const [primary, ...related] = await Promise.all([
        get<unknown>(route.loadEndpoint, context, query, signal),
        ...route.relatedEndpoints.map((endpoint) => get<unknown>(endpoint, context, query, signal)),
      ]);
      let primaryData = primary.data;
      if (context.reviewToken && isRecord(primaryData)) {
        const session = isRecord(primaryData.session) ? primaryData.session : null;
        const review = isRecord(primaryData.review) ? primaryData.review : null;
        const delivery = review && isRecord(review.delivery) ? review.delivery : null;
        if (session && typeof session.id === "string" && typeof session.ticket === "string") {
          reviewSessions.set(context.reviewToken, {
            id: session.id,
            ticket: session.ticket,
            deliveryVersion: typeof delivery?.version === "number" ? delivery.version : 1,
          });
          primaryData = {
            ...primaryData,
            mediaUrl: `${baseUrl}/review-links/${encodeURIComponent(context.reviewToken)}/media?session=${encodeURIComponent(session.id)}&ticket=${encodeURIComponent(session.ticket)}`,
          };
        }
      }
      const relatedSnapshots: CommerceRelatedSnapshot[] = related.map((envelope, index) => ({
        endpoint: route.relatedEndpoints[index] ?? "",
        data: envelope.data,
      }));
      return {
        requestId: primary.meta.requestId ?? "",
        serverTime: primary.meta.serverTime,
        items: itemsFrom(primaryData),
        detail: detailFrom(primaryData),
        summary: summaryFrom(primaryData),
        workflow: workflowFrom(primaryData),
        related: relatedSnapshots,
        nextPageToken: primary.meta.page?.nextToken ?? null,
      };
    },

    async downloadExport(route, context, query, signal) {
      if (!route.exportEndpoint) {
        throw new CollaborationCommerceApiError("当前页面不支持导出", "contract", null, "EXPORT_NOT_SUPPORTED", null, false);
      }
      const endpoint = resolveEndpoint(route.exportEndpoint, context);
      const separator = endpoint.includes("?") ? "&" : "?";
      const url = query?.trim() ? `${baseUrl}${endpoint}${separator}query=${encodeURIComponent(query.trim())}` : `${baseUrl}${endpoint}`;
      const response = await fetcher(url, {
        method: "GET",
        credentials: "include",
        headers: headers(context),
        signal,
      });
      if (!response.ok) {
        const payload = await readBody(response);
        const envelope = isRecord(payload) ? payload : {};
        const error = isRecord(envelope.error) ? envelope.error : envelope;
        const meta = isRecord(envelope.meta) ? envelope.meta : {};
        throw new CollaborationCommerceApiError(
          typeof error.message === "string" ? error.message : "导出失败",
          errorKind(response.status),
          response.status,
          typeof error.code === "string" ? error.code : "EXPORT_FAILED",
          typeof meta.requestId === "string" ? meta.requestId : response.headers.get("x-request-id"),
          response.status >= 500,
          error.details,
        );
      }
      const disposition = response.headers.get("content-disposition") ?? "";
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      return { blob: await response.blob(), filename: match?.[1] ?? `${route.id}.csv` };
    },

    async executeAction(route, requestedAction, context, input, requestOptions = {}) {
      const action = route.actions.find((candidate) => candidate.id === requestedAction.id);
      if (!action || (route.audience === "client" && (!action.clientSafe || !clientSafeActions.has(action.id)))) {
        throw new CollaborationCommerceApiError("客户上下文不允许该操作", "forbidden", 403, "CLIENT_ACTION_FORBIDDEN", null, false);
      }
      const idempotencyKey = createId();
      const endpoint = resolveEndpoint(action.endpoint, context, input.targetId);
      const effectiveVersion = input.version ?? (action.id === "saveEnterpriseProfile" ? 0 : undefined);
      const flatPayload = Object.fromEntries(Object.entries(input.payload).map(([key, value]) => [
        key,
        (key === "permissions" || key === "dataScope") && typeof value === "string"
          ? value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean)
          : value,
      ]));
      const normalizedPayload = nestPayload(flatPayload);
      let body: Record<string, unknown> = action.bodyKind === "entity"
        ? { ...normalizedPayload, ...(input.targetId ? { targetId: input.targetId } : {}), ...(effectiveVersion !== undefined ? { version: effectiveVersion } : {}) }
        : { action: action.id, ...(input.targetId ? { targetId: input.targetId } : {}), payload: normalizedPayload, ...(effectiveVersion !== undefined ? { version: effectiveVersion } : {}) };
      if (route.audience === "client" && context.reviewToken) {
        const session = reviewSessions.get(context.reviewToken);
        if (action.id === "verify") {
          const credential = normalizedPayload.credential;
          if (typeof credential === "string" && credential.trim()) reviewAccessSecrets.set(context.reviewToken, credential.trim());
        } else if (action.id === "comment") {
          body = { action: "comment", timecodeMs: normalizedPayload.timecodeMs, body: normalizedPayload.comment, severity: "normal" };
        } else if (action.id === "approve" || action.id === "reject") {
          body = {
            action: "decision",
            decision: action.id === "approve" ? "approved" : "changes_requested",
            note: normalizedPayload.reason,
            version: session?.deliveryVersion ?? 1,
          };
        }
      }
      const init: RequestInit = {
        method: action.method,
        credentials: "include",
        headers: headers(context, true, idempotencyKey, effectiveVersion),
        body: JSON.stringify(body),
        signal: requestOptions.signal,
      };
      const send = async () => parse<unknown>(await fetcher(`${baseUrl}${endpoint}`, init));
      try {
        const envelope = await send();
        return normalizeActionResult(envelope.data, envelope.meta);
      } catch (cause) {
        if (route.audience === "client" && action.id === "verify" && context.reviewToken) {
          reviewAccessSecrets.delete(context.reviewToken);
        }
        if (requestOptions.retryNetworkOnce && cause instanceof TypeError && !requestOptions.signal?.aborted) {
          const envelope = await send();
          return normalizeActionResult(envelope.data, envelope.meta);
        }
        if (cause instanceof CollaborationCommerceApiError || requestOptions.signal?.aborted || (cause instanceof Error && cause.name === "AbortError")) throw cause;
        throw new CollaborationCommerceApiError("网络请求失败", "network", null, "NETWORK_ERROR", null, true, cause);
      }
    },
  };
}

function nestPayload(payload: Readonly<Record<string, unknown>>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    const [parent, child, ...rest] = key.split(".");
    if (!child || rest.length > 0) {
      result[key] = value;
      continue;
    }
    const current = result[parent];
    result[parent] = {
      ...(isRecord(current) ? current : {}),
      [child]: value,
    };
  }
  return result;
}
