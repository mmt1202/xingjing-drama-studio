import { getTokenForSession } from "@/utils/auth";
import { useAuthStore } from "@/stores/auth-store";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowRight, BookOpen, Clapperboard, Film, GitFork, MessageSquare, RefreshCw, Settings2, Sparkles } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { createPreferenceApi, type PreferenceRecord } from "@/xingjing_creator_settings/api";
import { createMarketplaceApi, type MarketItem } from "@/xingjing_marketplace_client/api";
import { useControlTranslation } from "@/xingjing_creator_projects/control-i18n";
import { HomeApiError, type HomePage, type RecentProject, type WorkspaceTask } from "./api";
import { HomePreferencesEditor, homePreferences } from "./HomePreferences";
import { homeButton, homeField, useHomeTranslation } from "./i18n";
import { StoryStarter } from "./StoryStarter";
import { ProjectCard } from "./ProjectCard";
import { useCreatorWorkspace } from "./WorkspaceFrame";
import { readHomeReturn, restoreHomeScroll } from "./return-state";

export function HomeError({ error, retry }: { error: unknown; retry: () => void }) {
  const { t } = useHomeTranslation();
  return <div role="alert" className="rounded-xl border border-amber-300/20 p-5 text-sm text-amber-200"><p>{t(error instanceof HomeApiError && error.status === 403 ? "forbidden" : "failed")}</p><button className={homeButton + " mt-3"} onClick={retry}>{t("retry")}</button></div>;
}
const quickActions = [
  { key: "script", path: "script-import", permission: "script.view", icon: BookOpen },
  { key: "director", path: "ai-director", permission: "script.view", icon: Clapperboard },
  { key: "generation", path: "generation", permission: "generation.view", icon: Film },
  { key: "clientReview", path: "collaboration/CR-085", permission: "review.view", icon: MessageSquare },
];
function searchText(form: HTMLFormElement) {
  const value = new FormData(form).get("q");
  return typeof value === "string" ? value.trim() : "";
}
export function CreatorHomePage() {
  const { t } = useHomeTranslation();
  const { access } = useCreatorWorkspace();
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const preferencesApi = useMemo(() => createPreferenceApi({ workspaceId: access.workspaceId, accessToken: getTokenForSession(sessionEpoch) }), [access.workspaceId, sessionEpoch]);
  const [record, setRecord] = useState<PreferenceRecord | null>();
  const [preferenceError, setPreferenceError] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [reload, setReload] = useState(0);
  const [target, setTarget] = useState("control");
  const results = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    const task = access.permissions.includes("settings.view") ? preferencesApi.get("creator-home") : Promise.resolve(null);
    void task.then((value) => { if (active) { setRecord(value); setPreferenceError(false); } }).catch(() => { if (active) { setRecord(null); setPreferenceError(true); } });
    return () => { active = false; };
  }, [access.permissions, preferencesApi, reload]);
  const preferences = homePreferences(record?.value);
  if (!access.permissions.includes("project.view")) return <p role="alert" className="p-8">{t("forbidden")}</p>;
  return <main className="xj-home-page">
    <div className="xj-home-tools">{access.permissions.includes("settings.manage") && <button className={homeButton} disabled={record === undefined || preferenceError || editing} onClick={() => { setEditing(true); setSaved(false); }}><Settings2 size={15} />{t("customize")}</button>}</div>
    {preferenceError && <p role="status" className="mt-4 text-xs text-amber-200">{t("preferenceFailed")} <button className="underline" onClick={() => setReload((value) => value + 1)}>{t("retry")}</button></p>}
    {editing && record !== undefined && <div className="mt-5"><HomePreferencesEditor api={preferencesApi} record={record} onSaved={(next) => { setRecord(next); setEditing(false); setSaved(true); }} onClose={() => setEditing(false)} onReload={() => { setEditing(false); setReload((value) => value + 1); }} /></div>}
    {saved && <p role="status" className="mt-3 text-sm text-emerald-300">{t("saved")}</p>}
    {record === undefined ? <p role="status" className="py-20 text-center text-slate-400">{t("loading")}</p> : <StoryStarter defaultMode={preferences.defaultMode} />}
    <section className="mb-9 border-y border-white/10 py-5" aria-label={t("quick")}><div className="mb-4 flex flex-wrap items-baseline gap-x-5 gap-y-2"><h2 className="text-lg font-semibold">{t("quick")}</h2><p className="text-xs text-slate-500">{t("quickHint")}</p></div><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{quickActions.map(({ key, path, permission, icon: Icon }) => <button key={key} disabled={!access.permissions.includes(permission) || editing} onClick={() => { setTarget(path); results.current?.scrollIntoView({ behavior: "instant", block: "start" }); }} className="group flex items-center gap-3 rounded-xl border border-white/10 bg-[#11141D] p-4 text-left transition hover:border-violet-300/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 disabled:opacity-40"><Icon size={20} className="text-violet-300" /><span className="flex-1 text-sm">{t(key)}</span><ArrowRight size={15} className="text-slate-500 group-hover:text-orange-200" /></button>)}</div></section>
    <div ref={results}>{record !== undefined && (preferences.showRecent || target !== "control") && <RecentProjects defaultDays={preferences.recentDays} limit={preferences.recentLimit} target={target} onTarget={setTarget} disabled={editing} />}</div>
    <div className="mt-9 grid items-start gap-6 xl:grid-cols-2">{preferences.showTasks && access.permissions.includes("generation.view") && <WorkspaceTasks compact />}{preferences.showInspiration && access.permissions.includes("template.view") && <Inspiration />}</div>
  </main>;
}

function RecentProjects({ defaultDays, limit, target, onTarget, disabled }: { defaultDays: number; limit: number; target: string; onTarget: (value: string) => void; disabled: boolean }) {
  const { api, access, go } = useCreatorWorkspace();
  const { t } = useHomeTranslation();
  const [location, navigate] = useLocation();
  const search = useSearch();
  const params = new URLSearchParams(search);
  const query = new URLSearchParams({ q: params.get("q") ?? "", days: params.get("days") ?? String(defaultDays), state: params.get("state") ?? "", projectType: params.get("projectType") ?? "", offset: params.get("offset") ?? "0", limit: String(limit) }).toString();
  const [page, setPage] = useState<HomePage<RecentProject>>();
  const [error, setError] = useState<unknown>();
  const [loaded, setLoaded] = useState("");
  const [reload, setReload] = useState(0);
  const restored = useRef(false);
  useEffect(() => {
    const abort = new AbortController();
    void api.projects(new URLSearchParams(query), abort.signal).then((value) => { if (!abort.signal.aborted) { if (value.workspaceId !== access.workspaceId) throw new HomeApiError(404, "WORKSPACE_NOT_FOUND"); setPage(value); setError(undefined); setLoaded(query); } }).catch((problem: unknown) => { if (!abort.signal.aborted) { setError(problem); setPage(undefined); } });
    return () => abort.abort();
  }, [api, access.workspaceId, query, reload]);
  useEffect(() => {
    if (restored.current || loaded !== query || !page) return;
    restored.current = true;
    const previous = readHomeReturn(access.actorId, access.workspaceId);
    if (new URLSearchParams(search).toString() !== previous.query) return;
    return restoreHomeScroll(previous.scroll);
  }, [access.actorId, access.workspaceId, loaded, page, query, search]);
  function filter(values: Record<string, string>) { if (disabled) return; const next = new URLSearchParams(query); for (const [key, value] of Object.entries(values)) next.set(key, value); next.delete("limit"); navigate(`${location}?${next}`); }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); filter({ q: searchText(event.currentTarget), offset: "0" }); }
  const waiting = loaded !== query && !error;
  const targetKey = quickActions.find((item) => item.path === target)?.key ?? "control";
  return <section aria-label={t("results")} aria-busy={waiting}>
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">{t("recent")}</h2><button className="text-xs text-orange-200 hover:underline" onClick={() => go("projects")}>{t("allProjects")} →</button></div>
    {target !== "control" && <p role="status" className="mt-4 text-sm text-violet-200">{t("pickProject", { target: t(targetKey) })}<button className="ml-4 text-xs underline" onClick={() => onTarget("control")}>{t("cancel")}</button></p>}
    <form onSubmit={submit} className="my-5 flex flex-wrap gap-2"><input key={params.get("q")} name="q" defaultValue={params.get("q") ?? ""} placeholder={t("search")} aria-label={t("search")} maxLength={255} className={homeField + " min-w-0 flex-1"} disabled={disabled} /><button className={homeButton} disabled={disabled}>{t("searchAction")}</button>
      <select aria-label={t("range")} className={homeField} value={params.get("days") ?? defaultDays} disabled={disabled} onChange={(event) => filter({ days: event.target.value, offset: "0" })}>{[[0, "allTime"], [7, "week"], [30, "month"]].map(([days, key]) => <option key={days} value={days}>{t(String(key))}</option>)}</select>
      <select aria-label={t("state")} className={homeField} value={params.get("state") ?? ""} disabled={disabled} onChange={(event) => filter({ state: event.target.value, offset: "0" })}>{["", "draft", "active", "review", "completed"].map((state) => <option key={state} value={state}>{t(state || "all")}</option>)}</select>
      <select aria-label={t("type")} className={homeField} value={params.get("projectType") ?? ""} disabled={disabled} onChange={(event) => filter({ projectType: event.target.value, offset: "0" })}>{[["", "all"], ["drama", "drama"], ["comic", "comic"], ["brand", "brandType"], ["adaptation", "adaptation"]].map(([type, label]) => <option key={type} value={type}>{t(label)}</option>)}</select>
    </form>
    {error ? <HomeError error={error} retry={() => setReload((value) => value + 1)} /> : waiting ? <p role="status" className="py-12 text-center text-sm text-slate-400">{t("loading")}</p> : page && <>
      <div className="xj-workspace-project-grid">{page.items.map((project) => <ProjectCard key={project.id} project={project} headingAs="h3" openLabel={t(project.setupState === "draft" ? "resume" : "open")} disabled={project.setupState === "draft" && !access.permissions.includes("project.manage")} onOpen={() => go(`projects/${encodeURIComponent(project.id)}/${project.setupState === "draft" ? "guided-create" : target}`)} />)}</div>
      {!page.items.length && <div className="rounded-xl border border-dashed border-white/15 p-8 text-center"><p className="text-slate-300">{t(!params.get("q")?.trim() && !params.get("state") && !params.get("projectType") && !Number(params.get("offset") ?? 0) && !Number(params.get("days") ?? defaultDays) ? "empty" : "noMatches")}</p><button className={homeButton + " mt-4"} onClick={() => filter({ q: "", state: "", projectType: "", offset: "0", days: "0" })}>{t("clear")}</button></div>}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>{t("page", { total: page.total, page: Math.floor(page.offset / page.limit) + 1 })}</span><div className="flex gap-2"><button className={homeButton} disabled={disabled || page.offset === 0} onClick={() => filter({ offset: String(Math.max(0, page.offset - page.limit)) })}>{t("previous")}</button><button className={homeButton} disabled={disabled || page.offset + page.limit >= page.total} onClick={() => filter({ offset: String(page.offset + page.limit) })}>{t("next")}</button></div></div>
    </>}
  </section>;
}

export function WorkspaceTasks({ compact = false }: { compact?: boolean }) {
  const { access, api, go } = useCreatorWorkspace();
  const { t, i18n } = useHomeTranslation();
  const { t: taskText } = useControlTranslation();
  const [page, setPage] = useState<HomePage<WorkspaceTask>>();
  const [error, setError] = useState<unknown>();
  const [query, setQuery] = useState("q=&state=&days=0&offset=0");
  const [loaded, setLoaded] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController(); const params = new URLSearchParams(query); params.set("limit", compact ? "4" : "20");
    void api.tasks(params, abort.signal).then((value) => { if (!abort.signal.aborted) { if (value.workspaceId !== access.workspaceId) throw new HomeApiError(404, "WORKSPACE_NOT_FOUND"); setPage(value); setError(undefined); setLoaded(query); } }).catch((problem: unknown) => { if (!abort.signal.aborted) { setError(problem); setPage(undefined); } });
    return () => abort.abort();
  }, [api, access.workspaceId, compact, query, reload]);
  const filters = new URLSearchParams(query);
  const Heading = compact ? "h2" : "h1";
  function filter(key: string, value: string) { filters.set(key, value); if (key !== "offset") filters.set("offset", "0"); setQuery(filters.toString()); }
  return <section className={compact ? "rounded-2xl border border-white/10 bg-[#11141D] p-5" : "p-5 sm:p-8"} aria-busy={loaded !== query && !error}>
    <div className="flex items-center justify-between gap-3"><Heading className="text-lg font-semibold">{t(compact ? "latestTasks" : "tasks")}</Heading><button className={homeButton} onClick={() => compact ? go("tasks") : setReload((value) => value + 1)}>{compact ? <ArrowRight size={15} /> : <RefreshCw size={15} />}<span className="sr-only">{t(compact ? "tasks" : "refresh")}</span></button></div><p className="mt-3 text-xs leading-6 text-slate-500">{t("taskHint")}</p>
    {!compact && <form className="mt-5 flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); filter("q", searchText(event.currentTarget)); }}><input name="q" aria-label={t("taskSearch")} placeholder={t("taskSearch")} maxLength={255} className={homeField} /><button className={homeButton}>{t("searchAction")}</button><select className={homeField} aria-label={t("taskState")} value={filters.get("state") ?? ""} onChange={(event) => filter("state", event.target.value)}>{["", "queued", "running", "retrying", "cancelling", "succeeded", "failed", "cancelled", "timeout"].map((state) => <option key={state} value={state}>{state ? taskText(`taskState.${state}`) : t("all")}</option>)}</select><select className={homeField} aria-label={t("range")} value={filters.get("days") ?? "0"} onChange={(event) => filter("days", event.target.value)}>{[[0, "allTime"], [7, "week"], [30, "month"]].map(([days, label]) => <option key={days} value={days}>{t(String(label))}</option>)}</select></form>}
    {error ? <div className="mt-4"><HomeError error={error} retry={() => setReload((value) => value + 1)} /></div> : loaded !== query ? <p role="status" className="py-8 text-sm text-slate-400">{t("loading")}</p> : <><ul className="mt-4 divide-y divide-white/10">{page?.items.map((task) => <li key={`${task.projectId}:${task.id}`} className="flex flex-wrap items-center justify-between gap-3 py-4"><div className="min-w-0"><p className="truncate text-sm">{task.projectName}</p><p className="mt-1 break-all font-mono text-[10px] text-slate-500">{task.id}</p><p className="mt-2 text-xs text-slate-400">{taskText(`taskState.${task.status}`, { defaultValue: task.status })} · {new Date(task.updatedAt).toLocaleString(i18n.resolvedLanguage)}</p></div><button className="text-xs text-orange-200 hover:underline" onClick={() => go(`projects/${encodeURIComponent(task.projectId)}/tasks?taskId=${encodeURIComponent(task.id)}`)}>{t("openTasks")} →</button></li>)}</ul>{!page?.items.length && <p className="py-7 text-sm text-slate-500">{t("emptyTasks")}</p>}{!compact && page && <div className="mt-4 flex flex-wrap justify-between gap-3 text-xs text-slate-500"><span>{t("page", { total: page.total, page: Math.floor(page.offset / page.limit) + 1 })}</span><div className="flex gap-2"><button className={homeButton} disabled={page.offset === 0} onClick={() => filter("offset", String(Math.max(0, page.offset - page.limit)))}>{t("previous")}</button><button className={homeButton} disabled={page.offset + page.limit >= page.total} onClick={() => filter("offset", String(page.offset + page.limit))}>{t("next")}</button></div></div>}</>}
  </section>;
}

function Inspiration() {
  const { go } = useCreatorWorkspace(); const { t } = useHomeTranslation();
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const api = useMemo(() => createMarketplaceApi({ accessToken: getTokenForSession(sessionEpoch) ?? undefined }), [sessionEpoch]);
  const [items, setItems] = useState<MarketItem[]>(); const [error, setError] = useState<unknown>(); const [reload, setReload] = useState(0);
  useEffect(() => { let active = true; void api.market().then((page) => { if (active) { setItems(page.items.slice(0, 4)); setError(undefined); } }).catch((problem: unknown) => { if (active) { setError(problem); setItems(undefined); } }); return () => { active = false; }; }, [api, reload]);
  return <section className="rounded-2xl border border-white/10 bg-[#11141D] p-5"><div className="flex items-center justify-between"><h2 className="flex items-center gap-2 text-lg font-semibold"><Sparkles size={18} className="text-violet-300" />{t("inspiration")}</h2><GitFork size={17} className="text-slate-600" /></div><p className="mt-3 text-xs leading-6 text-slate-500">{t("inspirationHint")}</p>{error ? <div className="mt-4"><HomeError error={error} retry={() => setReload((value) => value + 1)} /></div> : !items ? <p role="status" className="py-7 text-sm text-slate-400">{t("loading")}</p> : items.length ? <ul className="mt-4 grid gap-3 sm:grid-cols-2">{items.map((item) => <li key={item.id} className="rounded-xl border border-violet-300/15 bg-violet-300/[0.025] p-4"><p className="text-[10px] text-violet-200">{item.template_kind ?? item.source_kind}</p><h3 className="mt-3 truncate text-sm font-medium" title={item.title}>{item.title}</h3><p className="mt-2 truncate text-xs text-slate-500">{item.tags.join(" / ")}</p></li>)}</ul> : <p className="py-7 text-sm text-slate-500">{t("emptyMarket")}</p>}<button className={homeButton + " mt-4 w-full text-violet-200"} onClick={() => go("templates")}>{t("explore")}<ArrowRight size={15} /></button></section>;
}
