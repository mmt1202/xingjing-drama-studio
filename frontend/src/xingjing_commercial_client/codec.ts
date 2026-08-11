import { CommercialApiError } from "./errors";
import type {
  CommercialAcceptanceDecision,
  CommercialAcceptanceRecord,
  CommercialContractVersion,
  CommercialDelivery,
  CommercialDeliveryStatus,
  CommercialDispute,
  CommercialDisputeStatus,
  CommercialEtag,
  CommercialMilestone,
  CommercialMilestonePage,
  CommercialMilestoneStatus,
  CommercialOrder,
  CommercialOrderPage,
  CommercialOrderStatus,
  CommercialQuote,
  CommercialQuoteStatus,
  CommercialSettlement,
  CommercialSettlementStatus,
  VersionedCommercialOrder,
} from "./types";

const etagPattern = /^(?:W\/)?"([1-9][0-9]*)"$/;

function contractError(code: string, details: unknown): CommercialApiError {
  return new CommercialApiError({
    message: "商单服务响应格式无效",
    kind: "contract",
    status: null,
    code,
    requestId: null,
    retryable: false,
    requiresRefresh: false,
    conflict: null,
    details,
  });
}

function record(value: unknown, path: string): Readonly<Record<string, unknown>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw contractError("INVALID_RESPONSE", { path, value });
  }
  return value as Readonly<Record<string, unknown>>;
}

function stringValue(value: unknown, path: string): string {
  if (typeof value !== "string") throw contractError("INVALID_RESPONSE", { path, value });
  return value;
}

function nullableString(value: unknown, path: string): string | null {
  if (value === null) return null;
  return stringValue(value, path);
}

function integer(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw contractError("INVALID_RESPONSE", { path, value });
  }
  return value;
}

function array<Value>(
  value: unknown,
  path: string,
  parseItem: (item: unknown, itemPath: string) => Value,
): readonly Value[] {
  if (!Array.isArray(value)) throw contractError("INVALID_RESPONSE", { path, value });
  return value.map((item, index) => parseItem(item, `${path}[${index}]`));
}

function enumValue<Value extends string>(
  value: unknown,
  path: string,
  allowed: readonly Value[],
): Value {
  const parsed = stringValue(value, path);
  if (!allowed.includes(parsed as Value)) {
    throw contractError("INVALID_RESPONSE", { path, value });
  }
  return parsed as Value;
}

function parseMilestone(value: unknown, path: string): CommercialMilestone {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    title: stringValue(item.title, `${path}.title`),
    amount_minor: integer(item.amount_minor, `${path}.amount_minor`),
    acceptance_criteria: stringValue(item.acceptance_criteria, `${path}.acceptance_criteria`),
    due_at: nullableString(item.due_at, `${path}.due_at`),
    status: enumValue<CommercialMilestoneStatus>(
      item.status,
      `${path}.status`,
      ["pending", "delivered", "changes_requested", "accepted", "settled"],
    ),
    version: integer(item.version, `${path}.version`),
  };
}

function parseQuote(value: unknown, path: string): CommercialQuote {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    contractor_workspace_id: stringValue(item.contractor_workspace_id, `${path}.contractor_workspace_id`),
    submitted_by: stringValue(item.submitted_by, `${path}.submitted_by`),
    amount_minor: integer(item.amount_minor, `${path}.amount_minor`),
    currency: stringValue(item.currency, `${path}.currency`),
    proposal: stringValue(item.proposal, `${path}.proposal`),
    valid_until: stringValue(item.valid_until, `${path}.valid_until`),
    status: enumValue<CommercialQuoteStatus>(
      item.status,
      `${path}.status`,
      ["submitted", "accepted", "declined"],
    ),
    version: integer(item.version, `${path}.version`),
    created_at: stringValue(item.created_at, `${path}.created_at`),
  };
}

function parseContractVersion(value: unknown, path: string): CommercialContractVersion {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    sequence: integer(item.sequence, `${path}.sequence`),
    quote_id: stringValue(item.quote_id, `${path}.quote_id`),
    content_ref: stringValue(item.content_ref, `${path}.content_ref`),
    content_digest: stringValue(item.content_digest, `${path}.content_digest`),
    amount_minor: integer(item.amount_minor, `${path}.amount_minor`),
    currency: stringValue(item.currency, `${path}.currency`),
    created_by: stringValue(item.created_by, `${path}.created_by`),
    created_at: stringValue(item.created_at, `${path}.created_at`),
  };
}

function parseDelivery(value: unknown, path: string): CommercialDelivery {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    milestone_id: stringValue(item.milestone_id, `${path}.milestone_id`),
    revision: integer(item.revision, `${path}.revision`),
    contract_version_id: stringValue(item.contract_version_id, `${path}.contract_version_id`),
    artifact_version_id: stringValue(item.artifact_version_id, `${path}.artifact_version_id`),
    artifact_digest: stringValue(item.artifact_digest, `${path}.artifact_digest`),
    note: nullableString(item.note, `${path}.note`),
    submitted_by: stringValue(item.submitted_by, `${path}.submitted_by`),
    status: enumValue<CommercialDeliveryStatus>(
      item.status,
      `${path}.status`,
      ["submitted", "returned", "accepted"],
    ),
    version: integer(item.version, `${path}.version`),
    created_at: stringValue(item.created_at, `${path}.created_at`),
    updated_at: stringValue(item.updated_at, `${path}.updated_at`),
  };
}

function parseAcceptanceRecord(value: unknown, path: string): CommercialAcceptanceRecord {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    milestone_id: stringValue(item.milestone_id, `${path}.milestone_id`),
    delivery_id: stringValue(item.delivery_id, `${path}.delivery_id`),
    delivery_revision: integer(item.delivery_revision, `${path}.delivery_revision`),
    decision: enumValue<CommercialAcceptanceDecision>(
      item.decision,
      `${path}.decision`,
      ["accepted", "changes_requested"],
    ),
    reason: nullableString(item.reason, `${path}.reason`),
    evidence_ref: nullableString(item.evidence_ref, `${path}.evidence_ref`),
    decided_by: stringValue(item.decided_by, `${path}.decided_by`),
    created_at: stringValue(item.created_at, `${path}.created_at`),
  };
}

function parseSettlement(value: unknown, path: string): CommercialSettlement {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    milestone_id: stringValue(item.milestone_id, `${path}.milestone_id`),
    payee_workspace_id: stringValue(item.payee_workspace_id, `${path}.payee_workspace_id`),
    amount_minor: integer(item.amount_minor, `${path}.amount_minor`),
    currency: stringValue(item.currency, `${path}.currency`),
    status: enumValue<CommercialSettlementStatus>(
      item.status,
      `${path}.status`,
      ["ready", "frozen", "paid"],
    ),
    version: integer(item.version, `${path}.version`),
    accounting_operation_id: nullableString(item.accounting_operation_id, `${path}.accounting_operation_id`),
    accounting_receipt_id: nullableString(item.accounting_receipt_id, `${path}.accounting_receipt_id`),
    created_at: stringValue(item.created_at, `${path}.created_at`),
    updated_at: stringValue(item.updated_at, `${path}.updated_at`),
  };
}

function parseDispute(value: unknown, path: string): CommercialDispute {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    milestone_id: stringValue(item.milestone_id, `${path}.milestone_id`),
    kind: stringValue(item.kind, `${path}.kind`),
    reason: stringValue(item.reason, `${path}.reason`),
    opened_by: stringValue(item.opened_by, `${path}.opened_by`),
    opened_by_workspace_id: stringValue(item.opened_by_workspace_id, `${path}.opened_by_workspace_id`),
    status: enumValue<CommercialDisputeStatus>(
      item.status,
      `${path}.status`,
      ["open", "resolved"],
    ),
    resolution: nullableString(item.resolution, `${path}.resolution`),
    resolved_by: nullableString(item.resolved_by, `${path}.resolved_by`),
    version: integer(item.version, `${path}.version`),
    created_at: stringValue(item.created_at, `${path}.created_at`),
    updated_at: stringValue(item.updated_at, `${path}.updated_at`),
  };
}

export function parseCommercialOrder(value: unknown, path = "order"): CommercialOrder {
  const item = record(value, path);
  return {
    id: stringValue(item.id, `${path}.id`),
    owner_workspace_id: stringValue(item.owner_workspace_id, `${path}.owner_workspace_id`),
    title: stringValue(item.title, `${path}.title`),
    requirements: stringValue(item.requirements, `${path}.requirements`),
    budget_minor: integer(item.budget_minor, `${path}.budget_minor`),
    currency: stringValue(item.currency, `${path}.currency`),
    milestones: array(item.milestones, `${path}.milestones`, parseMilestone),
    status: enumValue<CommercialOrderStatus>(
      item.status,
      `${path}.status`,
      ["published", "awarded", "contracted", "in_delivery", "accepted", "settled"],
    ),
    version: integer(item.version, `${path}.version`),
    created_at: stringValue(item.created_at, `${path}.created_at`),
    updated_at: stringValue(item.updated_at, `${path}.updated_at`),
    contractor_workspace_id: nullableString(item.contractor_workspace_id, `${path}.contractor_workspace_id`),
    accepted_quote_id: nullableString(item.accepted_quote_id, `${path}.accepted_quote_id`),
    active_contract_version_id: nullableString(
      item.active_contract_version_id,
      `${path}.active_contract_version_id`,
    ),
    quotes: array(item.quotes, `${path}.quotes`, parseQuote),
    contract_versions: array(item.contract_versions, `${path}.contract_versions`, parseContractVersion),
    deliveries: array(item.deliveries, `${path}.deliveries`, parseDelivery),
    acceptance_records: array(
      item.acceptance_records,
      `${path}.acceptance_records`,
      parseAcceptanceRecord,
    ),
    settlements: array(item.settlements, `${path}.settlements`, parseSettlement),
    disputes: array(item.disputes, `${path}.disputes`, parseDispute),
  };
}

export function parseCommercialOrderPage(value: unknown): CommercialOrderPage {
  const page = record(value, "page");
  return {
    items: array(page.items, "page.items", parseCommercialOrder),
    total: integer(page.total, "page.total"),
    offset: integer(page.offset, "page.offset"),
    limit: integer(page.limit, "page.limit"),
  };
}

export function parseCommercialMilestonePage(value: unknown): CommercialMilestonePage {
  const page = record(value, "milestones");
  return {
    items: array(page.items, "milestones.items", parseMilestone),
    total: integer(page.total, "milestones.total"),
  };
}

export function commercialEtagFromVersion(version: number): CommercialEtag {
  if (!Number.isInteger(version) || version < 1) {
    throw new RangeError("commercial order version must be a positive integer");
  }
  return `"${version}"` as CommercialEtag;
}

export function parseCommercialEtag(value: string | null): {
  readonly etag: CommercialEtag;
  readonly version: number;
} {
  if (value === null) throw contractError("MISSING_ETAG", null);
  const match = etagPattern.exec(value.trim());
  if (!match) throw contractError("INVALID_ETAG", value);
  return {
    etag: value.trim() as CommercialEtag,
    version: Number(match[1]),
  };
}

export function parseVersionedCommercialOrder(
  value: unknown,
  etagValue: string | null,
): VersionedCommercialOrder {
  const order = parseCommercialOrder(value);
  const etag = parseCommercialEtag(etagValue);
  if (etag.version !== order.version) {
    throw contractError("ETAG_VERSION_MISMATCH", {
      etagVersion: etag.version,
      orderVersion: order.version,
    });
  }
  return { order, ...etag };
}
