declare const commercialEtagBrand: unique symbol;

export type CommercialEtag = string & { readonly [commercialEtagBrand]: true };

export type CommercialOrderStatus =
  | "published"
  | "awarded"
  | "contracted"
  | "in_delivery"
  | "accepted"
  | "settled";
export type CommercialQuoteStatus = "submitted" | "accepted" | "declined";
export type CommercialMilestoneStatus =
  | "pending"
  | "delivered"
  | "changes_requested"
  | "accepted"
  | "settled";
export type CommercialDeliveryStatus = "submitted" | "returned" | "accepted";
export type CommercialAcceptanceDecision = "accepted" | "changes_requested";
export type CommercialSettlementStatus = "ready" | "frozen" | "paid";
export type CommercialDisputeStatus = "open" | "resolved";

export interface CommercialMilestone {
  readonly id: string;
  readonly title: string;
  readonly amount_minor: number;
  readonly acceptance_criteria: string;
  readonly due_at: string | null;
  readonly status: CommercialMilestoneStatus;
  readonly version: number;
}

export interface CommercialQuote {
  readonly id: string;
  readonly contractor_workspace_id: string;
  readonly submitted_by: string;
  readonly amount_minor: number;
  readonly currency: string;
  readonly proposal: string;
  readonly valid_until: string;
  readonly status: CommercialQuoteStatus;
  readonly version: number;
  readonly created_at: string;
}

export interface CommercialContractVersion {
  readonly id: string;
  readonly sequence: number;
  readonly quote_id: string;
  readonly content_ref: string;
  readonly content_digest: string;
  readonly amount_minor: number;
  readonly currency: string;
  readonly created_by: string;
  readonly created_at: string;
}

export interface CommercialDelivery {
  readonly id: string;
  readonly milestone_id: string;
  readonly revision: number;
  readonly contract_version_id: string;
  readonly artifact_version_id: string;
  readonly artifact_digest: string;
  readonly note: string | null;
  readonly submitted_by: string;
  readonly status: CommercialDeliveryStatus;
  readonly version: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CommercialAcceptanceRecord {
  readonly id: string;
  readonly milestone_id: string;
  readonly delivery_id: string;
  readonly delivery_revision: number;
  readonly decision: CommercialAcceptanceDecision;
  readonly reason: string | null;
  readonly evidence_ref: string | null;
  readonly decided_by: string;
  readonly created_at: string;
}

export interface CommercialSettlement {
  readonly id: string;
  readonly milestone_id: string;
  readonly payee_workspace_id: string;
  readonly amount_minor: number;
  readonly currency: string;
  readonly status: CommercialSettlementStatus;
  readonly version: number;
  readonly accounting_operation_id: string | null;
  readonly accounting_receipt_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CommercialDispute {
  readonly id: string;
  readonly milestone_id: string;
  readonly kind: string;
  readonly reason: string;
  readonly opened_by: string;
  readonly opened_by_workspace_id: string;
  readonly status: CommercialDisputeStatus;
  readonly resolution: string | null;
  readonly resolved_by: string | null;
  readonly version: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CommercialOrder {
  readonly id: string;
  readonly owner_workspace_id: string;
  readonly title: string;
  readonly requirements: string;
  readonly budget_minor: number;
  readonly currency: string;
  readonly milestones: readonly CommercialMilestone[];
  readonly status: CommercialOrderStatus;
  readonly version: number;
  readonly created_at: string;
  readonly updated_at: string;
  readonly contractor_workspace_id: string | null;
  readonly accepted_quote_id: string | null;
  readonly active_contract_version_id: string | null;
  readonly quotes: readonly CommercialQuote[];
  readonly contract_versions: readonly CommercialContractVersion[];
  readonly deliveries: readonly CommercialDelivery[];
  readonly acceptance_records: readonly CommercialAcceptanceRecord[];
  readonly settlements: readonly CommercialSettlement[];
  readonly disputes: readonly CommercialDispute[];
}

export interface CommercialOrderPage {
  readonly items: readonly CommercialOrder[];
  readonly total: number;
  readonly offset: number;
  readonly limit: number;
}

export interface CommercialMilestonePage {
  readonly items: readonly CommercialMilestone[];
  readonly total: number;
}

export interface VersionedCommercialOrder {
  readonly order: CommercialOrder;
  readonly etag: CommercialEtag;
  readonly version: number;
}

export interface CommercialOrderListQuery {
  readonly ownerWorkspaceId?: string;
  readonly offset?: number;
  readonly limit?: number;
}

export interface CommercialCommandOptions {
  readonly ifMatch: CommercialEtag;
  readonly idempotencyKey: string;
  readonly requestId?: string;
  readonly signal?: AbortSignal;
}

export interface SubmitQuoteInput {
  readonly amount_minor: number;
  readonly currency: string;
  readonly proposal: string;
  readonly valid_until: string;
}

export interface RecordContractInput {
  readonly content_ref: string;
  readonly content_digest: string;
  readonly amount_minor: number;
}

export interface SubmitDeliveryInput {
  readonly artifact_version_id: string;
  readonly artifact_digest: string;
  readonly note?: string | null;
}

export interface ReturnDeliveryInput {
  readonly reason: string;
}

export interface AcceptDeliveryInput {
  readonly evidence_ref: string;
}

export interface OpenDisputeInput {
  readonly kind: string;
  readonly reason: string;
}

export interface ResolveDisputeInput {
  readonly resolution: string;
}

export interface SettlementConfirmationInput {
  readonly amount_minor: number;
  readonly currency: string;
}

export interface CommercialClientOptions {
  readonly baseUrl?: string;
  readonly accessToken: string;
  readonly workspaceId: string;
  /** Admin pages use the separately-authorized platform surface. */
  readonly surface?: "creator" | "admin";
  readonly fetcher?: typeof fetch;
}

export interface CommercialCreateOptions {
  readonly idempotencyKey: string;
  readonly requestId?: string;
  readonly signal?: AbortSignal;
}

export interface PublishOrderInput {
  readonly owner_workspace_id?: string | null;
  readonly title: string;
  readonly requirements: string;
  readonly budget_minor: number;
  readonly currency: string;
  readonly milestones: readonly {
    readonly title: string;
    readonly amount_minor: number;
    readonly acceptance_criteria: string;
    readonly due_at?: string | null;
  }[];
}

export interface CommercialClient {
  publishOrder(
    input: PublishOrderInput,
    options: CommercialCreateOptions,
  ): Promise<VersionedCommercialOrder>;
  listOrders(query?: CommercialOrderListQuery, signal?: AbortSignal): Promise<CommercialOrderPage>;
  getOrder(orderId: string, signal?: AbortSignal): Promise<VersionedCommercialOrder>;
  listMilestones(orderId: string, signal?: AbortSignal): Promise<CommercialMilestonePage>;
  submitQuote(
    orderId: string,
    input: SubmitQuoteInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  acceptQuote(
    orderId: string,
    quoteId: string,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  recordContract(
    orderId: string,
    input: RecordContractInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  submitDelivery(
    orderId: string,
    milestoneId: string,
    input: SubmitDeliveryInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  returnDelivery(
    orderId: string,
    deliveryId: string,
    input: ReturnDeliveryInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  acceptDelivery(
    orderId: string,
    deliveryId: string,
    input: AcceptDeliveryInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  openDispute(
    orderId: string,
    milestoneId: string,
    input: OpenDisputeInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  resolveDispute(
    orderId: string,
    disputeId: string,
    input: ResolveDisputeInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  freezeSettlement(
    orderId: string,
    settlementId: string,
    input: SettlementConfirmationInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  resumeSettlement(
    orderId: string,
    settlementId: string,
    input: SettlementConfirmationInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
  paySettlement(
    orderId: string,
    settlementId: string,
    input: SettlementConfirmationInput,
    command: CommercialCommandOptions,
  ): Promise<VersionedCommercialOrder>;
}
