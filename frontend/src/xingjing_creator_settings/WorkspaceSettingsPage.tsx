import { useCallback, useState } from "react";
import { useLocation } from "wouter";
import { ArrowUpRight, ChartNoAxesCombined, House, LockKeyhole, Settings2, ShieldCheck, UserRound } from "lucide-react";
import { useCreatorWorkspace } from "@/xingjing_creator_home/WorkspaceFrame";
import type { PreferenceApi } from "./api";
import { useSettingsTranslation } from "./i18n";
import { PreferenceGroup } from "./PreferenceGroup";
import type { SettingsResource } from "./preference-state";
import "./settings.css";

export function WorkspaceSettingsPage({ api }: { api: PreferenceApi }) {
  const { access, go } = useCreatorWorkspace();
  const { t } = useSettingsTranslation();
  const [, navigate] = useLocation();
  const [sections, setSections] = useState<Record<SettingsResource, { dirty: boolean; pending: boolean }>>({ "creator-home": { dirty: false, pending: false }, "data-dashboard": { dirty: false, pending: false } });
  const [leaveWarning, setLeaveWarning] = useState(false);
  const onState = useCallback((resource: SettingsResource, dirty: boolean, pending: boolean) => {
    setSections((current) => current[resource].dirty === dirty && current[resource].pending === pending ? current : { ...current, [resource]: { dirty, pending } });
  }, []);
  const dirty = Object.values(sections).some((section) => section.dirty);
  const pending = Object.values(sections).some((section) => section.pending);
  const can = (permission: string) => access.permissions.includes(permission);
  function leave(path: string, global = false) {
    if (dirty || pending) { setLeaveWarning(true); return; }
    setLeaveWarning(false);
    if (global) navigate(path); else go(path);
  }
  if (!can("settings.view")) return <main className="xj-settings-page"><h1>{t("title")}</h1><p role="alert" className="xj-settings-notice">{t("noSettingsView")}</p></main>;
  const scope = { actorId: access.actorId, workspaceId: access.workspaceId };
  return <main className="xj-settings-page">
    <header className="xj-settings-page-heading"><div><h1>{t("title")}</h1><p>{t("subtitle")}</p></div><span className="xj-settings-context">{access.workspaceName}</span></header>
    {leaveWarning && (dirty || pending) ? <p className="xj-settings-notice" role="alert">{t("unsaved")}</p> : null}
    <div className="xj-settings-layout">
      <nav className="xj-settings-nav" aria-label={t("navigation")}>{[
        ["settings-account", "account", UserRound], ["settings-home", "home", House],
        ...(can("analytics.view") ? [["settings-dashboard", "dashboard", ChartNoAxesCombined] as const] : []),
        ["settings-security", "security", ShieldCheck], ["settings-projects", "projectDefaults", Settings2],
      ].map(([id, label, Icon]) => <button key={String(id)} type="button" onClick={() => document.getElementById(String(id))?.scrollIntoView({ behavior: "instant", block: "start" })}><Icon size={16} aria-hidden="true" />{t(String(label))}</button>)}</nav>
      <div className="xj-settings-content">
        <section className="xj-settings-panel" id="settings-account" aria-labelledby="settings-account-title"><header className="xj-settings-section-heading"><div><h2 id="settings-account-title"><UserRound size={19} aria-hidden="true" />{t("account")}</h2><p>{t("accountHelp")}</p></div></header><div className="xj-settings-account"><span className="xj-settings-avatar" aria-hidden="true">{Array.from(access.userName)[0] ?? "—"}</span><dl><div><dt>{t("displayName")}</dt><dd>{access.userName}</dd></div><div><dt>{t("workspace")}</dt><dd>{access.workspaceName}</dd></div><div className="xj-settings-identifier"><dt>{t("actorId")}</dt><dd>{access.actorId}</dd></div><div className="xj-settings-identifier"><dt>{t("workspaceId")}</dt><dd>{access.workspaceId}</dd></div></dl></div></section>
        <PreferenceGroup key={`${access.actorId}:${access.workspaceId}:home`} api={api} scope={scope} resource="creator-home" canManage={can("settings.manage")} onState={onState} />
        {can("analytics.view") ? <PreferenceGroup key={`${access.actorId}:${access.workspaceId}:dashboard`} api={api} scope={scope} resource="data-dashboard" canManage={can("analytics.manage")} onState={onState} /> : null}
        <section className="xj-settings-panel" id="settings-security" aria-labelledby="settings-security-title"><header className="xj-settings-section-heading"><div><h2 id="settings-security-title"><LockKeyhole size={19} aria-hidden="true" />{t("security")}</h2><p>{t("securityHelp")}</p></div></header><div className="xj-settings-link-grid">{[["/creator/account-security", "securityOverview"], ["/creator/login-devices", "devices"], ["/creator/risk-login-alert", "riskEvents"]].map(([path, label]) => <button key={path} type="button" disabled={pending} className="xj-settings-link" onClick={() => leave(path, true)}>{t(label)}<ArrowUpRight size={16} aria-hidden="true" /></button>)}</div></section>
        <section className="xj-settings-panel" id="settings-projects" aria-labelledby="settings-projects-title"><header className="xj-settings-section-heading"><div><h2 id="settings-projects-title"><Settings2 size={19} aria-hidden="true" />{t("projectDefaults")}</h2><p>{t("projectDefaultsHelp")}</p></div></header><div className="xj-settings-actions"><button type="button" className="xj-settings-button" disabled={pending || !can("project.view")} onClick={() => leave("projects")}>{t("openProjects")}<ArrowUpRight size={15} aria-hidden="true" /></button><button type="button" className="xj-settings-button" disabled={pending || !can("project.view")} onClick={() => leave("home")}>{t("openHome")}</button>{can("analytics.view") ? <button type="button" className="xj-settings-button" disabled={pending} onClick={() => leave("analytics")}>{t("openDashboard")}</button> : null}</div></section>
        <p className="xj-settings-footnote">{t("permissions")} {t("retained")}</p>
      </div>
    </div>
  </main>;
}
