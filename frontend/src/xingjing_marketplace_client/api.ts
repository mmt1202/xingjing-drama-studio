export interface TemplateVersion { id: string; number: number; price_minor: number; content: Record<string, unknown>; rights: Rights; revenue_rule: RevenueRule }
export interface TemplateReview { id: string; status: string; statement: string; submitted_at: string; decision_reason: string | null }
export interface TemplateRecord { id: string; title: string; kind: string; tags: string[]; revision: number; review_state: string; publication_state: string; latest_version_id: string; versions: TemplateVersion[]; reviews: TemplateReview[]; updated_at: string }
export interface Rights { scope: string; commercial_use: boolean; attribution_required: boolean; inheritable_scopes: string[]; allowed_workspace_ids: string[]; allow_fork: boolean }
export interface RevenueRule { id: string; shares: Array<{ beneficiary_role: string; basis_points: number }> }
export interface MarketItem { id: string; title: string; source_kind: string; source_version_id: string; template_kind: string | null; author_id: string; tags: string[]; price_minor: number; rights: Rights; revenue_rule: RevenueRule; revision: number; source_fork_id: string | null }
export interface ForkRecord { id: string; target_project_id: string; parent_fork_id: string | null; ancestor_fork_ids: string[]; rights_record: { granted_scopes: string[]; attribution_required: boolean; commercial_use: boolean }; revenue_rule: RevenueRule; lineage: Array<{ fork_id: string; target_project_id: string; source_id: string; source_version_id: string }> }
export interface ForkProject { id: string; name: string; revision: number; version_id: string }
export interface Page<T> { items: T[]; next_cursor: string | null; total: number }

class MarketplaceApiError extends Error { constructor(readonly status: number, readonly code: string) { super(code); } }

export function createMarketplaceApi({ accessToken, baseUrl = "/api/v1", fetcher = fetch }: { accessToken?: string; baseUrl?: string; fetcher?: typeof fetch } = {}) {
  const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const headers = new Headers(init.headers); headers.set("Accept", "application/json"); headers.set("X-Request-Id", crypto.randomUUID());
    if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`); if (init.body) headers.set("Content-Type", "application/json");
    const response = await fetcher(`${baseUrl}${path}`, { ...init, headers });
    const payload = await response.json() as { data?: T; error?: { code?: string } };
    if (!response.ok || payload.data === undefined) throw new MarketplaceApiError(response.status, payload.error?.code ?? "MARKETPLACE_REQUEST_FAILED");
    return payload.data;
  };
  const write = <T,>(path: string, body: object, method = "POST", version?: number) => request<T>(path, { method,
    headers: { "Idempotency-Key": crypto.randomUUID(), ...(version ? { "If-Match": String(version) } : {}) }, body: JSON.stringify(body) });
  return {
    listTemplates: async () => (await request<{ items: TemplateRecord[] }>("/templates")).items,
    template: (id: string) => request<TemplateRecord>(`/templates/${encodeURIComponent(id)}`),
    createTemplate: (body: object) => write<{ resource_type: string; resource_id: string }>("/templates", body),
    createTemplateVersion: (template: TemplateRecord, body: object) => write<{ resource_type: string; resource_id: string }>(`/templates/${encodeURIComponent(template.id)}/versions`, body, "PUT", template.revision),
    submitReview: (template: TemplateRecord, statement: string) => write(`/templates/${encodeURIComponent(template.id)}/reviews`, { statement }, "POST", template.revision),
    withdrawTemplate: (template: TemplateRecord, reason: string) => write(`/templates/${encodeURIComponent(template.id)}/withdraw`, { reason }, "POST", template.revision),
    market: (query = "", cursor?: string) => request<Page<MarketItem>>(`/market/items?search=${encodeURIComponent(query)}&page_size=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    marketItem: (id: string) => request<MarketItem>(`/market/items/${encodeURIComponent(id)}`),
    createFork: (item: MarketItem, projectName: string, options: { commercialUse: boolean; scopes: string[] }) => write<{ resource_type: string; resource_id: string }>("/forks", {
      market_item_id: item.id, expected_market_revision: item.revision, expected_source_version_id: item.source_version_id,
      project_name: projectName, intended_commercial_use: options.commercialUse, requested_inheritable_scopes: options.scopes,
    }),
    listForks: async () => (await request<{ items: ForkRecord[] }>("/forks")).items,
    lineage: (id: string) => request<ForkRecord["lineage"]>(`/forks/${encodeURIComponent(id)}/lineage`),
    forkProject: (projectId: string) => request<ForkProject>(`/fork-projects/${encodeURIComponent(projectId)}`),
    publishCommunityProject: (fork: ForkRecord, project: ForkProject, title: string) => write<{ resource_type: string; resource_id: string }>("/community-projects", {
      fork_id: fork.id,
      title,
      tags: [],
      rights: {
        scope: "public",
        commercial_use: fork.rights_record.commercial_use,
        attribution_required: fork.rights_record.attribution_required,
        inheritable_scopes: fork.rights_record.granted_scopes,
        allowed_workspace_ids: [],
        allow_fork: true,
      },
      revenue_shares: fork.revenue_rule.shares,
      price_minor: 0,
    }, "POST", project.revision),
    adminObjects: () => request<{ items: TemplateRecord[]; audit_events: unknown[] }>("/admin/templates"),
    adminDecision: (template: TemplateRecord, review: TemplateReview, decision: "approve" | "reject", reason: string) => write("/admin/templates/actions", {
      action: "decide_review", review_id: review.id, decision, reason, version: template.revision,
    }),
  };
}

export type MarketplaceApi = ReturnType<typeof createMarketplaceApi>;
export function marketplaceError(error: unknown): string {
  if (!(error instanceof MarketplaceApiError)) return "市场服务请求失败，请稍后重试。";
  if (error.status === 403) return "当前账号没有此操作权限。";
  if (error.status === 409) return "数据已被更新，请刷新后重试。";
  if (error.code === "MARKETPLACE_BALANCE_INSUFFICIENT") return "团队余额不足，无法购买并 Fork 此模板。";
  return `操作失败（${error.code}）`;
}
