import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, ChevronRight, Film, LockKeyhole, RefreshCw } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { CreatorShell } from "@/xingjing_creator_home/CreatorShell";
import { NotificationCenter } from "@/xingjing_notifications/NotificationCenter";
import { createHomeApi, type HomeApi, type WorkspaceAccess } from "@/xingjing_creator_home/api";
import { readHomeReturn } from "@/xingjing_creator_home/return-state";
import { readAnalyticsReturn } from "@/xingjing_analytics/return-state";
import { readVersionReturn } from "@/xingjing_versions/return-state";
import { useAuthStore } from "@/stores/auth-store";
import { getTokenForSession } from "@/utils/auth";
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
  if (slug === "version-history") return "versions";
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
  if (/export|release|cover|jianying|premiere|davinci/.test(slug)) return "export";
  if (/compliance|rights|provenance|aigc|authorization|risk|sensitive|likeness|ip-rights/.test(slug)) return "compliance";
  return "control";
}

export function ProjectFlowShell({ api, workspaceId, projectId, slug, itemId, children }: {
  api: ProjectControlApi; workspaceId: string; projectId: string; slug: string; itemId?: string; children: ReactNode;
}) {
  const { t } = useControlTranslation();
  const [location, navigate] = useLocation();
  const search = useSearch();
  const token = useAuthStore((state) => state.token);
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const workspaceApi = useMemo(() => createHomeApi(workspaceId, getTokenForSession(sessionEpoch)), [workspaceId, sessionEpoch]);
  const [workspaceContext, setWorkspaceContext] = useState<{ api: HomeApi; access?: WorkspaceAccess; failed?: boolean }>();
  const [workspaceReload, setWorkspaceReload] = useState(0);
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
    void workspaceApi.context(abort.signal).then((access) => {
      if (!abort.signal.aborted) setWorkspaceContext({ api: workspaceApi, access });
    }).catch(() => {
      if (!abort.signal.aborted) setWorkspaceContext({ api: workspaceApi, failed: true });
    });
    return () => abort.abort();
  }, [workspaceApi, workspaceReload]);
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
  const workspaceBase = `/app/workspaces/${encodeURIComponent(workspaceId)}`;
  const workspaceAccess = workspaceContext?.api === workspaceApi ? workspaceContext.access : undefined;
  const workspaceUnavailable = workspaceContext?.api === workspaceApi && workspaceContext.failed;
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
    let restoringVersionReturn = false;
    if (path === "version-history" && slug !== "version-history" && number === undefined && !supplied && workspaceAccess) {
      const previous = readVersionReturn(workspaceAccess.actorId, workspaceId, projectId);
      restoringVersionReturn = previous.present;
      for (const [key, value] of new URLSearchParams(previous.query)) query.set(key, value);
    }
    if (slug === "control") sessionStorage.setItem(filterKey, filters);
    if (path === "control" && !supplied) {
      for (const [key, value] of new URLSearchParams(sessionStorage.getItem(filterKey) ?? "")) query.set(key, value);
    }
    const selected = number === null ? undefined : number === undefined ? episode : validData?.episodes.find((e) => e.episode === number);
    if (selected && !query.has("episodeId") && !restoringVersionReturn) query.set("episodeId", String(selected.episode));
    if (/^(release|export|cover|compliance|rights|provenance|aigc)/.test(path) && selected?.finalVersionId) {
      if (!query.has("projectVersion")) query.set("projectVersion", selected.finalVersionId);
      if (!query.has("target")) query.set("target", validData?.project.targetPlatform ?? "douyin");
    }
    if (path === "postproduction" && selected?.timelineId) query.set("timelineId", selected.timelineId);
    setGate(undefined);
    navigate(`${base}/${path}${query.size ? `?${query}` : ""}`);
  }
  function goWorkspace(target: string) {
    if (!canLeave() || !workspaceAccess) return;
    const [path, supplied = ""] = target.split("?");
    const query = supplied || (path === "home" ? readHomeReturn(workspaceAccess.actorId, workspaceId).query : path === "analytics" ? readAnalyticsReturn(workspaceAccess.actorId, workspaceId).query : "");
    navigate(`${workspaceBase}/${path}${query ? `?${query}` : ""}`);
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
  const isCover = slug === "cover-generate" || slug === "cover-title-ab-test";
  const coverReason = !validData ? null
    : !["project.view", "final.view", "settings.view"].every((permission) => validData.permissions.includes(permission)) ? "permission"
      : validData.project.archived ? "archived"
        : validData.project.setupState && validData.project.setupState !== "ready" ? "setup"
          : episode ? !episode.finalVersionId ? "finals" : null
            : !validData.episodes.some((item) => item.finalVersionId) ? "finals" : null;
  const pageGate = isCover ? coverReason ? { id: "export", reason: coverReason, recovery: coverReason === "setup" ? "guided-create" : coverReason === "finals" ? "postproduction" : null } : undefined
    : stages.find((s) => s.id === active && s.reason);
  return <CreatorShell active="projects" title={t(`stage.${active}`)} workspaceLabel={workspaceAccess?.workspaceName ?? workspaceId} permissions={workspaceAccess?.permissions ?? []} notifications={workspaceAccess ? <NotificationCenter workspaceId={workspaceId} actorId={workspaceAccess.actorId} token={token} permissions={workspaceAccess.permissions} onNavigate={goWorkspace} registerNavigationGuard={registerGuard} /> : undefined} onNavigate={goWorkspace} onWorkspace={() => { if (canLeave()) navigate("/workspace-select"); }}>
    <div className="xj-project-layout">
      <section className="xj-project-card" aria-label={t("projectIdentity")}>
        {validData ? <>
          <div className="xj-project-identity">
            <div className="flex min-w-0 flex-1 items-start gap-4">
              <div className="xj-project-poster" aria-hidden="true"><Film size={23} className="text-violet-200" /></div>
              <div className="min-w-0 flex-1">
                <button className="max-w-full truncate text-left text-lg font-semibold text-white hover:text-violet-200" title={validData.project.name} onClick={() => go("control")}>{validData.project.name}</button>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400"><span className="rounded-full border border-white/10 bg-white/[.03] px-3 py-1">{t(`projectType.${validData.project.type}`, { defaultValue: validData.project.type })}</span><span className="rounded-full border border-white/10 bg-white/[.03] px-3 py-1">{t("planned", { count: validData.plannedCount })}</span><span>{t("productionState")}: {t(`status.${validData.project.archived ? "archived" : validData.project.productionStatus}`, { defaultValue: validData.project.productionStatus })}</span></div>
                <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-[11px] leading-5 text-slate-500">{[["projectId", validData.project.id], ["target", validData.project.targetPlatform], ["owner", validData.project.ownerName], ["version", validData.project.version]].map(([label, value]) => <div key={label} className="flex min-w-0 max-w-full gap-2"><dt>{t(String(label))}</dt><dd className="min-w-0 truncate text-slate-300" title={String(value)}>{value}</dd></div>)}</dl>
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-start gap-3 sm:items-end">
              <div className="text-left sm:text-right"><p className="text-[11px] text-slate-500">{t("currentStage")}</p><span className="mt-2 inline-flex rounded-full border border-violet-300/20 bg-gradient-to-r from-violet-500/30 to-orange-400/15 px-4 py-2 text-sm text-white">{t(`stage.${active}`)}</span></div>
              <button className="xj-shell-button" disabled={!workspaceAccess?.permissions.includes("project.view")} onClick={() => goWorkspace("projects")}><ArrowLeft size={14} />{t("library")}</button>
            </div>
          </div>
          <div className="xj-project-flow">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><p className="text-[11px] text-slate-500">{t("flow")}</p><label className="flex max-w-full items-center gap-3 text-xs text-slate-400"><span className="shrink-0">{t("context")}</span><select aria-label={t("context")} value={episodeId ?? ""} className="min-w-0 max-w-72 rounded-xl border border-white/10 bg-[#171B27] px-3 py-2 text-sm text-slate-200" onChange={(event) => {
              const target = slug === "generation" && itemId ? `${slug}/${itemId}` : slug === "collaboration" && itemId ? `${slug}/${itemId}` : slug;
              go(target, event.target.value ? Number(event.target.value) : null);
            }}><option value="">{t("allEpisodes")}</option>{invalidEpisode ? <option value={episodeId ?? ""}>{t("invalidEpisode")}</option> : null}{validData.episodes.map((item) => <option key={item.episode} value={item.episode}>{t("episode", { number: item.episode })} · {item.title}</option>)}</select></label></div>
            <nav aria-label={t("flow")} className="flex flex-wrap gap-2">{stages.map((stage) => <button key={stage.id} className="xj-project-stage" aria-current={stage.id === active ? "step" : undefined} aria-disabled={Boolean(stage.reason)} title={stage.reason ? t(`gate.${stage.reason}`) : t(`stage.${stage.id}`)} onClick={() => stage.reason ? setGate(stage.id) : go(stage.target)}>{t(`stage.${stage.id}`)}{stage.reason ? <LockKeyhole size={12} /> : null}</button>)}</nav>
            {objectId ? <p className="mt-3 truncate text-[11px] text-slate-500" title={objectId}>{t("object")}: {objectId}</p> : null}
            {selectedGate ? <div role="status" className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-amber-600/30 bg-amber-500/5 p-3 text-sm text-amber-200"><span>{t(`stage.${selectedGate.id}`)} · {t(`gate.${selectedGate.reason}`)}</span>{selectedGate.recovery ? <button className={controlButton} onClick={() => go(selectedGate.recovery!)}>{t("recovery")}<ChevronRight size={14} /></button> : null}</div> : null}
          </div>
        </> : <p className="px-6 py-5 text-sm text-slate-500">{t(error ? "projectIdentity" : "loading")}</p>}
      </section>
      {workspaceUnavailable ? <p role="status" className="mt-4 text-xs leading-6 text-amber-200">{t("workspaceNavigationUnavailable")} <button className="underline underline-offset-4" onClick={() => setWorkspaceReload((value) => value + 1)}>{t("retry")}</button></p> : null}
      {navigationBlocked ? <p role="status" className="mt-4 rounded-xl border border-amber-500/20 bg-amber-500/[.04] p-3 text-sm text-amber-200">{t("saveBeforeNavigate")}</p> : null}
      <div className="xj-project-surface">
        {error ? <section role="alert" className="rounded-xl border border-red-400/30 p-6"><h1>{t("failed")}</h1><p className="my-3 text-sm text-slate-400">{t(apiError?.status === 403 ? "forbidden" : apiError?.status === 404 ? "missing" : "notSaved")}</p>{apiError?.requestId ? <p className="mb-3 font-mono text-xs">{apiError.requestId}</p> : null}<button className={controlButton} onClick={refresh}><RefreshCw size={14} />{t("retry")}</button></section>
          : data?.project.id === projectId && data.workspaceId !== workspaceId ? <section role="alert" className="p-6"><p>{t("missing")}</p><button className={controlButton + " mt-4"} disabled={!workspaceAccess?.permissions.includes("project.view")} onClick={() => goWorkspace("projects")}>{t("library")}</button></section>
          : !validData ? <p role="status" className="p-12 text-center text-slate-400">{t("loading")}</p>
          : invalidEpisode ? <section role="alert" className="p-6"><p>{t("invalidEpisode")}</p><button className={controlButton + " mt-4"} onClick={() => go("control", null)}>{t("title")}</button></section>
          : pageGate ? <section role="status" className="rounded-xl border border-amber-400/30 p-6"><h1 className="text-lg">{t(`stage.${pageGate.id}`)}</h1><p className="my-4 text-sm text-amber-200">{t(`gate.${pageGate.reason}`)}</p>{pageGate.recovery ? <button className={controlButton} onClick={() => go(pageGate.recovery!)}>{t("recovery")}</button> : null}</section>
          : <FlowContext.Provider value={{ data: validData, loading: loading || loadedKey !== requestKey, refresh, go, registerGuard }}><div key={`${projectId}:${location}:${episodeId ?? ""}:${params.get("timelineId") ?? ""}`}>{["generation", "audio", "audio-subtitle"].includes(slug) ? <p className="mb-4 text-xs leading-6 text-slate-400">{t("projectWide")}</p> : null}{children}</div></FlowContext.Provider>}
      </div>
    </div>
  </CreatorShell>;
}
