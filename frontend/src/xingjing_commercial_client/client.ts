import { parseCommercialMilestonePage, parseCommercialOrderPage, parseVersionedCommercialOrder } from "./codec";
import {
  CommercialApiError,
  type CommercialApiErrorKind,
  type CommercialConflictKind,
} from "./errors";
import type {
  AcceptDeliveryInput,
  CommercialClient,
  CommercialClientOptions,
  CommercialCommandOptions,
  CommercialOrderListQuery,
  OpenDisputeInput,
  RecordContractInput,
  ResolveDisputeInput,
  ReturnDeliveryInput,
  SettlementConfirmationInput,
  SubmitDeliveryInput,
  SubmitQuoteInput,
  VersionedCommercialOrder,
} from "./types";

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredOption(value: string, name: string): string {
  const normalized = value.trim();
  if (!normalized) throw new TypeError(`${name} must not be blank`);
  return normalized;
}

function statusKind(status: number): CommercialApiErrorKind {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 428) return "precondition";
  if (status === 429) return "rate_limited";
  if (status === 502) return "upstream";
  if (status === 400 || status === 422) return "validation";
  return "server";
}

function conflictKind(code: string): CommercialConflictKind | null {
  if (code === "VERSION_CONFLICT") return "version";
  if (code === "IDEMPOTENCY_CONFLICT") return "idempotency";
  if (code === "INVALID_TRANSITION") return "state";
  if (code === "SETTLEMENT_BLOCKED_BY_DISPUTE") return "settlement";
  if (code === "AMOUNT_CONFIRMATION_MISMATCH") return "amount";
  return code.endsWith("_CONFLICT") || code.startsWith("SETTLEMENT_") ? "unknown" : null;
}

async function responsePayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function errorDescriptor(payload: unknown): {
  readonly code: string;
  readonly message: string;
  readonly details: unknown;
  readonly requestId: string | null;
  readonly retryable: boolean | null;
} {
  if (!isRecord(payload)) {
    return {
      code: "REQUEST_FAILED",
      message: "商单请求失败",
      details: payload,
      requestId: null,
      retryable: null,
    };
  }

  const meta = isRecord(payload.meta) ? payload.meta : {};
  const standard = isRecord(payload.error) ? payload.error : null;
  const detail = isRecord(payload.detail) ? payload.detail : null;
  const source = standard ?? detail;
  const code = source && typeof source.code === "string"
    ? source.code
    : Array.isArray(payload.detail)
      ? "VALIDATION_ERROR"
      : "REQUEST_FAILED";
  const message = source && typeof source.message === "string"
    ? source.message
    : typeof payload.detail === "string"
      ? payload.detail
      : code;
  return {
    code,
    message,
    details: source && "details" in source ? source.details : payload.detail ?? payload,
    requestId: typeof meta.requestId === "string" ? meta.requestId : null,
    retryable: source && typeof source.retryable === "boolean" ? source.retryable : null,
  };
}

function httpError(response: Response, payload: unknown): CommercialApiError {
  const descriptor = errorDescriptor(payload);
  const conflict = response.status === 409 ? conflictKind(descriptor.code) ?? "unknown" : null;
  const requiresRefresh = descriptor.code === "VERSION_CONFLICT";
  const retryable = descriptor.retryable
    ?? (requiresRefresh || response.status === 429 || response.status >= 500);
  return new CommercialApiError({
    message: descriptor.message,
    kind: statusKind(response.status),
    status: response.status,
    code: descriptor.code,
    requestId: descriptor.requestId ?? response.headers.get("x-request-id"),
    retryable,
    requiresRefresh,
    conflict,
    details: descriptor.details,
  });
}

function networkError(cause: unknown): CommercialApiError {
  return new CommercialApiError({
    message: "商单网络请求失败",
    kind: "network",
    status: null,
    code: "NETWORK_ERROR",
    requestId: null,
    retryable: true,
    requiresRefresh: false,
    conflict: null,
    details: cause,
  });
}

function encodePath(value: string, name: string): string {
  return encodeURIComponent(requiredOption(value, name));
}

export function createCommercialClient(options: CommercialClientOptions): CommercialClient {
  const baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/+$/, "");
  const accessToken = requiredOption(options.accessToken, "accessToken");
  const workspaceId = requiredOption(options.workspaceId, "workspaceId");
  const fetcher = options.fetcher ?? fetch;
  const orderRoot = `${baseUrl}/${options.surface === "admin" ? "admin/" : ""}commercial-orders`;

  function commonHeaders(): Headers {
    return new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${accessToken}`,
      "X-Workspace-Id": workspaceId,
    });
  }

  async function send(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
  ): Promise<{ readonly response: Response; readonly payload: unknown }> {
    try {
      const response = await fetcher(`${orderRoot}${path}`, {
        credentials: "include",
        ...init,
        signal,
      });
      const payload = await responsePayload(response);
      if (!response.ok) throw httpError(response, payload);
      return { response, payload };
    } catch (cause) {
      if (cause instanceof CommercialApiError) throw cause;
      throw networkError(cause);
    }
  }

  async function readVersioned(path: string, signal?: AbortSignal): Promise<VersionedCommercialOrder> {
    const { response, payload } = await send(path, {
      method: "GET",
      headers: commonHeaders(),
    }, signal);
    return parseVersionedCommercialOrder(payload, response.headers.get("etag"));
  }

  async function command(
    path: string,
    body: unknown,
    optionsForCommand: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder> {
    const headers = commonHeaders();
    headers.set("Idempotency-Key", requiredOption(optionsForCommand.idempotencyKey, "idempotencyKey"));
    headers.set("If-Match", optionsForCommand.ifMatch);
    if (optionsForCommand.requestId?.trim()) {
      headers.set("X-Request-ID", optionsForCommand.requestId.trim());
    }
    const init: RequestInit = { method: "POST", headers };
    if (body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(body);
    }
    const { response, payload } = await send(path, init, optionsForCommand.signal);
    return parseVersionedCommercialOrder(payload, response.headers.get("etag"));
  }

  function orderPath(orderId: string): string {
    return `/${encodePath(orderId, "orderId")}`;
  }

  return {
    async listOrders(query: CommercialOrderListQuery = {}, signal?: AbortSignal) {
      const search = new URLSearchParams();
      if (query.ownerWorkspaceId?.trim()) search.set("owner_workspace_id", query.ownerWorkspaceId.trim());
      if (query.offset !== undefined) search.set("offset", String(query.offset));
      if (query.limit !== undefined) search.set("limit", String(query.limit));
      const suffix = search.size > 0 ? `?${search.toString()}` : "";
      const { payload } = await send(suffix, {
        method: "GET",
        headers: commonHeaders(),
      }, signal);
      return parseCommercialOrderPage(payload);
    },

    getOrder(orderId, signal) {
      return readVersioned(orderPath(orderId), signal);
    },

    async listMilestones(orderId, signal) {
      const { payload } = await send(`${orderPath(orderId)}/milestones`, {
        method: "GET",
        headers: commonHeaders(),
      }, signal);
      return parseCommercialMilestonePage(payload);
    },

    submitQuote(orderId, input: SubmitQuoteInput, commandOptions) {
      return command(`${orderPath(orderId)}/quotes`, input, commandOptions);
    },

    acceptQuote(orderId, quoteId, commandOptions) {
      return command(
        `${orderPath(orderId)}/quotes/${encodePath(quoteId, "quoteId")}/accept`,
        undefined,
        commandOptions,
      );
    },

    recordContract(orderId, input: RecordContractInput, commandOptions) {
      return command(`${orderPath(orderId)}/contracts`, input, commandOptions);
    },

    submitDelivery(orderId, milestoneId, input: SubmitDeliveryInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/milestones/${encodePath(milestoneId, "milestoneId")}/deliveries`,
        input,
        commandOptions,
      );
    },

    returnDelivery(orderId, deliveryId, input: ReturnDeliveryInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/deliveries/${encodePath(deliveryId, "deliveryId")}/return`,
        input,
        commandOptions,
      );
    },

    acceptDelivery(orderId, deliveryId, input: AcceptDeliveryInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/deliveries/${encodePath(deliveryId, "deliveryId")}/accept`,
        input,
        commandOptions,
      );
    },

    openDispute(orderId, milestoneId, input: OpenDisputeInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/milestones/${encodePath(milestoneId, "milestoneId")}/disputes`,
        input,
        commandOptions,
      );
    },

    resolveDispute(orderId, disputeId, input: ResolveDisputeInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/disputes/${encodePath(disputeId, "disputeId")}/resolve`,
        input,
        commandOptions,
      );
    },

    freezeSettlement(orderId, settlementId, input: SettlementConfirmationInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/settlements/${encodePath(settlementId, "settlementId")}/freeze`,
        input,
        commandOptions,
      );
    },

    resumeSettlement(orderId, settlementId, input: SettlementConfirmationInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/settlements/${encodePath(settlementId, "settlementId")}/resume`,
        input,
        commandOptions,
      );
    },

    paySettlement(orderId, settlementId, input: SettlementConfirmationInput, commandOptions) {
      return command(
        `${orderPath(orderId)}/settlements/${encodePath(settlementId, "settlementId")}/pay`,
        input,
        commandOptions,
      );
    },
  };
}
