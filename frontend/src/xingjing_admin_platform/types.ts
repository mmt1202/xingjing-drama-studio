export type AdminDomain =
  | "business"
  | "finance"
  | "models"
  | "review"
  | "security"
  | "operations"
  | "operationsConfig"
  | "notifications"
  | "support"
  | "api";

export interface AdminContext {
  actor: { id: string; displayName: string; role: string };
  workspace: { id: string; name: string };
  project?: { id: string; name: string };
  permissions: string[];
  dataScope: string;
}

export interface AdminRecord {
  id: string;
  name: string;
  status: string;
  version: number;
  updatedAt: string;
  details?: Record<string, unknown>;
  sensitive?: Record<string, string>;
  maskedSecret?: string;
  scopes?: string[];
  webhooks?: Array<{ id: string; url: string; status: string; version: number }>;
  [key: string]: unknown;
}

export interface PageResult<T> { items: T[]; page: number; pageSize: number; total: number }
export interface AdminAction {
  action: string;
  objectId: string;
  version: number;
  resource?: string;
  reason?: string;
  payload?: Record<string, unknown>;
}
export interface AdminActionResult { requestId: string; status: "processing" | "succeeded" | "failed"; objectId?: string; oneTimeSecret?: string; oneTimeSecretKind?: "api_key" | "webhook_signing_secret" }

export interface AdminApi {
  getContext(): Promise<AdminContext>;
  list(domain: AdminDomain, query: { page: number; pageSize: number; resource?: string; projectId?: string; search?: string; status?: string; targetTenantId?: string; targetWorkspaceId?: string; approvalId?: string; accessReason?: string }): Promise<PageResult<AdminRecord>>;
  act(domain: AdminDomain, action: AdminAction, options?: { retryNetworkOnce?: boolean }): Promise<AdminActionResult>;
}

export interface AdminRouteDefinition {
  id: string;
  path: string;
  title: string;
  domain: AdminDomain;
  resource: string;
  viewPermission: string;
  managePermission: string;
  emptyLabel: string;
  primaryAction: string;
}
