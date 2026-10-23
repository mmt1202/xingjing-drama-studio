import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowUpRight, Download, RefreshCw, Settings2 } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { useCreatorWorkspace } from "@/xingjing_creator_home/WorkspaceFrame";
import { createPreferenceApi, type PreferenceRecord } from "@/xingjing_creator_settings/api";
import { AnalyticsApiError, createAnalyticsApi, downloadReport, type CreatorDashboard, type DashboardFilters, type DashboardQuery, type FilterOption, type StatisticRow } from "./api";
import { CostChart, CostValues, CoverageNotes, DashboardMetrics, MemberChart, ModelChart, ProductionChart, ProjectStatusChart, TaskTrend } from "./Charts";
import { analyticsButton, analyticsField, analyticsPanel, useAnalyticsTranslation } from "./i18n";
import { DashboardPreferences } from "./Preferences";
import { dashboardDefaults, dashboardQueryString, groups, ranges, readDashboardQuery } from "./query";
import { readAnalyticsReturn, rememberAnalytics, restoreAnalyticsScroll } from "./return-state";
import { attributionLabel } from "./labels";

export function AnalyticsError({ error, retry }: { error: unknown; retry: () => void }) {
  const { t } = useAnalyticsTranslation();
  const apiError = error instanceof AnalyticsApiError ? error : undefined;
  const code = apiError?.code;
  const message = code === "WORKSPACE_NOT_FOUND" ? "scopeError" : code === "ANALYTICS_EXPORT_LIMIT" ? "exportLimit" : code === "ANALYTICS_SCAN_LIMIT" ? "scanLimit" : code === "ANALYTICS_IDEMPOTENCY_CONFLICT" ? "exportConflict" : code === "ANALYTICS_EXPORT_SCOPE_CHANGED" ? "exportScopeChanged" : apiError?.status === 403 ? "forbidden" : apiError?.status === 400 || apiError?.status === 404 || apiError?.status === 422 ? "invalid" : "unavailable";
  return <div role="alert" className="rounded-xl border border-amber-300/25 bg-amber-300/[.04] p-5 text-sm text-amber-200"><p>{t(message)}</p>{apiError?.requestId ? <p className="mt-2 text-xs">{t("requestId", { id: apiError.requestId })}</p> : null}<button className={analyticsButton + " mt-4"} onClick={retry}>{t("retry")}</button></div>;
}

function DashboardSkeleton() {
  const { t } = useAnalyticsTranslation();
  return <div role="status" className="space-y-5 py-5"><p className="text-sm text-slate-500">{t("loading")}</p><div aria-hidden="true" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((item) => <div key={item} className={analyticsPanel + " h-40"}><div className="h-3 w-24 rounded bg-white/[.05]" /><div className="mt-6 h-8 w-16 rounded bg-white/[.04]" /></div>)}</div><div aria-hidden="true" className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_320px]"><div className={analyticsPanel + " h-80"} /><div className={analyticsPanel + " h-80"} /></div></div>;
}

export function CreatorAnalyticsPage() {
  const { access, token, go } = useCreatorWorkspace();
  const { t, i18n } = useAnalyticsTranslation();
  const [location, navigate] = useLocation();
  const search = useSearch();
  const api = useMemo(() => createAnalyticsApi(access.workspaceId, access.actorId, token), [access.workspaceId, access.actorId, token]);
  const preferenceApi = useMemo(() => createPreferenceApi({ workspaceId: access.workspaceId, accessToken: token }), [access.workspaceId, token]);
  const [record, setRecord] = useState<PreferenceRecord | null>();
  const [preferenceFailed, setPreferenceFailed] = useState(false);
  const [preferenceReload, setPreferenceReload] = useState(0);
  const [editing, setEditing] = useState(false);
  const [preferenceSaved, setPreferenceSaved] = useState(false);
  const [data, setData] = useState<CreatorDashboard>();
  const [error, setError] = useState<unknown>();
  const [loaded, setLoaded] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>();
  const [exported, setExported] = useState(false);
  const active = useRef(true);
  const exportBusy = useRef(false);
  const exportController = useRef<AbortController | null>(null);
  const restored = useRef(false);
  const defaults = dashboardDefaults(record?.value);
  const query = useMemo(() => readDashboardQuery(search, { range: defaults.range, groupBy: defaults.groupBy }), [search, defaults.range, defaults.groupBy]);
  const queryKey = dashboardQueryString(query);
  const canView = access.permissions.includes("analytics.view");
  const canManage = access.permissions.includes("analytics.manage");
  const preferencesReady = record !== undefined;

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; exportController.current?.abort(); };
  }, []);

  useEffect(() => {
    let current = true;
    if (!canView) return;
    void preferenceApi.get("data-dashboard").then((value) => { if (current) { setRecord(value); setPreferenceFailed(false); } }).catch(() => { if (current) { setRecord(null); setPreferenceFailed(true); } });
    return () => { current = false; };
  }, [preferenceApi, preferenceReload, canView]);

  useEffect(() => {
    if (record === undefined || !canView) return;
    if (new URLSearchParams(search).toString() !== queryKey) navigate(`${location}?${queryKey}`, { replace: true });
  }, [record, canView, search, queryKey, location, navigate]);

  useEffect(() => {
    if (!preferencesReady || !canView) return;
    const abort = new AbortController();
    const requestQuery = readDashboardQuery(queryKey, { range: "week", groupBy: "project" });
    const timer = window.setTimeout(() => {
      setLoading(true); setError(undefined);
      void api.dashboard(requestQuery, abort.signal).then((value) => {
        if (!abort.signal.aborted) { setData(value); setLoaded(queryKey); }
      }).catch((problem: unknown) => {
        if (!abort.signal.aborted) { setData(undefined); setError(problem); }
      }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    }, 0);
    return () => { window.clearTimeout(timer); abort.abort(); };
  }, [api, preferencesReady, canView, queryKey, reload]);

  const page = loaded === queryKey ? data : undefined;
  useEffect(() => {
    if (!page || restored.current) return;
    restored.current = true;
    const previous = readAnalyticsReturn(access.actorId, access.workspaceId);
    if (queryKey !== previous.query) return;
    return restoreAnalyticsScroll(previous.scroll);
  }, [access.actorId, access.workspaceId, page, queryKey]);

  function filter(values: Partial<DashboardQuery>) {
    if (editing || exporting) return;
    setExportError(undefined); setExported(false);
    navigate(`${location}?${dashboardQueryString({ ...query, offset: 0, ...values })}`);
  }
  function openRow(row: StatisticRow, tasks = false) {
    if (!row.projectId || editing) return;
    rememberAnalytics(access.actorId, access.workspaceId, queryKey);
    const params = new URLSearchParams();
    if (row.episodeId) params.set("episodeId", row.episodeId);
    go(`projects/${encodeURIComponent(row.projectId)}/${tasks ? "tasks" : "control"}${params.size ? `?${params}` : ""}`);
  }
  async function exportReport() {
    if (!page?.canExport || editing || exportBusy.current) return;
    exportBusy.current = true; setExporting(true); setExported(false); setExportError(undefined);
    const abort = new AbortController(); exportController.current = abort;
    const { offset: _offset, limit: _limit, ...filters } = query;
    try {
      const blob = await api.exportReport(filters, abort.signal);
      if (active.current && !abort.signal.aborted) { downloadReport(blob, query.range); setExported(true); }
    } catch (problem) { if (active.current && !abort.signal.aborted) setExportError(problem); }
    finally { exportBusy.current = false; if (active.current) setExporting(false); }
  }
  const formatDate = (value: string) => new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value));

  if (!canView) return <main className="p-6 sm:p-8"><h1 className="text-xl font-semibold">{t("title")}</h1><p role="alert" className="mt-6 text-sm text-amber-200">{t("forbidden")}</p></main>;
  return <main className="xj-analytics-page">
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 py-5"><div><h1 className="text-xl font-semibold">{t("title")}</h1><p className="mt-2 text-xs text-slate-500">{t("subtitle")}</p></div><div className="flex flex-wrap gap-2"><button className={analyticsButton} disabled={editing || exporting || record === undefined} onClick={() => setReload((value) => value + 1)}><RefreshCw size={15} />{t("refresh")}</button>{canManage ? <button className={analyticsButton} disabled={editing || exporting || record === undefined || preferenceFailed} onClick={() => { setEditing(true); setPreferenceSaved(false); }}><Settings2 size={15} />{t("configure")}</button> : null}<button className={analyticsButton + " border-violet-300/30 bg-gradient-to-r from-violet-500/20 to-orange-400/10"} disabled={!page?.canExport || loading || editing || exporting} onClick={() => void exportReport()}><Download size={15} />{t(exporting ? "exporting" : "export")}</button></div></header>
    {preferenceFailed ? <p role="status" className="mt-4 text-xs leading-6 text-amber-200">{t("preferenceFailed")} <button className="underline" onClick={() => setPreferenceReload((value) => value + 1)}>{t("retry")}</button></p> : null}
    {editing && record !== undefined ? <div className="mt-5"><DashboardPreferences api={preferenceApi} record={record} onSaved={(value) => { setRecord(value); setEditing(false); setPreferenceSaved(true); }} onClose={() => setEditing(false)} onReload={() => { setEditing(false); setRecord(undefined); setPreferenceReload((value) => value + 1); }} /></div> : null}
    {preferenceSaved ? <p role="status" className="mt-4 text-sm text-emerald-300">{t("preferenceSaved")}</p> : null}
    <nav aria-label={t("range")} className="mt-5 flex flex-wrap gap-2">{ranges.map((range) => <button key={range} disabled={editing || exporting} aria-current={query.range === range ? "true" : undefined} onClick={() => filter({ range })} className={analyticsButton + (query.range === range ? " border-violet-300/50 bg-violet-400/15 text-violet-100" : " text-slate-500")}>{t(range)}</button>)}</nav>
    <DashboardFiltersForm query={query} data={page} disabled={editing || exporting} onFilter={filter} />
    {error ? <div className="my-5"><AnalyticsError error={error} retry={() => setReload((value) => value + 1)} /></div> : !page || record === undefined ? <DashboardSkeleton /> : <div aria-busy={loading}>
      {loading ? <p role="status" className="mb-4 text-xs text-slate-500">{t("loading")}</p> : null}
      <p className="mb-4 text-xs leading-6 text-slate-500">{page.range.start ? t("selectedPeriod", { start: formatDate(page.range.start), end: formatDate(page.range.end) }) : t("allUntil", { end: formatDate(page.range.end) })}</p>
      {page.comparison ? <p className="mb-4 text-[11px] leading-6 text-violet-200/70">{t("comparisonWindow", { start: formatDate(page.comparison.start), end: formatDate(page.comparison.end) })}</p> : null}
      <DashboardMetrics data={page} />
      <SummaryLine data={page} />
      <div className="mt-5 grid items-start gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(280px,320px)]"><div className="min-w-0 space-y-5"><ProductionChart data={page} /><ModelChart data={page} select={(modelId) => filter({ modelId, groupBy: "model" })} /><MemberChart data={page} select={(memberId) => filter({ memberId, groupBy: "member" })} /></div><aside className="min-w-0 space-y-5"><ProjectStatusChart data={page} /><TaskTrend data={page} /><CostChart data={page} /></aside></div>
      <div className="mt-6"><StatisticTable data={page} query={query} disabled={editing || exporting} onFilter={filter} onOpen={openRow} /></div>
      <div className="mt-6"><CoverageNotes data={page} /></div>
      <footer className="mt-6 flex flex-wrap items-start justify-between gap-3 border-t border-[#2A2E3A] pt-5 text-xs leading-6 text-slate-500"><p>{t("snapshot", { time: formatDate(page.generatedAt) })}（UTC）</p><p>{t("noAutoRefresh")}</p></footer>
    </div>}
    {exportError ? <div className="mt-5"><AnalyticsError error={exportError} retry={() => void exportReport()} /></div> : exported ? <p role="status" className="mt-5 text-sm text-emerald-300">{t("exported")}</p> : null}
  </main>;
}

function DashboardFiltersForm({ query, data, disabled, onFilter }: { query: DashboardQuery; data?: CreatorDashboard; disabled: boolean; onFilter: (values: Partial<DashboardQuery>) => void }) {
  const { t } = useAnalyticsTranslation();
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const value = new FormData(event.currentTarget).get("q");
    onFilter({ q: typeof value === "string" ? value.trim() : "" });
  }
  const options = data?.options;
  const select = (field: "projectId" | "episodeId" | "modelId" | "memberId", values: FilterOption[]) => <label className="min-w-0 space-y-2 text-[11px] text-slate-500"><span>{t(field)}</span><select className={analyticsField + " !py-2"} value={query[field]} disabled={disabled} onChange={(event) => onFilter({ [field]: event.target.value, ...(field === "projectId" ? { episodeId: "", modelId: "", memberId: "" } : {}) })}><option value="">{t("all")}</option>{query[field] && !values.some((item) => item.id === query[field]) ? <option value={query[field]}>{field === "projectId" ? query[field] : attributionLabel(query[field], query[field], t("unknown"), field === "episodeId")}</option> : null}{values.map((item) => <option key={item.id} value={item.id}>{field === "projectId" ? item.label : attributionLabel(item.id, item.label, t("unknown"), field === "episodeId")}</option>)}</select></label>;
  return <section className="my-5 rounded-2xl border border-[#2A2E3A] bg-[#11141D]/60 p-4">
    <form key={query.q} onSubmit={submit} className="flex flex-wrap gap-2"><label className="min-w-0 flex-1"><span className="sr-only">{t("search")}</span><input name="q" defaultValue={query.q} maxLength={160} placeholder={t("search")} disabled={disabled} className={analyticsField} /></label><button className={analyticsButton} disabled={disabled}>{t("searchAction")}</button><button type="button" className={analyticsButton} disabled={disabled} onClick={() => onFilter({ projectId: "", episodeId: "", modelId: "", memberId: "", q: "" })}>{t("clear")}</button></form>
    <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {select("projectId", options?.projects ?? [])}{select("episodeId", options?.episodes.filter((item) => !query.projectId || item.projectId === query.projectId || item.id === "unknown") ?? [])}{select("modelId", options?.models ?? [])}{select("memberId", options?.members ?? [])}
      <label className="min-w-0 space-y-2 text-[11px] text-slate-500"><span>{t("groupBy")}</span><select className={analyticsField + " !py-2"} disabled={disabled} value={query.groupBy} onChange={(event) => onFilter({ groupBy: event.target.value as DashboardFilters["groupBy"] })}>{groups.map((group) => <option key={group} value={group}>{t(group)}</option>)}</select></label>
    </div>
  </section>;
}

function SummaryLine({ data }: { data: CreatorDashboard }) {
  const { t, i18n } = useAnalyticsTranslation();
  const hidden = !data.coverage.sources.some((source) => ["generation", "audio", "editing"].includes(source));
  return <dl className="mt-4 grid grid-cols-2 gap-3 rounded-xl border border-white/[.06] bg-[#0C0E16] p-4 sm:grid-cols-3 xl:grid-cols-6">{[["tasks", hidden ? "—" : data.summary.tasks], ["successRate", hidden || data.summary.successRate === null ? "—" : `${new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 1 }).format(data.summary.successRate)}%`], ["attempts", hidden ? "—" : data.summary.retriedTasks], ["failed", hidden ? "—" : data.summary.failed], ["deliveryCount", data.coverage.notes.includes("deliveries_restricted") || data.filters.modelId || data.filters.memberId ? "—" : data.summary.deliveries], ["deliveredEpisodes", data.coverage.notes.includes("deliveries_restricted") || data.filters.modelId || data.filters.memberId ? "—" : data.summary.deliveredEpisodes]].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-[10px] leading-5 text-slate-500">{t(String(label))}</dt><dd className="mt-1 text-sm tabular-nums text-slate-200">{value}</dd></div>)}</dl>;
}

function StatisticTable({ data, query, disabled, onFilter, onOpen }: { data: CreatorDashboard; query: DashboardQuery; disabled: boolean; onFilter: (values: Partial<DashboardQuery>) => void; onOpen: (row: StatisticRow, tasks?: boolean) => void }) {
  const { t, i18n } = useAnalyticsTranslation();
  const hiddenTasks = !data.coverage.sources.some((source) => ["generation", "audio", "editing"].includes(source));
  const hiddenFinals = data.coverage.notes.includes("finals_restricted") || Boolean(query.modelId || query.memberId);
  const hiddenDeliveries = data.coverage.notes.includes("deliveries_restricted") || Boolean(query.modelId || query.memberId);
  return <section className={analyticsPanel}>
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{t("detail")}</h2><span className="text-xs text-slate-500">{t("groupBy")}: {t(query.groupBy)}</span></div>
    <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-xs"><thead className="border-b border-[#2A2E3A] text-[10px] text-slate-500"><tr>{["object", "tasks", "successRate", "attempts", "generatedEpisodes", "deliveryCount", "groupCost"].map((key) => <th key={key} className="whitespace-nowrap py-3 pr-5 font-medium">{t(key)}</th>)}<th className="py-3"><span className="sr-only">{t("openProject")}</span></th></tr></thead><tbody className="divide-y divide-white/[.05]">{data.rows.map((row) => <tr key={row.id}>
      <td className="max-w-64 min-w-40 py-4 pr-5"><p className="truncate text-sm text-slate-200" title={row.label}>{row.modelId || row.memberId || row.episodeId ? attributionLabel(row.modelId ?? row.memberId ?? row.episodeId ?? row.id, row.label, t("unknown"), Boolean(row.episodeId)) : row.label}</p><p className="mt-2 max-w-60 truncate font-mono text-[10px] text-slate-600" title={row.id}>{row.id}</p></td><td className="py-4 pr-5 tabular-nums text-slate-300">{hiddenTasks ? "—" : row.tasks}</td><td className="py-4 pr-5 tabular-nums text-slate-300">{hiddenTasks || row.successRate === null ? "—" : `${new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 1 }).format(row.successRate)}%`}</td><td className="py-4 pr-5 tabular-nums text-slate-300">{hiddenTasks ? "—" : row.retriedTasks}</td><td className="py-4 pr-5 tabular-nums text-slate-300">{hiddenFinals || query.groupBy === "model" || query.groupBy === "member" ? "—" : row.episodes}</td><td className="py-4 pr-5 tabular-nums text-slate-300">{hiddenDeliveries || query.groupBy === "model" || query.groupBy === "member" ? "—" : row.deliveries}</td><td className="py-4 pr-5"><CostValues costs={row.costs} compact /></td><td className="py-4 text-right">{row.projectId ? <div className="flex flex-col items-end gap-2"><button className="inline-flex items-center gap-1 whitespace-nowrap text-orange-200 disabled:opacity-40" disabled={disabled} onClick={() => onOpen(row)}>{t("openProject")}<ArrowUpRight size={13} /></button>{!hiddenTasks ? <button className="whitespace-nowrap text-slate-400 hover:text-orange-200 disabled:opacity-40" disabled={disabled} onClick={() => onOpen(row, true)}>{t("openTasks")}</button> : null}</div> : row.modelId || row.memberId ? <button className="whitespace-nowrap text-orange-200 disabled:opacity-40" disabled={disabled} onClick={() => onFilter({ ...(row.modelId ? { modelId: row.modelId } : { memberId: row.memberId }), groupBy: "project" })}>{t("project")} →</button> : null}</td>
    </tr>)}</tbody></table></div>
    {!data.rows.length ? <p className="py-10 text-center text-sm text-slate-500">{t(query.q || query.projectId || query.episodeId || query.modelId || query.memberId ? "noMatches" : "noDimensions")}</p> : null}
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>{t("records", { total: data.total, page: Math.floor(data.offset / data.limit) + 1 })}</span><div className="flex gap-2"><button className={analyticsButton} disabled={disabled || data.offset === 0} onClick={() => onFilter({ offset: Math.max(0, data.offset - data.limit) })}>{t("previous")}</button><button className={analyticsButton} disabled={disabled || data.offset + data.limit >= data.total} onClick={() => onFilter({ offset: data.offset + data.limit })}>{t("next")}</button></div></div>
  </section>;
}
