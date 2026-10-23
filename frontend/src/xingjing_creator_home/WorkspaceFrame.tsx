import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { ArrowUpRight, BriefcaseBusiness, ChartNoAxesCombined, Check, CircleDollarSign, Clapperboard, FolderOpen, GitFork, House, Images, ListTodo, Plus, Settings2, Users } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { createHomeApi, HomeApiError, type HomeApi, type WorkspaceAccess } from "./api";
import { homeButton, useHomeTranslation } from "./i18n";
import { readHomeReturn, rememberHome } from "./return-state";

const navigation = [
  { key: "home", path: "home", icon: House, permission: "project.view" },
  { key: "projects", path: "projects", icon: FolderOpen, permission: "project.view" },
  { key: "create", path: "projects/_new/guided-create", icon: Plus, permission: "project.manage" },
  { key: "assets", path: "assets", icon: Images, permission: "asset.view" },
  { key: "templates", path: "templates", icon: GitFork, permission: "template.view" },
  { key: "tasks", path: "tasks", icon: ListTodo, permission: "generation.view" },
  { key: "billing", path: "billing", icon: CircleDollarSign, permission: "billing.view" },
  { key: "commercial", path: "commercial", icon: BriefcaseBusiness, permission: "commercial.view" },
  { key: "team", path: "team", icon: Users, permission: "workspace.view" },
  { key: "analytics", path: "", icon: ChartNoAxesCombined, permission: "" },
  { key: "settings", path: "settings", icon: Settings2, permission: "settings.view" },
];
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
    const query = access && path === "home" ? readHomeReturn(access.actorId, workspaceId).query : "";
    navigate(`${base}/${path}${query ? `?${query}` : ""}`);
  }
  if (error || !access) return <main className="grid min-h-screen place-content-center gap-4 bg-[#05060A] px-6 text-slate-200" aria-busy={!error}>
    <h1 className="text-xl">{t(error ? error instanceof HomeApiError && error.status === 404 ? "scopeError" : error instanceof HomeApiError && error.status === 403 ? "forbidden" : "failed" : "loading")}</h1>
    {Boolean(error) && <div className="flex gap-3"><button className={homeButton} onClick={() => setReload((value) => value + 1)}>{t("retry")}</button><Link className={homeButton} href="/workspace-select">{t("workspace")}</Link></div>}
  </main>;
  return <Context.Provider value={{ access, api, token, base, go, register }}>
    <div className="min-h-screen bg-[#05060A] text-[#F5F7FB] [overflow-anchor:none] lg:grid lg:grid-cols-[208px_minmax(0,1fr)]">
      <aside className="border-b border-white/10 bg-[#090B12] p-4 lg:sticky lg:top-0 lg:h-screen lg:border-r lg:border-b-0">
        <button onClick={() => go("home")} className="flex items-center gap-3 rounded-lg text-lg font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400"><span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-orange-400"><Clapperboard size={21} /></span>{t("brand")}</button>
        <p className="mt-5 truncate text-xs text-slate-500" title={access.workspaceName}>{access.workspaceName}</p>
        <nav aria-label={t("navigation")} className="mt-3 flex gap-1 overflow-x-auto pb-2 lg:flex-col lg:overflow-visible">
          {navigation.map(({ key, path, icon: Icon, permission }) => {
            const available = Boolean(path) && access.permissions.includes(permission);
            const reason = !path ? t("unavailable") : t("forbidden");
            return <button key={key} aria-current={active === key ? "page" : undefined} disabled={!available} title={!available ? reason : t(key)} onClick={() => go(path)} className={`flex shrink-0 items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 disabled:cursor-not-allowed disabled:opacity-35 ${active === key ? "bg-orange-400/10 text-orange-200" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}><Icon size={17} />{t(key)}{active === key && <Check size={12} className="ml-auto hidden lg:block" />}</button>;
          })}
        </nav>
        <button className={homeButton + " mt-2 w-full text-slate-400 lg:absolute lg:bottom-5 lg:w-[176px]"} onClick={() => { if (canLeave()) navigate("/workspace-select"); }}>{t("workspace")}<ArrowUpRight size={14} /></button>
      </aside>
      <div className="min-w-0">{blocked && <p role="status" className="border-b border-amber-300/20 bg-amber-300/5 px-6 py-3 text-sm text-amber-200">{t("unsaved")}</p>}{children}</div>
    </div>
  </Context.Provider>;
}
