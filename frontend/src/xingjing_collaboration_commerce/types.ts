export type CollaborationCommerceModule = "M01" | "M10" | "M11" | "M12" | "M13" | "M14" | "M16";
export type CommerceAudience = "internal" | "admin" | "client";
export type CommerceResponseKind = "list" | "object";
export type CommerceActionMethod = "POST" | "PUT";
export type CommerceActionBodyKind = "action" | "entity";
export type CommerceConfirmationKind = "standard" | "approval" | "financial" | "danger" | "none";
export type CommerceFieldType = "text" | "email" | "number" | "textarea" | "password" | "datetime-local" | "checkbox" | "file";
export type CommerceContextKey = "workspaceId" | "projectId" | "reviewToken";

export interface CommercePageContext {
  readonly workspaceId?: string;
  readonly projectId?: string;
  readonly reviewToken?: string;
}

export interface CommerceActionField {
  readonly name: string;
  readonly labelKey: string;
  readonly type: CommerceFieldType;
  readonly required?: boolean;
  readonly min?: number;
}

export interface CommerceActionDefinition {
  readonly id: string;
  readonly labelKey: string;
  readonly permission: string;
  readonly method: CommerceActionMethod;
  readonly endpoint: string;
  readonly bodyKind: CommerceActionBodyKind;
  readonly scope: "page" | "record";
  readonly confirmation: CommerceConfirmationKind;
  readonly fields: readonly CommerceActionField[];
  readonly clientSafe?: boolean;
}

export interface CommerceColumnDefinition {
  readonly key: string;
  readonly labelKey: string;
  readonly format?: "text" | "status" | "integer" | "minor" | "date" | "datetime" | "version";
}

export interface CollaborationCommerceRouteDefinition {
  readonly id: string;
  readonly module: CollaborationCommerceModule;
  readonly path: string;
  readonly titleKey: string;
  readonly descriptionKey: string;
  readonly emptyKey: string;
  readonly audience: CommerceAudience;
  readonly loadEndpoint: string;
  readonly exportEndpoint?: string;
  readonly relatedEndpoints: readonly string[];
  readonly responseKind: CommerceResponseKind;
  readonly requiredContext: readonly CommerceContextKey[];
  readonly viewPermission: string;
  readonly managePermission?: string;
  readonly columns: readonly CommerceColumnDefinition[];
  readonly actions: readonly CommerceActionDefinition[];
  readonly media?: boolean;
}

export interface SessionContext {
  readonly user: { readonly id: string; readonly displayName?: string; readonly [key: string]: unknown };
  readonly activeWorkspace?: { readonly id: string; readonly name?: string; readonly [key: string]: unknown } | null;
  readonly workspaces: readonly { readonly id: string; readonly name?: string; readonly [key: string]: unknown }[];
  readonly permissions: readonly string[];
  readonly featureFlags: Readonly<Record<string, unknown>>;
}

export interface CommerceRecord {
  readonly id?: string;
  readonly name?: string;
  readonly title?: string;
  readonly status?: string;
  readonly version?: number;
  readonly updatedAt?: string;
  readonly [key: string]: unknown;
}

export interface CommerceSummaryMetric {
  readonly key: string;
  readonly label: string;
  readonly value: string | number;
  readonly format?: "text" | "integer" | "minor" | "percent";
  readonly currency?: string;
}

export interface CommerceWorkflowNode {
  readonly id: string;
  readonly label: string;
  readonly status: string;
  readonly occurredAt?: string;
}

export interface CommerceRelatedSnapshot {
  readonly endpoint: string;
  readonly data: unknown;
}

export interface CommercePageSnapshot {
  readonly requestId: string;
  readonly serverTime?: string;
  readonly items: readonly CommerceRecord[];
  readonly detail?: CommerceRecord;
  readonly summary: readonly CommerceSummaryMetric[];
  readonly workflow: readonly CommerceWorkflowNode[];
  readonly related?: readonly CommerceRelatedSnapshot[];
  readonly nextPageToken: string | null;
}

export interface CommercePageQuery {
  readonly pageSize?: number;
  readonly pageToken?: string;
  readonly query?: string;
  readonly status?: string;
  readonly sort?: string;
}

export interface CommerceActionInput {
  readonly targetId?: string;
  readonly version?: number;
  readonly payload: Readonly<Record<string, unknown>>;
}

export interface CommerceActionFailure {
  readonly id: string;
  readonly code: string;
  readonly message: string;
}

export interface CommerceActionResult {
  readonly requestId: string;
  readonly status: "processing" | "succeeded" | "partial" | "failed";
  readonly failures: readonly CommerceActionFailure[];
  readonly retryableIds: readonly string[];
  readonly object?: CommerceRecord;
}

export interface CollaborationCommerceApi {
  getSessionContext(signal?: AbortSignal): Promise<SessionContext>;
  loadPage(
    route: CollaborationCommerceRouteDefinition,
    context: CommercePageContext,
    query?: CommercePageQuery,
    signal?: AbortSignal,
  ): Promise<CommercePageSnapshot>;
  downloadExport?(
    route: CollaborationCommerceRouteDefinition,
    context: CommercePageContext,
    query?: string,
    signal?: AbortSignal,
  ): Promise<{ readonly blob: Blob; readonly filename: string }>;
  executeAction(
    route: CollaborationCommerceRouteDefinition,
    action: CommerceActionDefinition,
    context: CommercePageContext,
    input: CommerceActionInput,
    options?: { readonly retryNetworkOnce?: boolean; readonly signal?: AbortSignal },
  ): Promise<CommerceActionResult>;
}
