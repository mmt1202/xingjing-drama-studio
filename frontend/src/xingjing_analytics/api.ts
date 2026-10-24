import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";

export type AnalyticsRange = "week" | "month" | "quarter" | "all";
export type AnalyticsGroup = "project" | "episode" | "model" | "member";
export interface DashboardFilters { range: AnalyticsRange; projectId: string; episodeId: string; modelId: string; memberId: string; q: string; groupBy: AnalyticsGroup }
export interface DashboardQuery extends DashboardFilters { offset: number; limit: number }
export interface CostAmount { currency: string; actualMinor: string; heldMinor: string; failedMinor: string; retryMinor: string; dailyActualMinor: string | null }
export interface Metrics {
  projects: number; episodes: number; shots: number; tasks: number; succeeded: number; failed: number; cancelled: number; processing: number;
  successRate: number | null; retriedTasks: number; averageTaskDurationMs: number | null; taskDurationSamples: number;
  deliveries: number; deliveredEpisodes: number; costs: CostAmount[];
}
export interface StatisticRow extends Metrics { id: string; label: string; projectId?: string; episodeId?: string; modelId?: string; memberId?: string }
export interface FilterOption { id: string; label: string }
export interface CreatorDashboard {
  workspaceId: string; actorId: string; generatedAt: string;
  range: { key: AnalyticsRange; start: string | null; end: string; timeZone: "UTC" };
  filters: Omit<DashboardFilters, "range">;
  summary: Metrics;
  comparison: { start: string; end: string; summary: Metrics } | null;
  trend: ({ date: string } & Metrics)[];
  trendGranularity: "day" | "month";
  monthlyProduction: { month: string; episodes: number }[];
  projectStates: { state: string; count: number }[];
  modelUsage: { id: string; label: string; tasks: number; share: number }[];
  memberContributions: { id: string; label: string; tasks: number; succeeded: number }[];
  rows: StatisticRow[]; total: number; offset: number; limit: number;
  options: { projects: FilterOption[]; episodes: (FilterOption & { projectId: string })[]; models: FilterOption[]; members: FilterOption[] };
  coverage: { sources: string[]; missingAttribution: { model: number; member: number; episode: number }; notes: string[] };
  canExport: boolean;
}
export class AnalyticsApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) { super(code); }
}

interface ErrorEnvelope { error?: { code?: string }; detail?: { code?: string }; meta?: { requestId?: string } }

export function createAnalyticsApi(workspaceId: string, actorId: string, token: string | null) {
  const fetcher = createAuthenticatedFetcher({ workspaceId, actorId });
  const base = "/api/v1/analytics/dashboards/creator";
  const receipts = new Map<string, string>();
  function headers() {
    return { Accept: "application/json", "X-Workspace-Id": workspaceId, "X-Request-Id": crypto.randomUUID(), "Accept-Language": i18n.resolvedLanguage ?? i18n.language, ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  }
  async function send(path: string, init: RequestInit): Promise<Response> {
    let response: Response;
    try { response = await fetcher(path, { ...init, credentials: "same-origin", cache: "no-store" }); }
    catch (problem) { if (init.signal?.aborted) throw problem; throw new AnalyticsApiError(0, "NETWORK_UNAVAILABLE"); }
    if (!response.ok) {
      const problem = await response.json().catch(() => null) as ErrorEnvelope | null;
      throw new AnalyticsApiError(response.status, problem?.error?.code ?? problem?.detail?.code ?? "ANALYTICS_REQUEST_FAILED", problem?.meta?.requestId);
    }
    return response;
  }
  return {
    async dashboard(query: DashboardQuery, signal: AbortSignal): Promise<CreatorDashboard> {
      const params = new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]));
      const response = await send(`${base}?${params}`, { signal, headers: headers() });
      const parsed = await response.json().catch(() => null) as { data?: CreatorDashboard } | null;
      const value = parsed?.data;
      if (!value || value.workspaceId !== workspaceId || value.actorId !== actorId) throw new AnalyticsApiError(404, "WORKSPACE_NOT_FOUND");
      if (!value.summary || !Array.isArray(value.rows) || !Array.isArray(value.trend) || !Array.isArray(value.monthlyProduction) || !value.options || !value.coverage) throw new AnalyticsApiError(502, "ANALYTICS_RESPONSE_INVALID");
      return value;
    },
    async exportReport(query: DashboardFilters, signal: AbortSignal): Promise<Blob> {
      const body = JSON.stringify(query);
      const key = receipts.get(body) ?? crypto.randomUUID();
      receipts.set(body, key);
      const response = await send(`${base}/export`, { method: "POST", signal, headers: { ...headers(), Accept: "text/csv", "Content-Type": "application/json", "Idempotency-Key": key }, body });
      if (!response.headers.get("Content-Type")?.toLowerCase().includes("text/csv")) throw new AnalyticsApiError(502, "ANALYTICS_RESPONSE_INVALID");
      const blob = await response.blob();
      receipts.delete(body);
      return blob;
    },
  };
}
export type AnalyticsApi = ReturnType<typeof createAnalyticsApi>;

export function downloadReport(blob: Blob, range: AnalyticsRange) {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href; anchor.download = `creator-analytics-${range}-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}
