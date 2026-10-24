import { useEffect, useState, type ReactNode } from "react";
import { ArrowUpRight, BriefcaseBusiness, ChartNoAxesCombined, FolderOpen, GitFork, House, Images, ListTodo, Menu, Plus, Search, Settings2, Users, Wallet, X } from "lucide-react";
import { useHomeTranslation } from "./i18n";
import "./creator-shell.css";

const navigation = [
  { key: "home", path: "home", icon: House, permission: "project.view" },
  { key: "projects", path: "projects", icon: FolderOpen, permission: "project.view" },
  { key: "assets", path: "assets", icon: Images, permission: "asset.view" },
  { key: "templates", path: "templates", icon: GitFork, permission: "template.view" },
  { key: "tasks", path: "tasks", icon: ListTodo, permission: "generation.view" },
  { key: "billing", path: "billing", icon: Wallet, permission: "billing.view" },
  { key: "commercial", path: "commercial", icon: BriefcaseBusiness, permission: "commercial.view" },
  { key: "team", path: "team", icon: Users, permission: "workspace.view" },
  { key: "analytics", path: "analytics", icon: ChartNoAxesCombined, permission: "analytics.view" },
  { key: "settings", path: "settings", icon: Settings2, permission: "settings.view" },
];

/** Presentation only: the route controller owns scope, permissions and navigation guards. */
export function CreatorShell({ active, title, workspaceLabel, permissions = [], notifications, onNavigate, onWorkspace, children }: {
  active: string; title?: string; workspaceLabel?: string; permissions?: string[];
  notifications?: ReactNode;
  onNavigate: (path: string) => void; onWorkspace: () => void; children: ReactNode;
}) {
  const { t } = useHomeTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const previous = document.title;
    document.title = `${title ?? t(active)} · ${t("brand")}`;
    return () => { document.title = previous; };
  }, [active, title, t]);
  const can = (permission: string) => permissions.includes(permission);
  const navigate = (path: string) => { onNavigate(path); setMenuOpen(false); };
  return <div className="xj-creator-shell">
    <aside id="creator-navigation" className={`xj-creator-sidebar ${menuOpen ? "is-open" : ""}`}>
      <button className="xj-creator-brand" title={t("brand")} aria-label={t("brand")} onClick={() => navigate("home")}>
        <img className="xj-creator-brand-mark" src="/xingjing-mark.svg" alt="" aria-hidden="true" width="40" height="40" />
        <span className="xj-creator-brand-copy"><strong>{t("brand")}</strong><small>{t("subtitle")}</small></span>
      </button>
      <p className="xj-creator-nav-label">{t("navigation")}</p>
      <nav aria-label={t("navigation")} className="xj-creator-nav">
        {navigation.map(({ key, path, icon: Icon, permission }) => <button key={key} disabled={!can(permission)} aria-label={t(key)} title={t(can(permission) ? key : "forbidden")} aria-current={active === key ? "page" : undefined} onClick={() => navigate(path)} className="xj-creator-nav-item">
          <Icon size={17} aria-hidden="true" /><span>{t(key)}</span>
        </button>)}
      </nav>
      <button className="xj-creator-workspace" onClick={onWorkspace} title={t("workspace")} aria-label={t("workspace")}>
        <span className="xj-creator-workspace-avatar"><Users size={17} aria-hidden="true" /></span>
        <span className="xj-creator-workspace-copy"><small>{t("workspaceLabel")}</small><strong>{workspaceLabel ?? t("workspace")}</strong></span><ArrowUpRight size={14} aria-hidden="true" />
      </button>
    </aside>
    <div className="xj-creator-body">
      <header className="xj-creator-topbar">
        <button className="xj-shell-button xj-creator-menu" aria-label={t(menuOpen ? "closeNavigation" : "openNavigation")} aria-expanded={menuOpen} aria-controls="creator-navigation" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={18} /> : <Menu size={18} />}</button>
        <div className="xj-creator-heading"><span className="xj-creator-context">{title ?? t(active)}</span><span className="xj-creator-heading-detail" title={workspaceLabel}>{workspaceLabel ?? t("subtitle")}</span></div>
        <form className="xj-creator-search" role="search" onSubmit={(event) => { event.preventDefault(); navigate(`home?q=${encodeURIComponent(query.trim())}&days=0&offset=0`); }}>
          <Search size={16} aria-hidden="true" /><input aria-label={t("search")} placeholder={t("search")} value={query} maxLength={255} onChange={(event) => setQuery(event.target.value)} disabled={!can("project.view")} /><button type="submit" className="sr-only" disabled={!can("project.view")}>{t("searchAction")}</button>
        </form>
        <div className="xj-creator-top-actions">{notifications}<button className="xj-shell-button xj-creator-task-action" disabled={!can("generation.view")} onClick={() => navigate("tasks")}>{t("tasks")}</button><button className="xj-shell-button xj-shell-button--primary" disabled={!can("project.manage")} onClick={() => navigate("projects/_new/guided-create")}><Plus size={15} aria-hidden="true" />{t("create")}</button></div>
      </header>
      <div className="xj-creator-content">{children}</div>
    </div>
  </div>;
}
