import {
  commercialEtagFromVersion,
  type CommercialClient,
  type CommercialCommandOptions,
  type CommercialOrder,
} from "@/xingjing_commercial_client";

import type {
  CollaborationCommerceApi,
  CollaborationCommerceRouteDefinition,
  CommerceActionDefinition,
  CommerceActionInput,
  CommerceActionResult,
  CommercePageContext,
  CommercePageQuery,
  CommercePageSnapshot,
  SessionContext,
} from "./types";

export interface CommercialM14PageApiOptions {
  readonly client: CommercialClient;
  readonly session: SessionContext;
  readonly createId?: () => string;
}

function createId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function asText(value: unknown, name: string, required = true): string {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (!required && (value === undefined || value === null || value === "")) return "";
  throw new TypeError(`${name} is required`);
}

function asAmount(value: unknown): number {
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) return value;
  throw new TypeError("amount_minor must be a non-negative integer");
}

function record(order: CommercialOrder): Record<string, unknown> {
  const settlement = order.settlements.at(-1);
  return {
    ...order,
    client: order.owner_workspace_id,
    contractor: order.contractor_workspace_id ?? "—",
    amountMinor: order.budget_minor,
    milestone: order.milestones.length,
    settlementStatus: settlement?.status ?? "—",
    updatedAt: order.updated_at,
  };
}

function snapshot(orders: readonly CommercialOrder[], total: number, offset: number, limit: number): CommercePageSnapshot {
  const items = orders.map(record);
  const detail = items[0];
  return {
    requestId: "",
    items,
    detail,
    summary: [
      { key: "total", label: "商单总数", value: total, format: "integer" },
      { key: "settlement", label: "结算金额", value: orders.reduce((sum, order) => sum + order.settlements.reduce((subtotal, value) => subtotal + value.amount_minor, 0), 0), format: "minor" },
    ],
    workflow: orders[0]?.milestones.map((milestone) => ({ id: milestone.id, label: milestone.title, status: milestone.status })) ?? [],
    related: [],
    nextPageToken: offset + limit < total ? String(offset + limit) : null,
  };
}

function command(input: CommerceActionInput, idempotencyKey: string): CommercialCommandOptions {
  if (typeof input.version !== "number") throw new TypeError("M14 write actions require the current order version");
  return { ifMatch: commercialEtagFromVersion(input.version), idempotencyKey };
}

function target(input: CommerceActionInput): string {
  if (!input.targetId?.trim()) throw new TypeError("M14 write actions require an order id");
  return input.targetId;
}

/** Maps M14 page reads and commands onto the strict CommercialClient contract. */
export function createCommercialM14PageApi(options: CommercialM14PageApiOptions): CollaborationCommerceApi {
  const nextId = options.createId ?? createId;

  return {
    getSessionContext: () => Promise.resolve(options.session),

    async loadPage(route: CollaborationCommerceRouteDefinition, context: CommercePageContext, query: CommercePageQuery = {}, signal?: AbortSignal) {
      if (route.module !== "M14") throw new TypeError("Commercial M14 adapter only supports M14 routes");
      const offset = Number(query.pageToken ?? "0");
      const page = await options.client.listOrders({
        ownerWorkspaceId: context.workspaceId,
        offset: Number.isInteger(offset) && offset >= 0 ? offset : 0,
        limit: query.pageSize ?? 25,
      }, signal);
      const matching = query.query?.trim()
        ? page.items.filter((order) => order.title.includes(query.query!.trim()))
        : page.items;
      return snapshot(matching, page.total, page.offset, page.limit);
    },

    async executeAction(_route: CollaborationCommerceRouteDefinition, action: CommerceActionDefinition, _context: CommercePageContext, input: CommerceActionInput): Promise<CommerceActionResult> {
      const orderId = target(input);
      const optionsForCommand = command(input, nextId());
      const payload = input.payload;
      let result;
      switch (action.id) {
        case "submitQuote":
          result = await options.client.submitQuote(orderId, {
            amount_minor: asAmount(payload.amount_minor), currency: asText(payload.currency, "currency"),
            proposal: asText(payload.proposal, "proposal"), valid_until: asText(payload.valid_until, "valid_until"),
          }, optionsForCommand);
          break;
        case "acceptQuote":
          result = await options.client.acceptQuote(orderId, asText(payload.quoteId, "quoteId"), optionsForCommand);
          break;
        case "recordContract":
          result = await options.client.recordContract(orderId, {
            content_ref: asText(payload.content_ref, "content_ref"), content_digest: asText(payload.content_digest, "content_digest"), amount_minor: asAmount(payload.amount_minor),
          }, optionsForCommand);
          break;
        case "deliver":
        case "submitDelivery":
          result = await options.client.submitDelivery(orderId, asText(payload.milestoneId, "milestoneId"), {
            artifact_version_id: asText(payload.artifact_version_id, "artifact_version_id"), artifact_digest: asText(payload.artifact_digest, "artifact_digest"), note: asText(payload.note, "note", false) || null,
          }, optionsForCommand);
          break;
        case "rejectDelivery":
        case "returnDelivery":
          result = await options.client.returnDelivery(orderId, asText(payload.deliveryId, "deliveryId"), { reason: asText(payload.reason, "reason") }, optionsForCommand);
          break;
        case "acceptDelivery":
          result = await options.client.acceptDelivery(orderId, asText(payload.deliveryId, "deliveryId"), { evidence_ref: asText(payload.evidence_ref, "evidence_ref") }, optionsForCommand);
          break;
        case "openDispute":
          result = await options.client.openDispute(orderId, asText(payload.milestoneId, "milestoneId"), { kind: asText(payload.kind, "kind"), reason: asText(payload.reason, "reason") }, optionsForCommand);
          break;
        case "resolveDispute":
          result = await options.client.resolveDispute(orderId, asText(payload.disputeId, "disputeId"), { resolution: asText(payload.resolution, "resolution") }, optionsForCommand);
          break;
        case "freezeSettlement":
        case "resumeSettlement":
        case "paySettlement": {
          const settlementId = asText(payload.settlementId, "settlementId");
          const confirmation = { amount_minor: asAmount(payload.amount_minor), currency: asText(payload.currency, "currency") };
          result = action.id === "freezeSettlement"
            ? await options.client.freezeSettlement(orderId, settlementId, confirmation, optionsForCommand)
            : action.id === "resumeSettlement"
              ? await options.client.resumeSettlement(orderId, settlementId, confirmation, optionsForCommand)
              : await options.client.paySettlement(orderId, settlementId, confirmation, optionsForCommand);
          break;
        }
        default:
          throw new TypeError(`Unsupported M14 page action: ${action.id}`);
      }
      return { requestId: "", status: "succeeded", failures: [], retryableIds: [], object: record(result.order) };
    },
  };
}
