import type {
  CollaborationCommerceModule,
  CollaborationCommerceRouteDefinition,
  CommerceActionDefinition,
  CommerceActionField,
  CommerceActionMethod,
  CommerceColumnDefinition,
  CommerceConfirmationKind,
  CommerceContextKey,
  CommerceResponseKind,
} from "./types";

const field = (name: string, type: CommerceActionField["type"], required = true, min?: number): CommerceActionField => ({
  name,
  type,
  required,
  min,
  labelKey: `fields.${name}`,
});

function action(
  id: string,
  permission: string,
  endpoint: string,
  options: {
    method?: CommerceActionMethod;
    bodyKind?: CommerceActionDefinition["bodyKind"];
    scope?: CommerceActionDefinition["scope"];
    confirmation?: CommerceConfirmationKind;
    fields?: readonly CommerceActionField[];
    clientSafe?: boolean;
  } = {},
): CommerceActionDefinition {
  return {
    id,
    labelKey: `actions.${id}`,
    permission,
    endpoint,
    method: options.method ?? "POST",
    bodyKind: options.bodyKind ?? "action",
    scope: options.scope ?? "record",
    confirmation: options.confirmation ?? "standard",
    fields: options.fields ?? [],
    clientSafe: options.clientSafe,
  };
}

const memberColumns: readonly CommerceColumnDefinition[] = [
  { key: "email", labelKey: "columns.member" },
  { key: "roleId", labelKey: "columns.role" },
  { key: "memberId", labelKey: "columns.dataScope" },
  { key: "active", labelKey: "columns.status", format: "status" },
];
const memberPerformanceColumns: readonly CommerceColumnDefinition[] = [
  { key: "email", labelKey: "columns.member" },
  { key: "roleId", labelKey: "columns.role" },
  { key: "taskCount", labelKey: "columns.task", format: "integer" },
  { key: "succeededTaskCount", labelKey: "columns.completed", format: "integer" },
  { key: "failedTaskCount", labelKey: "columns.failure", format: "integer" },
  { key: "successRateMilli", labelKey: "columns.passRate", format: "integer" },
  { key: "reworkRateMilli", labelKey: "columns.reworkRate", format: "integer" },
  { key: "costAttributionStatus", labelKey: "columns.cost" },
  { key: "reviewEfficiencyStatus", labelKey: "columns.reviewEfficiency" },
];
const invitationColumns: readonly CommerceColumnDefinition[] = [
  { key: "email", labelKey: "columns.member" },
  { key: "roleId", labelKey: "columns.role" },
  { key: "state", labelKey: "columns.status", format: "status" },
  { key: "expiresAt", labelKey: "columns.expiresAt", format: "datetime" },
  { key: "version", labelKey: "columns.updatedAt", format: "version" },
];
const roleColumns: readonly CommerceColumnDefinition[] = [
  { key: "name", labelKey: "columns.role" },
  { key: "permissions", labelKey: "columns.dataScope" },
  { key: "version", labelKey: "columns.updatedAt", format: "version" },
];
const auditColumns: readonly CommerceColumnDefinition[] = [
  { key: "actorId", labelKey: "columns.member" },
  { key: "action", labelKey: "columns.type" },
  { key: "objectId", labelKey: "columns.dataScope" },
  { key: "result", labelKey: "columns.status", format: "status" },
  { key: "occurredAt", labelKey: "columns.occurredAt", format: "datetime" },
];
const overviewColumns: readonly CommerceColumnDefinition[] = [
  { key: "workspaceId", labelKey: "columns.dataScope" },
  { key: "memberCount", labelKey: "columns.member", format: "integer" },
  { key: "occupiedSeats", labelKey: "columns.seat", format: "integer" },
  { key: "availableSeats", labelKey: "columns.status", format: "integer" },
  { key: "permissionVersion", labelKey: "columns.updatedAt", format: "version" },
  { key: "projectCount", labelKey: "columns.project", format: "integer" },
  { key: "taskCount", labelKey: "columns.task", format: "integer" },
  { key: "runningTaskCount", labelKey: "columns.processing", format: "integer" },
  { key: "failedTaskCount", labelKey: "columns.failure", format: "integer" },
  { key: "planId", labelKey: "columns.plan" },
  { key: "planStatus", labelKey: "columns.status", format: "status" },
  { key: "quotaRemaining", labelKey: "columns.compute" },
  { key: "updatedAt", labelKey: "columns.updatedAt", format: "datetime" },
];
const projectMemberColumns: readonly CommerceColumnDefinition[] = [
  { key: "email", labelKey: "columns.member" },
  { key: "workspaceRole", labelKey: "columns.workspaceRole" },
  { key: "productionRole", labelKey: "columns.productionRole" },
  { key: "dataScope", labelKey: "columns.dataScope" },
  { key: "permissions", labelKey: "columns.permissions" },
  { key: "active", labelKey: "columns.status", format: "status" },
  { key: "version", labelKey: "columns.updatedAt", format: "version" },
];
const enterpriseColumns: readonly CommerceColumnDefinition[] = [
  { key: "legalName", labelKey: "fields.legalName" },
  { key: "invoiceTitle", labelKey: "fields.invoiceTitle" },
  { key: "dataRetentionDays", labelKey: "fields.dataRetentionDays", format: "integer" },
  { key: "version", labelKey: "columns.updatedAt", format: "version" },
  { key: "updatedAt", labelKey: "columns.occurredAt", format: "datetime" },
];
const financeColumns: readonly CommerceColumnDefinition[] = [
  { key: "businessNumber", labelKey: "columns.businessNumber" },
  { key: "amountMinor", labelKey: "columns.amount", format: "minor" },
  { key: "status", labelKey: "columns.status", format: "status" },
  { key: "taskId", labelKey: "columns.relatedTask" },
  { key: "occurredAt", labelKey: "columns.occurredAt", format: "datetime" },
];
const invoiceColumns: readonly CommerceColumnDefinition[] = [
  { key: "businessNumber", labelKey: "columns.businessNumber" },
  { key: "invoiceTitle", labelKey: "fields.invoiceTitle" },
  { key: "amountMinor", labelKey: "columns.amount", format: "minor" },
  { key: "status", labelKey: "columns.status", format: "status" },
  { key: "occurredAt", labelKey: "columns.occurredAt", format: "datetime" },
];
const costColumns: readonly CommerceColumnDefinition[] = [
  { key: "projectId", labelKey: "columns.dataScope" },
  { key: "episodeId", labelKey: "columns.episode" },
  { key: "shotId", labelKey: "columns.shot" },
  { key: "source", labelKey: "columns.type" },
  { key: "modelId", labelKey: "columns.model" },
  { key: "actorId", labelKey: "columns.member" },
  { key: "estimatedMinor", labelKey: "columns.price", format: "minor" },
  { key: "actualMinor", labelKey: "columns.amount", format: "minor" },
  { key: "releasedMinor", labelKey: "columns.status", format: "minor" },
  { key: "taskCount", labelKey: "columns.relatedTask", format: "integer" },
];
const planColumns: readonly CommerceColumnDefinition[] = [
  { key: "planId", labelKey: "columns.type" },
  { key: "status", labelKey: "columns.status", format: "status" },
  { key: "seatLimit", labelKey: "columns.seat", format: "integer" },
  { key: "version", labelKey: "columns.version", format: "version" },
  { key: "updatedAt", labelKey: "columns.updatedAt", format: "datetime" },
];
const reviewColumns: readonly CommerceColumnDefinition[] = [
  { key: "title", labelKey: "columns.reviewObject" },
  { key: "visibleVersion", labelKey: "columns.visibleVersion", format: "version" },
  { key: "expiresAt", labelKey: "columns.expiresAt", format: "datetime" },
  { key: "accessPolicy", labelKey: "columns.accessPolicy" },
  { key: "approvalStatus", labelKey: "columns.approvalStatus", format: "status" },
  { key: "updatedAt", labelKey: "columns.updatedAt", format: "datetime" },
];
const templateColumns: readonly CommerceColumnDefinition[] = [
  { key: "name", labelKey: "columns.template" },
  { key: "type", labelKey: "columns.type" },
  { key: "version", labelKey: "columns.version", format: "version" },
  { key: "scope", labelKey: "columns.scope" },
  { key: "priceMinor", labelKey: "columns.price", format: "minor" },
  { key: "reviewStatus", labelKey: "columns.reviewStatus", format: "status" },
];
const commercialColumns: readonly CommerceColumnDefinition[] = [
  { key: "title", labelKey: "columns.commercialOrder" },
  { key: "client", labelKey: "columns.client" },
  { key: "contractor", labelKey: "columns.contractor" },
  { key: "amountMinor", labelKey: "columns.amount", format: "minor" },
  { key: "milestone", labelKey: "columns.milestone" },
  { key: "settlementStatus", labelKey: "columns.settlementStatus", format: "status" },
];

interface RouteSeed {
  readonly id: string;
  readonly module: CollaborationCommerceModule;
  readonly path: string;
  readonly loadEndpoint: string;
  readonly exportEndpoint?: string;
  readonly relatedEndpoints?: readonly string[];
  readonly responseKind?: CommerceResponseKind;
  readonly requiredContext: readonly CommerceContextKey[];
  readonly audience?: CollaborationCommerceRouteDefinition["audience"];
  readonly viewPermission: string;
  readonly managePermission?: string;
  readonly columns: readonly CommerceColumnDefinition[];
  readonly actions?: readonly CommerceActionDefinition[];
  readonly media?: boolean;
}

const workspaceActions = "/workspaces/{workspaceId}/actions";
const roleEndpoint = "/workspaces/{workspaceId}/roles/{targetId}";
const roleImpactEndpoint = "/workspaces/{workspaceId}/roles/{targetId}/impact-preview";
const enterpriseEndpoint = "/workspaces/{workspaceId}/enterprise-profile";
const reviewActionEndpoint = "/review-links/{reviewToken}/actions";
const templateActionsEndpoint = "/templates/actions";
const commercialClientEndpoint = "commercial-client";

const seeds: readonly RouteSeed[] = [
  { id: "CR-035", module: "M01", path: "/creator/external-link-permission", loadEndpoint: "/workspaces/{workspaceId}/review-links", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "review.view", managePermission: "review.manage", columns: reviewColumns, actions: [action("createReviewLink", "review.manage", "/workspaces/{workspaceId}/review-links", { bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("projectId", "text"), field("finalVideoVersionId", "text"), field("expiresAt", "text", false), field("watermarkText", "text", false), field("accessSecret", "password", false), field("policy.comment", "checkbox", false), field("policy.approve", "checkbox", false), field("policy.download", "checkbox", false)] }), action("revokeReviewLink", "review.manage", "/workspaces/{workspaceId}/review-links/{targetId}/revoke", { bodyKind: "entity", confirmation: "danger", fields: [field("reason", "textarea", false)] })] },
  { id: "CR-069", module: "M01", path: "/creator/operation-logs", loadEndpoint: "/workspaces/{workspaceId}/audit-events", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.audit.view", managePermission: "workspace.audit.manage", columns: auditColumns },
  { id: "CR-070", module: "M01", path: "/creator/permission-matrix", loadEndpoint: "/workspaces/{workspaceId}/roles", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: roleColumns, actions: [action("previewPermissionImpact", "workspace.member.manage", roleImpactEndpoint, { bodyKind: "entity", confirmation: "none", fields: [field("permissions", "textarea")] }), action("savePermissionMatrix", "workspace.member.manage", roleEndpoint, { method: "PUT", bodyKind: "entity", confirmation: "approval", fields: [field("name", "text"), field("permissions", "textarea")] })] },
  { id: "CR-075", module: "M01", path: "/creator/project-members", loadEndpoint: "/workspaces/{workspaceId}/projects/{projectId}/members", relatedEndpoints: ["/workspaces/{workspaceId}/members"], responseKind: "list", requiredContext: ["workspaceId", "projectId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: projectMemberColumns, actions: [action("assignProjectMember", "workspace.member.manage", "/workspaces/{workspaceId}/projects/{projectId}/members", { bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("memberId", "text"), field("productionRole", "text"), field("dataScope", "textarea"), field("permissions", "textarea"), field("active", "checkbox", false), field("version", "number", true, 0)] })] },
  { id: "CR-077", module: "M01", path: "/creator/project-permission-detail", loadEndpoint: "/workspaces/{workspaceId}/projects/{projectId}/members", relatedEndpoints: ["/workspaces/{workspaceId}/members"], responseKind: "list", requiredContext: ["workspaceId", "projectId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: projectMemberColumns, actions: [action("assignProjectMember", "workspace.member.manage", "/workspaces/{workspaceId}/projects/{projectId}/members", { bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("memberId", "text"), field("productionRole", "text"), field("dataScope", "textarea"), field("permissions", "textarea"), field("active", "checkbox", false), field("version", "number", true, 0)] })] },
  { id: "CR-107", module: "M01", path: "/creator/team-space", loadEndpoint: "/workspaces/{workspaceId}/overview", relatedEndpoints: ["/workspaces/{workspaceId}/members", "/workspaces/{workspaceId}/roles"], responseKind: "object", requiredContext: ["workspaceId"], viewPermission: "workspace.view", managePermission: "workspace.manage", columns: overviewColumns },
  { id: "CR-123", module: "M01", path: "/creator/workspace-permissions", loadEndpoint: "/workspaces/{workspaceId}/roles", relatedEndpoints: ["/workspaces/{workspaceId}/overview"], responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: roleColumns, actions: [action("previewPermissionImpact", "workspace.member.manage", roleImpactEndpoint, { bodyKind: "entity", confirmation: "none", fields: [field("permissions", "textarea")] }), action("savePermissionMatrix", "workspace.member.manage", roleEndpoint, { method: "PUT", bodyKind: "entity", confirmation: "approval", fields: [field("name", "text"), field("permissions", "textarea")] })] },
  { id: "TM-006", module: "M10", path: "/team/team-enterprise-auth", loadEndpoint: enterpriseEndpoint, responseKind: "object", requiredContext: ["workspaceId"], viewPermission: "workspace.enterprise.view", managePermission: "workspace.enterprise.manage", columns: enterpriseColumns, actions: [action("saveEnterpriseProfile", "workspace.enterprise.manage", enterpriseEndpoint, { method: "PUT", bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("legalName", "text"), field("invoiceTitle", "text")] })] },
  { id: "TM-007", module: "M10", path: "/team/team-enterprise", loadEndpoint: enterpriseEndpoint, responseKind: "object", requiredContext: ["workspaceId"], viewPermission: "workspace.enterprise.view", managePermission: "workspace.enterprise.manage", columns: enterpriseColumns, actions: [action("saveEnterpriseProfile", "workspace.enterprise.manage", enterpriseEndpoint, { method: "PUT", bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("legalName", "text"), field("invoiceTitle", "text"), field("dataRetentionDays", "number", true, 1)] })] },
  { id: "TM-008", module: "M10", path: "/team/team-invite-records", loadEndpoint: "/workspaces/{workspaceId}/invitations", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: invitationColumns, actions: [action("revokeInvitation", "workspace.member.manage", "/workspaces/{workspaceId}/invitations/{targetId}/revoke", { bodyKind: "entity", confirmation: "danger" })] },
  { id: "TM-009", module: "M10", path: "/team/team-member-performance", loadEndpoint: "/workspaces/{workspaceId}/member-performance?days=30", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: memberPerformanceColumns },
  { id: "TM-010", module: "M10", path: "/team/team-members", loadEndpoint: "/workspaces/{workspaceId}/members", relatedEndpoints: ["/workspaces/{workspaceId}/roles", "/workspaces/{workspaceId}/overview"], responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: memberColumns, actions: [action("inviteMember", "workspace.member.manage", "/workspaces/{workspaceId}/invitations", { bodyKind: "entity", scope: "page", fields: [field("email", "email"), field("roleId", "text"), field("expiresAt", "text")] })] },
  { id: "TM-011", module: "M10", path: "/team/team-operation-logs", loadEndpoint: "/workspaces/{workspaceId}/audit-events", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.audit.view", managePermission: "workspace.audit.manage", columns: auditColumns },
  { id: "TM-012", module: "M10", path: "/team/team-overview", loadEndpoint: "/workspaces/{workspaceId}/overview", relatedEndpoints: ["/workspaces/{workspaceId}/members", "/workspaces/{workspaceId}/roles"], responseKind: "object", requiredContext: ["workspaceId"], viewPermission: "workspace.view", managePermission: "workspace.manage", columns: overviewColumns },
  { id: "TM-013", module: "M10", path: "/team/team-permission-matrix", loadEndpoint: "/workspaces/{workspaceId}/roles", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: roleColumns, actions: [action("savePermissionMatrix", "workspace.member.manage", roleEndpoint, { method: "PUT", bodyKind: "entity", confirmation: "approval", fields: [field("name", "text"), field("permissions", "textarea")] })] },
  { id: "TM-016", module: "M10", path: "/team/team-roles", loadEndpoint: "/workspaces/{workspaceId}/roles", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "workspace.member.view", managePermission: "workspace.member.manage", columns: roleColumns, actions: [action("updateRole", "workspace.member.manage", roleEndpoint, { method: "PUT", bodyKind: "entity", confirmation: "approval", fields: [field("name", "text"), field("permissions", "textarea")] })] },
  { id: "TM-017", module: "M10", path: "/team/team-seats", loadEndpoint: "/workspaces/{workspaceId}/overview", relatedEndpoints: ["/workspaces/{workspaceId}/members"], responseKind: "object", requiredContext: ["workspaceId"], viewPermission: "workspace.view", managePermission: "workspace.member.manage", columns: overviewColumns, actions: [action("inviteMember", "workspace.member.manage", "/workspaces/{workspaceId}/invitations", { bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("email", "email"), field("roleId", "text"), field("expiresAt", "text")] })] },
  { id: "TM-018", module: "M10", path: "/team/team-space", loadEndpoint: "/workspaces/{workspaceId}/overview", relatedEndpoints: ["/workspaces/{workspaceId}/members", "/workspaces/{workspaceId}/roles"], responseKind: "object", requiredContext: ["workspaceId"], viewPermission: "workspace.view", managePermission: "workspace.manage", columns: overviewColumns },

  { id: "TM-001", module: "M11", path: "/team/team-billing", loadEndpoint: "/workspaces/{workspaceId}/billing", exportEndpoint: "/workspaces/{workspaceId}/billing/export.csv?dataset=documents", relatedEndpoints: ["/workspaces/{workspaceId}/billing/plan"], responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "billing.view", managePermission: "billing.manage", columns: invoiceColumns, actions: [action("requestInvoice", "billing.manage", "/workspaces/{workspaceId}/billing/actions", { scope: "page", confirmation: "financial", fields: [field("amountMinor", "number", true, 1), field("invoiceTitle", "text"), field("currency", "text", false)] }), action("createPaymentOrder", "billing.manage", "/workspaces/{workspaceId}/billing/actions", { scope: "page", confirmation: "financial", fields: [field("amountMinor", "number", true, 1), field("currency", "text", false), field("orderType", "text", false), field("expiresInSeconds", "number", false, 1)] }), action("refundPaymentOrder", "billing.manage", "/workspaces/{workspaceId}/billing/actions", { scope: "page", confirmation: "financial", fields: [field("orderId", "text"), field("amountMinor", "number", true, 1)] })] },
  { id: "TM-004", module: "M11", path: "/team/team-cost", loadEndpoint: "/workspaces/{workspaceId}/costs", exportEndpoint: "/workspaces/{workspaceId}/billing/export.csv?dataset=costs", relatedEndpoints: ["/workspaces/{workspaceId}/billing"], responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "billing.view", managePermission: "billing.manage", columns: costColumns },
  { id: "TM-005", module: "M11", path: "/team/team-credit-flow", loadEndpoint: "/workspaces/{workspaceId}/billing/journals", exportEndpoint: "/workspaces/{workspaceId}/billing/export.csv?dataset=journals", relatedEndpoints: ["/workspaces/{workspaceId}/billing/holds"], responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "billing.view", managePermission: "billing.manage", columns: financeColumns },
  { id: "TM-014", module: "M11", path: "/team/team-plan-detail", loadEndpoint: "/workspaces/{workspaceId}/billing/plan", relatedEndpoints: ["/workspaces/{workspaceId}/billing"], responseKind: "object", requiredContext: ["workspaceId"], viewPermission: "billing.view", managePermission: "billing.manage", columns: planColumns },
  { id: "TM-015", module: "M11", path: "/team/team-project-cost-detail", loadEndpoint: "/workspaces/{workspaceId}/costs", exportEndpoint: "/workspaces/{workspaceId}/billing/export.csv?dataset=costs", relatedEndpoints: ["/workspaces/{workspaceId}/billing/journals"], responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "billing.view", managePermission: "billing.manage", columns: costColumns },
  { id: "CR-009", module: "M16", path: "/creator/billing", loadEndpoint: "/workspaces/{workspaceId}/credit-transactions", exportEndpoint: "/workspaces/{workspaceId}/billing/export.csv?dataset=journals", relatedEndpoints: ["/workspaces/{workspaceId}/credit-account", "/workspaces/{workspaceId}/costs"], responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "billing.view", managePermission: "billing.manage", columns: financeColumns },

  { id: "CL-001", module: "M12", path: "/client/review/approve", loadEndpoint: "/review-links/{reviewToken}/context", responseKind: "object", requiredContext: ["reviewToken"], audience: "client", viewPermission: "review.view", managePermission: "review.approve", columns: reviewColumns, actions: [action("approve", "review.approve", reviewActionEndpoint, { scope: "page", confirmation: "approval", clientSafe: true, fields: [field("reason", "textarea", false)] }), action("reject", "review.approve", reviewActionEndpoint, { scope: "page", confirmation: "approval", clientSafe: true, fields: [field("reason", "textarea")] })] },
  { id: "CL-002", module: "M12", path: "/client/review/comment", loadEndpoint: "/review-links/{reviewToken}/context", responseKind: "object", requiredContext: ["reviewToken"], audience: "client", viewPermission: "review.view", managePermission: "review.comment", columns: reviewColumns, actions: [action("comment", "review.comment", reviewActionEndpoint, { scope: "page", confirmation: "none", clientSafe: true, fields: [field("timecodeMs", "number", true, 0), field("comment", "textarea")] })] },
  { id: "CL-003", module: "M12", path: "/client/review", loadEndpoint: "/review-links/{reviewToken}/context", responseKind: "object", requiredContext: ["reviewToken"], audience: "client", viewPermission: "review.view", columns: reviewColumns },
  { id: "CL-004", module: "M12", path: "/client/review/verify", loadEndpoint: "/review-links/{reviewToken}/context", responseKind: "object", requiredContext: ["reviewToken"], audience: "client", viewPermission: "review.view", columns: reviewColumns, actions: [action("verify", "review.view", reviewActionEndpoint, { scope: "page", confirmation: "none", clientSafe: true, fields: [field("credential", "text")] })] },
  { id: "CL-005", module: "M12", path: "/client/review/player", loadEndpoint: "/review-links/{reviewToken}/context", responseKind: "object", requiredContext: ["reviewToken"], audience: "client", viewPermission: "review.view", columns: reviewColumns, media: true },
  { id: "CR-011", module: "M12", path: "/creator/client-review-link", loadEndpoint: "/projects/{projectId}/reviews", responseKind: "list", requiredContext: ["workspaceId", "projectId"], viewPermission: "review.view", managePermission: "review.manage", columns: reviewColumns, actions: [action("createReviewLink", "review.manage", "/projects/{projectId}/review-links", { bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("finalVideoVersionId", "text"), field("expiresAt", "text", false), field("policy.comment", "checkbox", false), field("policy.approve", "checkbox", false), field("policy.download", "checkbox", false)] })] },
  { id: "CR-085", module: "M12", path: "/creator/review-delivery", loadEndpoint: "/projects/{projectId}/reviews", responseKind: "list", requiredContext: ["workspaceId", "projectId"], viewPermission: "review.view", managePermission: "review.manage", columns: reviewColumns, actions: [action("createReviewLink", "review.manage", "/projects/{projectId}/review-links", { bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("finalVideoVersionId", "text"), field("expiresAt", "text", false), field("policy.comment", "checkbox", false), field("policy.approve", "checkbox", false), field("policy.download", "checkbox", false)] })] },
  { id: "TM-002", module: "M12", path: "/team/team-client-link-management", loadEndpoint: "/workspaces/{workspaceId}/review-links", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "review.view", managePermission: "review.manage", columns: reviewColumns, actions: [action("createReviewLink", "review.manage", "/workspaces/{workspaceId}/review-links", { bodyKind: "entity", scope: "page", confirmation: "approval", fields: [field("projectId", "text"), field("finalVideoVersionId", "text"), field("expiresAt", "text", false), field("policy.comment", "checkbox", false), field("policy.approve", "checkbox", false), field("policy.download", "checkbox", false)] }), action("revokeReviewLink", "review.manage", "/workspaces/{workspaceId}/review-links/{targetId}/revoke", { bodyKind: "entity", confirmation: "danger", fields: [field("reason", "textarea", false)] })] },
  { id: "TM-003", module: "M12", path: "/team/team-client-review", loadEndpoint: "/workspaces/{workspaceId}/review-links", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "review.view", managePermission: "review.manage", columns: reviewColumns },

  { id: "AD-015", module: "M13", path: "/admin/admin-export-template", loadEndpoint: "/admin/business-objects", responseKind: "list", requiredContext: ["workspaceId"], audience: "admin", viewPermission: "admin.business.view", managePermission: "admin.business.manage", columns: templateColumns, actions: [action("publishTemplate", "admin.business.manage", "/admin/business-objects/actions", { confirmation: "approval" })] },
  { id: "AD-020", module: "M13", path: "/admin/admin-message-templates", loadEndpoint: "/admin/business-objects", responseKind: "list", requiredContext: ["workspaceId"], audience: "admin", viewPermission: "admin.business.view", managePermission: "admin.business.manage", columns: templateColumns, actions: [action("publishTemplate", "admin.business.manage", "/admin/business-objects/actions", { confirmation: "approval" })] },
  { id: "AD-049", module: "M13", path: "/admin/admin-templates", loadEndpoint: "/admin/business-objects", responseKind: "list", requiredContext: ["workspaceId"], audience: "admin", viewPermission: "admin.business.view", managePermission: "admin.business.manage", columns: templateColumns, actions: [action("approveTemplate", "admin.business.manage", "/admin/business-objects/actions", { confirmation: "approval" }), action("unpublishTemplate", "admin.business.manage", "/admin/business-objects/actions", { confirmation: "danger", fields: [field("reason", "textarea")] })] },
  { id: "CR-108", module: "M13", path: "/creator/template-audit-submit", loadEndpoint: "/templates", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "template.view", managePermission: "template.manage", columns: templateColumns, actions: [action("submitTemplateReview", "template.manage", templateActionsEndpoint, { confirmation: "approval", fields: [field("rightsStatement", "textarea"), field("priceMinor", "number", true, 0)] })] },
  { id: "CR-109", module: "M13", path: "/creator/template-create", loadEndpoint: "/templates", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "template.view", managePermission: "template.manage", columns: templateColumns, actions: [action("createTemplate", "template.manage", "/templates", { bodyKind: "entity", scope: "page", fields: [field("name", "text"), field("type", "text"), field("scope", "text")] })] },
  { id: "CR-110", module: "M13", path: "/creator/template-detail", loadEndpoint: "/templates", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "template.view", managePermission: "template.manage", columns: templateColumns, actions: [action("applyTemplate", "template.manage", templateActionsEndpoint, { confirmation: "approval" })] },
  { id: "CR-024", module: "M13", path: "/creator/creator-community", loadEndpoint: "/market/items", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "community.view", managePermission: "community.manage", columns: templateColumns, actions: [action("createFork", "community.manage", "/forks", { bodyKind: "entity", confirmation: "financial", fields: [field("targetWorkspaceId", "text")] })] },
  { id: "CR-040", module: "M13", path: "/creator/fork-authorization", loadEndpoint: "/market/items", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "community.view", managePermission: "community.manage", columns: templateColumns, actions: [action("saveForkAuthorization", "community.manage", "/forks", { bodyKind: "entity", confirmation: "approval", fields: [field("licenseScope", "textarea")] })] },
  { id: "CR-041", module: "M13", path: "/creator/fork-lineage", loadEndpoint: "/market/items", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "community.view", managePermission: "community.manage", columns: templateColumns },
  { id: "CR-043", module: "M13", path: "/creator/fork-revenue-share", loadEndpoint: "/market/items", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "community.view", managePermission: "community.manage", columns: templateColumns, actions: [action("saveRevenueShare", "community.manage", "/forks", { bodyKind: "entity", confirmation: "financial", fields: [field("shareBasisPoints", "number", true, 0)] })] },
  { id: "CR-111", module: "M13", path: "/creator/template-market", loadEndpoint: "/templates", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "template.view", managePermission: "template.manage", columns: templateColumns, actions: [action("applyTemplate", "template.manage", templateActionsEndpoint, { confirmation: "financial" })] },
  { id: "CR-112", module: "M13", path: "/creator/templates-fork", loadEndpoint: "/templates", responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "template.view", managePermission: "template.manage", columns: templateColumns, actions: [action("createFork", "template.manage", "/forks", { bodyKind: "entity", confirmation: "financial", fields: [field("targetWorkspaceId", "text")] })] },

  { id: "AD-005", module: "M14", path: "/admin/admin-commercial-detail", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], audience: "admin", viewPermission: "admin.commercial.view", managePermission: "admin.commercial.manage", columns: commercialColumns },
  { id: "AD-006", module: "M14", path: "/admin/admin-commercial-dispute", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], audience: "admin", viewPermission: "admin.commercial.view", managePermission: "admin.commercial.manage", columns: commercialColumns, actions: [action("resolveDispute", "admin.commercial.manage", commercialClientEndpoint, { confirmation: "approval", fields: [field("disputeId", "text"), field("resolution", "textarea")] })] },
  { id: "AD-007", module: "M14", path: "/admin/admin-commercial-settlement", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], audience: "admin", viewPermission: "admin.commercial.view", managePermission: "admin.commercial.manage", columns: commercialColumns, actions: [action("freezeSettlement", "admin.commercial.manage", commercialClientEndpoint, { confirmation: "financial", fields: [field("settlementId", "text"), field("amount_minor", "number", true, 0), field("currency", "text")] }), action("resumeSettlement", "admin.commercial.manage", commercialClientEndpoint, { confirmation: "financial", fields: [field("settlementId", "text"), field("amount_minor", "number", true, 0), field("currency", "text")] }), action("paySettlement", "admin.commercial.manage", commercialClientEndpoint, { confirmation: "financial", fields: [field("settlementId", "text"), field("amount_minor", "number", true, 0), field("currency", "text")] })] },
  { id: "AD-008", module: "M14", path: "/admin/admin-commercial", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], audience: "admin", viewPermission: "admin.commercial.view", managePermission: "admin.commercial.manage", columns: commercialColumns },
  { id: "CR-012", module: "M14", path: "/creator/commercial-acceptance", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "commercial.view", managePermission: "commercial.manage", columns: commercialColumns, actions: [action("acceptDelivery", "commercial.manage", commercialClientEndpoint, { confirmation: "approval", fields: [field("deliveryId", "text"), field("evidence_ref", "text")] }), action("rejectDelivery", "commercial.manage", commercialClientEndpoint, { confirmation: "approval", fields: [field("deliveryId", "text"), field("reason", "textarea")] })] },
  { id: "CR-013", module: "M14", path: "/creator/commercial-delivery", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "commercial.view", managePermission: "commercial.manage", columns: commercialColumns, actions: [action("deliver", "commercial.manage", commercialClientEndpoint, { confirmation: "approval", fields: [field("milestoneId", "text"), field("artifact_version_id", "text"), field("artifact_digest", "text"), field("note", "textarea", false)] })] },
  { id: "CR-014", module: "M14", path: "/creator/commercial-order-detail", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "commercial.view", managePermission: "commercial.manage", columns: commercialColumns, actions: [action("submitQuote", "commercial.manage", commercialClientEndpoint, { confirmation: "financial", fields: [field("amount_minor", "number", true, 1), field("currency", "text"), field("proposal", "textarea"), field("valid_until", "text")] }), action("acceptQuote", "commercial.manage", commercialClientEndpoint, { confirmation: "approval", fields: [field("quoteId", "text")] }), action("recordContract", "commercial.manage", commercialClientEndpoint, { confirmation: "approval", fields: [field("content_ref", "text"), field("content_digest", "text"), field("amount_minor", "number", true, 1)] })] },
  { id: "CR-015", module: "M14", path: "/creator/commercial-orders", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "commercial.view", managePermission: "commercial.manage", columns: commercialColumns, actions: [action("submitQuote", "commercial.manage", commercialClientEndpoint, { confirmation: "financial", fields: [field("amount_minor", "number", true, 1), field("currency", "text"), field("proposal", "textarea"), field("valid_until", "text")] })] },
  { id: "CR-016", module: "M14", path: "/creator/commercial-settlement", loadEndpoint: commercialClientEndpoint, responseKind: "list", requiredContext: ["workspaceId"], viewPermission: "commercial.view", managePermission: "commercial.manage", columns: commercialColumns, actions: [action("openDispute", "commercial.manage", commercialClientEndpoint, { confirmation: "danger", fields: [field("milestoneId", "text"), field("kind", "text"), field("reason", "textarea")] })] },
];

export const collaborationCommerceRoutes: readonly CollaborationCommerceRouteDefinition[] = seeds.map((seed) => ({
  ...seed,
  audience: seed.audience ?? "internal",
  relatedEndpoints: seed.relatedEndpoints ?? [],
  responseKind: seed.responseKind ?? "list",
  actions: seed.actions ?? [],
  titleKey: `pages.${seed.id}.title`,
  descriptionKey: `pages.${seed.id}.description`,
  emptyKey: `pages.${seed.id}.empty`,
}));

export const collaborationCommerceRouteMap = new Map(collaborationCommerceRoutes.map((route) => [route.id, route]));
export const collaborationCommerceRouteMounts = collaborationCommerceRoutes.map(({ id, path }) => ({ id, path }));

export type CollaborationCommercePageId = (typeof collaborationCommerceRoutes)[number]["id"];

export const retryFailedAction: CommerceActionDefinition = action("retryFailed", "", workspaceActions, {
  scope: "page",
  confirmation: "none",
});
