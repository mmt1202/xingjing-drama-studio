import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { useAuthStore } from "@/stores/auth-store";
import { createHomeApi, HomeApiError, type HomeApi, type WorkspaceAccess } from "./api";
import { homeButton, useHomeTranslation } from "./i18n";
import { readHomeReturn, rememberHome } from "./return-state";
import { readAnalyticsReturn, rememberAnalytics } from "@/xingjing_analytics/return-state";
import { CreatorShell } from "./CreatorShell";
const Context = createContext<{ access: WorkspaceAccess; api: HomeApi; token: string | null; base: string; go: (path: string) => void; register: (guard: () => boolean) => () => void } | null>(null);
export function useCreatorWorkspace() {
  const value = useContext(Context);
  if (!value) throw new Error("Creator workspace is required");
  return value;
}
export function useWorkspaceNavigationGuard(blocked: boolean) {
  const register = useContext(Context)?.register;
  useEffect(() => register?.(() => !blocked), [register, blocked]);
  useEffect(() => {
    if (!blocked) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [blocked]);
}
export function CreatorWorkspaceFrame({ workspaceId, active, children }: { workspaceId: string; active: string; children: ReactNode }) {
  const token = useAuthStore((state) => state.token);
  return <WorkspaceFrameContent key={`${workspaceId}:${token}`} workspaceId={workspaceId} token={token} active={active}>{children}</WorkspaceFrameContent>;
}
function WorkspaceFrameContent({ workspaceId, token, active, children }: { workspaceId: string; token: string | null; active: string; children: ReactNode }) {
  const { t } = useHomeTranslation();
  const [location, navigate] = useLocation();
  const search = useSearch();
  const api = useMemo(() => createHomeApi(workspaceId, token), [workspaceId, token]);
  const [access, setAccess] = useState<WorkspaceAccess>();
  const [error, setError] = useState<unknown>();
  const [reload, setReload] = useState(0);
  const [blocked, setBlocked] = useState(false);
  const guards = useRef(new Set<() => boolean>());
  const base = `/app/workspaces/${encodeURIComponent(workspaceId)}`;
  const register = useCallback((guard: () => boolean) => {
    guards.current.add(guard);
    return () => { guards.current.delete(guard); if ([...guards.current].every((check) => check())) setBlocked(false); };
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    void api.context(abort.signal).then((value) => { if (!abort.signal.aborted) { setAccess(value); setError(undefined); } }).catch((problem: unknown) => { if (!abort.signal.aborted) { setError(problem); setAccess(undefined); } });
    return () => abort.abort();
  }, [api, reload]);
  function canLeave() { const allowed = [...guards.current].every((check) => check()); setBlocked(!allowed); return allowed; }
  function go(path: string) {
    if (!canLeave()) return;
    if (access && location === `${base}/home`) rememberHome(access.actorId, workspaceId, search);
    if (access && location === `${base}/analytics`) rememberAnalytics(access.actorId, workspaceId, search);
    const query = access && path === "home" ? readHomeReturn(access.actorId, workspaceId).query : access && path === "analytics" ? readAnalyticsReturn(access.actorId, workspaceId).query : "";
    navigate(`${base}/${path}${query ? `?${query}` : ""}`);
  }
  if (error || !access) return <main className="grid min-h-screen place-content-center gap-4 bg-[#05060A] px-6 text-slate-200" aria-busy={!error}>
    <h1 className="text-xl">{t(error ? error instanceof HomeApiError && error.status === 404 ? "scopeError" : error instanceof HomeApiError && error.status === 403 ? "forbidden" : "failed" : "loading")}</h1>
    {Boolean(error) && <div className="flex gap-3"><button className={homeButton} onClick={() => setReload((value) => value + 1)}>{t("retry")}</button><Link className={homeButton} href="/workspace-select">{t("workspace")}</Link></div>}
  </main>;
  return <Context.Provider value={{ access, api, token, base, go, register }}>
    <CreatorShell active={active} compact={active === "home"} workspaceLabel={access.workspaceName} permissions={access.permissions} onNavigate={go} onWorkspace={() => { if (canLeave()) navigate("/workspace-select"); }}>
      {blocked && <p role="status" className="border-b border-amber-300/20 bg-amber-300/5 px-6 py-3 text-sm text-amber-200">{t("unsaved")}</p>}{children}
    </CreatorShell>
  </Context.Provider>;
}
