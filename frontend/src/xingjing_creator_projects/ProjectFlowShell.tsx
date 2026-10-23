import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, ChevronRight, LockKeyhole, RefreshCw } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { CreatorApiError } from "./api";
import type { ProjectControl, ProjectControlApi } from "./control-api";
import { useControlTranslation } from "./control-i18n";

export const controlButton = "inline-flex items-center justify-center gap-2 rounded-lg border border-[#2A2E3A] px-3 py-2 text-sm transition hover:border-[#FF6B4A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 disabled:cursor-not-allowed disabled:opacity-40";
const FlowContext = createContext<{ data: ProjectControl; loading: boolean; refresh: () => void; go: (target: string, episode?: number | null) => void; registerGuard: (guard: () => boolean) => () => void } | null>(null);
export function useProjectNavigationBlocker(blocked: boolean) {
  const register = useContext(FlowContext)?.registerGuard;
  useEffect(() => register?.(() => !blocked), [register, blocked]);
}
export function useProjectFlow() {
  const context = useContext(FlowContext);
  if (!context) throw new Error("Project flow context is required");
  return context;
}

function activeStage(slug: string) {
  if (["control", "episodes", "episode-split-editor"].includes(slug)) return "control";
  if (["script-import", "script-lab", "source", "rewrite", "versions", "source-mapping"].includes(slug)) return "script";
  if (slug === "script-checkup") return "checkup";
  if (slug === "ai-director") return "director";
  if (slug.includes("asset") || slug === "prompts") return "assets";
  if (slug.startsWith("shots")) return "shots";
  if (slug === "storyboard") return "storyboard";
  if (["generation", "tasks"].includes(slug)) return "generation";
  if (/audio|subtitle|lip-sync|bgm-sfx/.test(slug)) return "audio";
  if (/postproduction|editing|final-|timeline|watermark/.test(slug)) return "editing";
  if (slug === "collaboration") return "review";
  if (/export|release|jianying|premiere|davinci/.test(slug)) return "export";
  if (/compliance|rights|provenance|aigc|authorization|risk|sensitive|likeness|ip-rights/.test(slug)) return "compliance";
  return "control";
}

export function ProjectFlowShell({ api, workspaceId, projectId, slug, itemId, children }: {
  api: ProjectControlApi; workspaceId: string; projectId: string; slug: string; itemId?: string; children: ReactNode;
}) {
  const { t } = useControlTranslation();
  const [location, navigate] = useLocation();
  const search = useSearch();
  const [data, setData] = useState<ProjectControl>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const [loadedKey, setLoadedKey] = useState("");
  const [reload, setReload] = useState(0);
  const [gate, setGate] = useState<string>();
  const [navigationBlocked, setNavigationBlocked] = useState(false);
  const guards = useRef(new Set<() => boolean>());
  const registerGuard = useCallback((guard: () => boolean) => {
    guards.current.add(guard);
    return () => {
      guards.current.delete(guard);
      if ([...guards.current].every((check) => check())) setNavigationBlocked(false);
    };
  }, []);
  const params = new URLSearchParams(search);
  const episodeId = params.get("episodeId");
  const objectId = (slug !== "generation" && slug !== "collaboration" ? itemId : undefined) ?? params.get("projectVersion") ?? params.get("timelineId") ?? params.get("taskId");
  const filters = slug === "control" ? new URLSearchParams({ days: params.get("days") ?? "0", q: params.get("q") ?? "", episodeState: params.get("episodeState") ?? "", offset: params.get("offset") ?? "0" }).toString() : "";
  const requestKey = `${projectId}:${filters}:${slug}:${itemId ?? ""}:${reload}`;
  useEffect(() => {
    const abort = new AbortController();
    const query = new URLSearchParams(filters);
    void api.get(projectId, { days: Number(query.get("days") ?? 0), q: query.get("q") ?? "", episodeState: query.get("episodeState") ?? "", offset: Number(query.get("offset") ?? 0) }, abort.signal)
      .then((result) => { if (!abort.signal.aborted) { setData(result); setError(undefined); setLoading(false); setLoadedKey(requestKey); } })
      .catch((problem: unknown) => { if (!abort.signal.aborted) { setError(problem); setLoading(false); } });
    return () => abort.abort();
  }, [api, projectId, filters, requestKey]);
  const refresh = () => { setLoading(true); setReload((value) => value + 1); };
  const apiError = error instanceof CreatorApiError ? error : undefined;
  const validData = !error && data?.project.id === projectId && data.workspaceId === workspaceId ? data : undefined;
  const episode = validData?.episodes.find((e) => String(e.episode) === episodeId);
  const invalidEpisode = Boolean(episodeId && validData && !episode);
  const base = `/app/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}`;
  const filterKey = `control:${workspaceId}:${projectId}:filters`;
  function canLeave() {
    const allowed = [...guards.current].every((guard) => guard());
    setNavigationBlocked(!allowed);
    return allowed;
  }
  function go(target: string, number?: number | null) {
    if (!canLeave()) return;
    const [path, supplied = ""] = target.split("?");
    const query = new URLSearchParams(supplied);
    if (slug === "control") sessionStorage.setItem(filterKey, filters);
    if (path === "control" && !supplied) {
      for (const [key, value] of new URLSearchParams(sessionStorage.getItem(filterKey) ?? "")) query.set(key, value);
    }
    const selected = number === null ? undefined : number === undefined ? episode : validData?.episodes.find((e) => e.episode === number);
    if (selected && !query.has("episodeId")) query.set("episodeId", String(selected.episode));
    if (/^(release|export|compliance|rights|provenance|aigc)/.test(path) && selected?.finalVersionId) {
      if (!query.has("projectVersion")) query.set("projectVersion", selected.finalVersionId);
      if (!query.has("target")) query.set("target", validData?.project.targetPlatform ?? "douyin");
    }
    if (path === "postproduction" && selected?.timelineId) query.set("timelineId", selected.timelineId);
    setGate(undefined);
    navigate(`${base}/${path}${query.size ? `?${query}` : ""}`);
  }
  const active = activeStage(slug);
  const stages = validData?.stages.map((stage) => {
    if (stage.reason || !episode) return stage;
    if (["review", "export", "compliance"].includes(stage.id) && !episode.finalVersionId) return { ...stage, reason: "finals", recovery: "postproduction" };
    if (stage.id === "shots" && !episode.assetsReady && !episode.shots) return { ...stage, reason: "assets", recovery: "episodes" };
    if (stage.id === "storyboard" && !episode.shots) return { ...stage, reason: "shots", recovery: "shots" };
    return stage;
  }) ?? [];
  const selectedGate = stages.find((s) => s.id === gate && s.reason);
  const pageGate = stages.find((s) => s.id === active && s.reason);
  return <div className="min-h-screen bg-[#07080C] text-[#F5F6FA]">
    <header className="border-b border-[#2A2E3A] bg-[#101218] px-4 py-4 md:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav aria-label={t("context")} className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
          <button className={controlButton} onClick={() => { if (canLeave()) navigate(`/app/workspaces/${encodeURIComponent(workspaceId)}/projects`); }}><ArrowLeft size={14} />{t("library")}</button>
          <span className="max-w-40 truncate text-xs text-slate-500" title={workspaceId}>{workspaceId}</span>
          <ChevronRight size={14} className="text-slate-600" /><button className="max-w-72 truncate font-semibold hover:text-orange-300" onClick={() => go("control")}>{validData?.project.name ?? t("title")}</button>
          {objectId && <span className="max-w-48 truncate font-mono text-xs text-slate-400" title={objectId}>{t("object")} · {objectId}</span>}
        </nav>
        {validData && <label className="flex items-center gap-3 text-xs text-slate-400">{t("context")}<select aria-label={t("context")} value={episodeId ?? ""} className="max-w-64 rounded-lg border border-[#2A2E3A] bg-[#171922] p-2 text-sm text-slate-100" onChange={(event) => {
          const target = slug === "generation" && itemId ? `${slug}/${itemId}` : slug === "collaboration" && itemId ? `${slug}/${itemId}` : slug;
          go(target, event.target.value ? Number(event.target.value) : null);
        }}><option value="">{t("allEpisodes")}</option>{invalidEpisode && <option value={episodeId ?? ""}>{t("invalidEpisode")}</option>}{validData.episodes.map((e) => <option key={e.episode} value={e.episode}>{t("episode", { number: e.episode })} · {e.title}</option>)}</select></label>}
      </div>
      {validData && <nav aria-label={t("flow")} className="mt-4 flex flex-wrap gap-1">{stages.map((stage, index) => <button key={stage.id} aria-current={stage.id === active ? "step" : undefined} aria-disabled={Boolean(stage.reason)} title={stage.reason ? t(`gate.${stage.reason}`) : t(`stage.${stage.id}`)} onClick={() => stage.reason ? setGate(stage.id) : go(stage.target)} className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 ${stage.id === active ? "bg-[#FF6B4A] font-semibold text-white" : stage.reason ? "text-slate-500 hover:bg-white/5" : "text-slate-300 hover:bg-white/10"}`}>
        <span className="font-mono opacity-60">{String(index + 1).padStart(2, "0")}</span>{t(`stage.${stage.id}`)}{stage.reason && <LockKeyhole size={11} />}
      </button>)}</nav>}
      {selectedGate && <div role="status" className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-amber-600/30 bg-amber-500/5 p-3 text-sm text-amber-200"><span>{t(`stage.${selectedGate.id}`)} · {t(`gate.${selectedGate.reason}`)}</span>{selectedGate.recovery && <button className={controlButton} onClick={() => go(selectedGate.recovery!)}>{t("recovery")}<ChevronRight size={14} /></button>}</div>}
      {navigationBlocked && <p role="status" className="mt-3 text-sm text-amber-200">{t("saveBeforeNavigate")}</p>}
    </header>
    {error ? <section role="alert" className="m-6 rounded-xl border border-red-400/30 p-6"><h1>{t("failed")}</h1><p className="my-3 text-sm text-slate-400">{t(apiError?.status === 403 ? "forbidden" : apiError?.status === 404 ? "missing" : "notSaved")}</p>{apiError?.requestId && <p className="mb-3 font-mono text-xs">{apiError.requestId}</p>}<button className={controlButton} onClick={refresh}><RefreshCw size={14} />{t("retry")}</button></section>
      : data?.project.id === projectId && data.workspaceId !== workspaceId ? <section role="alert" className="m-6 p-6"><p>{t("missing")}</p><button className={controlButton + " mt-4"} onClick={() => navigate(`/app/workspaces/${encodeURIComponent(data.workspaceId)}/projects`)}>{t("library")}</button></section>
      : !validData ? <p role="status" className="p-12 text-center text-slate-400">{t("loading")}</p>
      : invalidEpisode ? <section role="alert" className="m-6 p-6"><p>{t("invalidEpisode")}</p><button className={controlButton + " mt-4"} onClick={() => go("control", null)}>{t("title")}</button></section>
      : pageGate ? <section role="status" className="m-6 rounded-xl border border-amber-400/30 p-6"><h1 className="text-lg">{t(`stage.${pageGate.id}`)}</h1><p className="my-4 text-sm text-amber-200">{t(`gate.${pageGate.reason}`)}</p>{pageGate.recovery && <button className={controlButton} onClick={() => go(pageGate.recovery!)}>{t("recovery")}</button>}</section>
      : <FlowContext.Provider value={{ data: validData, loading: loading || loadedKey !== requestKey, refresh, go, registerGuard }}><div key={`${projectId}:${location}:${episodeId ?? ""}:${params.get("timelineId") ?? ""}`}>{["generation", "audio", "audio-subtitle"].includes(slug) && <p className="px-6 pt-4 text-xs text-slate-400">{t("projectWide")}</p>}{children}</div></FlowContext.Provider>}
  </div>;
}
