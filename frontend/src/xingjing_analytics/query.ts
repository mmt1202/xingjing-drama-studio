import type { AnalyticsGroup, AnalyticsRange, DashboardQuery } from "./api";

export const ranges: AnalyticsRange[] = ["week", "month", "quarter", "all"];
export const groups: AnalyticsGroup[] = ["project", "episode", "model", "member"];
export interface DashboardDefaults { range: AnalyticsRange; groupBy: AnalyticsGroup }
export function dashboardDefaults(value?: Record<string, unknown>): DashboardDefaults {
  return {
    range: ranges.includes(value?.range as AnalyticsRange) ? value?.range as AnalyticsRange : "week",
    groupBy: groups.includes(value?.groupBy as AnalyticsGroup) ? value?.groupBy as AnalyticsGroup : "project",
  };
}
export function readDashboardQuery(raw: string, defaults: DashboardDefaults): DashboardQuery {
  const params = new URLSearchParams(raw);
  const range = params.get("range") as AnalyticsRange | null;
  const groupBy = params.get("groupBy") as AnalyticsGroup | null;
  const offset = Number(params.get("offset") ?? 0);
  return {
    range: range && ranges.includes(range) ? range : defaults.range,
    groupBy: groupBy && groups.includes(groupBy) ? groupBy : defaults.groupBy,
    q: params.get("q") ?? "", projectId: params.get("projectId") ?? "", episodeId: params.get("episodeId") ?? "", modelId: params.get("modelId") ?? "", memberId: params.get("memberId") ?? "",
    offset: Number.isSafeInteger(offset) && offset >= 0 ? offset : 0, limit: 20,
  };
}
export function dashboardQueryString(value: DashboardQuery): string {
  const { limit: _limit, ...query } = value;
  return new URLSearchParams(Object.entries(query).map(([key, field]) => [key, String(field)])).toString();
}
