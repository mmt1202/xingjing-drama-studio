import { useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle2, Film, Layers, RefreshCw, TriangleAlert } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { CreatorApiError } from "./api";
import type { CreatorProjectsPort } from "./types";
import { controlButton, useProjectFlow, useProjectNavigationBlocker } from "./ProjectFlowShell";
import { useControlTranslation } from "./control-i18n";
import { ProjectOverviewSettings } from "./ProjectOverviewSettings";

const panel = "min-w-0 rounded-xl border border-[#2A2E3A] bg-[#12141B] p-5";
const input = "min-w-0 rounded-lg border border-[#2A2E3A] bg-[#07080C] px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-orange-400";
const states = ["unbound", "script_frozen", "storyboard_ready", "final_ready", "pending_review", "review_blocked", "package_ready", "restricted"];

export function ProjectControlPage({ api }: { api: CreatorProjectsPort }) {
  const { t, i18n } = useControlTranslation();
  const { data, loading, refresh, go } = useProjectFlow();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(useSearch());
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useProjectNavigationBlocker(busy);
  const [feedback, setFeedback] = useState<string>();
  const [failure, setFailure] = useState(false);
  const [editing, setEditing] = useState(false);
  const { project, episodePage, episodes, tasks, costs } = data;
  const disabled = busy || loading || editing;
  const can = (permission: string) => data.permissions.includes(permission);
  const label = (state: string) => t(`status.${state}`, { defaultValue: state });
  const date = (value: string) => new Date(value).toLocaleString(i18n.resolvedLanguage ?? "zh");
  const money = (minor: number, currency: string) => new Intl.NumberFormat(i18n.resolvedLanguage ?? "zh", { style: "currency", currency }).format(minor / 100);
  function filter(values: Record<string, string | number>) {
    if (disabled) return;
    for (const [key, value] of Object.entries(values)) {
      if (value === "") params.delete(key); else params.set(key, String(value));
    }
    navigate(`${location}?${params}`);
  }
  function search(event: FormEvent) { event.preventDefault(); filter({ q: query.trim(), offset: 0 }); }
  async function change(action: "advance" | "rollback", target: string) {
    if (disabled) return;
    if (reason.trim().length < 3 || reason.trim().length > 500) { setFeedback(t("reasonRequired")); setFailure(true); return; }
    setBusy(true); setFeedback(undefined);
    try {
      await api.projectAction(project.id, action, { productionStatus: target, reason: reason.trim() }, project.version);
      setReason(""); setFailure(false); setFeedback(t("saved")); refresh();
    } catch (error) {
      setFailure(true);
      const code = error instanceof CreatorApiError ? error : undefined;
      setFeedback(t(code?.status === 403 ? "forbidden" : code?.status === 409 ? "conflict" : "notSaved"));
    } finally { setBusy(false); }
  }
  const metrics = [
    { key: "totalEpisodes", value: episodes.length, icon: Layers },
    { key: "finals", value: can("final.view") ? episodes.filter((e) => e.finalVersionId).length : "—", icon: Film },
    { key: "packages", value: can("export.view") && episodes.every((e) => e.state !== "restricted") ? episodes.filter((e) => e.state === "package_ready").length : "—", icon: CheckCircle2 },
    { key: "pending", value: can("compliance.view") && episodes.every((e) => e.state !== "restricted") ? episodes.filter((e) => ["pending_review", "review_blocked"].includes(e.state)).length : "—", icon: TriangleAlert },
  ];
  const counts = tasks?.counts ?? {};
  return <main className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 md:px-6" aria-busy={busy || loading}>
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="text-xs font-medium tracking-wider text-[#FF8D74]">{t("title")}</p><h1 className="mt-2 text-2xl font-semibold tracking-tight md:text-3xl">{project.name}</h1><p className="mt-2 text-sm text-slate-400">{t("intro")}</p></div>
      <div className="flex flex-wrap gap-2"><button className={controlButton} disabled={disabled || editing} onClick={refresh}><RefreshCw size={14} />{t("refresh")}</button>{can("project.manage") && !project.archived && <button className={controlButton} disabled={disabled || editing} onClick={() => project.setupState === "draft" ? go("guided-create") : setEditing(true)}>{t(project.setupState === "draft" ? "setup" : "editConfig")}</button>}<button className={controlButton + " border-orange-400/40 bg-orange-400/10 text-orange-200"} onClick={() => go("episodes")}>{t("manageEpisodes")}<ArrowRight size={14} /></button></div>
    </header>
    <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-400">{[["projectId", project.id], ["type", project.type], ["target", project.targetPlatform], ["owner", project.ownerName], ["version", project.version]].map(([key, value]) => <span key={key}>{t(String(key))}<span className="ml-2 font-mono text-slate-200">{value}</span></span>)}</div>
    {editing && <ProjectOverviewSettings api={api} onClose={() => setEditing(false)} />}
    <section aria-label={t("overview")} className="grid grid-cols-2 gap-3 xl:grid-cols-4">{metrics.map(({ key, value, icon: Icon }) => <article key={key} className={panel}><div className="flex items-center justify-between gap-2 text-sm text-slate-400">{t(key)}<Icon size={17} className="text-slate-500" /></div><p className="mt-4 font-mono text-3xl font-semibold tracking-tight">{value}</p>{key === "totalEpisodes" && <p className="mt-2 text-xs text-slate-500">{t("planned", { count: data.plannedCount })}</p>}</article>)}</section>
    <p className="text-xs text-slate-500">{t("countHint")}</p>
    <section className="flex flex-wrap items-center justify-between gap-3 border-y border-[#2A2E3A] py-3"><p className="max-w-3xl text-xs leading-5 text-slate-400">{t("rangeHint")}</p><label className="flex items-center gap-3 text-sm">{t("range")}<select aria-label={t("range")} className={input} value={params.get("days") ?? "0"} disabled={disabled} onChange={(event) => filter({ days: event.target.value })}><option value="0">{t("allTime")}</option><option value="7">{t("week")}</option><option value="30">{t("month")}</option></select></label></section>
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0 space-y-5">
        <section className={panel}>
          <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{t("episodeProgress")}</h2><form onSubmit={search} className="flex max-w-full flex-wrap gap-2"><input aria-label={t("search")} placeholder={t("search")} className={input + " w-44"} value={query} onChange={(event) => setQuery(event.target.value)} /><button className={controlButton} disabled={disabled}>{t("searchAction")}</button><select aria-label={t("filter")} className={input} value={params.get("episodeState") ?? ""} disabled={disabled} onChange={(event) => filter({ episodeState: event.target.value, offset: 0 })}><option value="">{t("allStates")}</option>{states.map((state) => <option key={state} value={state}>{t(`episodeState.${state}`)}</option>)}</select></form></div>
          <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-[#2A2E3A] text-xs text-slate-500"><tr><th className="py-3">{t("episodeColumn")}</th><th>{t("state")}</th><th>{t("shots")}</th><th className="text-right">{t("next")}</th></tr></thead><tbody>{episodePage.items.map((e) => <tr key={e.episode} className="border-b border-[#2A2E3A]/60"><td className="min-w-44 py-4 pr-4"><p className="font-medium">{e.title}</p><p className="mt-1 font-mono text-xs text-slate-500">{t("episode", { number: e.episode })}</p></td><td className="min-w-28 pr-3"><span className={e.state === "package_ready" ? "text-emerald-300" : e.state === "review_blocked" || e.issue ? "text-amber-300" : "text-slate-300"}>{t(`episodeState.${e.state}`)}</span>{e.issue && <p className="mt-1 max-w-44 text-xs text-amber-300">{t("gate.assetsReady")}</p>}</td><td className="font-mono">{e.shots ?? "—"}</td><td className="text-right"><button className={controlButton + " whitespace-nowrap"} onClick={() => go(e.finalVersionId && can("export.view") ? "release-package" : e.shots && can("shot.view") ? "shots" : "episodes", e.episode)}>{t("next")}<ArrowRight size={13} /></button></td></tr>)}</tbody></table></div>
          {!episodePage.items.length && <p className="py-8 text-center text-sm text-slate-500">{t("empty")}</p>}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>{t("page", { total: episodePage.total, page: Math.floor(episodePage.offset / episodePage.limit) + 1 })}</span><div className="flex gap-2"><button className={controlButton} disabled={disabled || episodePage.offset === 0} onClick={() => filter({ offset: Math.max(0, episodePage.offset - episodePage.limit) })}>{t("previous")}</button><button className={controlButton} disabled={disabled || episodePage.offset + episodePage.limit >= episodePage.total} onClick={() => filter({ offset: episodePage.offset + episodePage.limit })}>{t("nextPage")}</button></div></div>
        </section>
        <section className={panel}><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{t("tasks")}</h2>{can("generation.view") && <button className={controlButton} onClick={() => go("tasks")}>{t("taskCenter")}<ArrowRight size={13} /></button>}</div>
          {tasks ? <><div className="my-4 flex flex-wrap gap-6 text-sm"><span className="text-slate-400">{t("active")} <strong className="ml-2 font-mono text-slate-100">{["queued", "running", "retrying", "cancelling"].reduce((sum, state) => sum + (counts[state] ?? 0), 0)}</strong></span><span className="text-slate-400">{t("errors")} <strong className="ml-2 font-mono text-amber-300">{(counts.failed ?? 0) + (counts.timeout ?? 0)}</strong></span></div><p className="text-xs text-slate-500">{t("taskHint")}</p><ul className="mt-3 divide-y divide-[#2A2E3A]">{tasks.items.map((task) => <li key={task.id} className="flex flex-wrap items-center justify-between gap-3 py-3"><span className="max-w-full truncate font-mono text-xs" title={task.id}>{task.id}</span><span className="text-xs text-slate-400">{t(`taskState.${task.status}`, { defaultValue: task.status })} · {date(task.updatedAt)}</span></li>)}</ul>{!tasks.items.length && <p className="py-5 text-sm text-slate-500">{t("empty")}</p>}</> : <p className="mt-5 text-sm text-slate-500">{t("restricted")}</p>}
        </section>
        <section className={panel}><h2 className="text-lg font-semibold">{t("history")}</h2><ul className="mt-4 divide-y divide-[#2A2E3A]">{data.history.map((log) => <li key={log.id} className="py-3"><p className="text-sm">{label(log.from)} → {label(log.to)}</p><p className="mt-1 text-sm text-slate-400">{log.reason}</p><p className="mt-1 break-all font-mono text-xs text-slate-500">{log.actorId} · {date(log.createdAt)} · {log.requestId}</p></li>)}</ul>{!data.history.length && <p className="mt-4 text-sm text-slate-500">{t("noHistory")}</p>}</section>
      </div>
      <aside className="min-w-0 space-y-5">
        <section className={panel + " border-orange-400/25"}><p className="text-xs text-slate-400">{t("stageActions")}</p><h2 className="mt-2 text-xl font-semibold text-orange-200">{label(project.archived ? "archived" : project.productionStatus)}</h2><p className="mt-3 text-xs leading-5 text-slate-400">{t("stageHint")}</p>
          {can("project.manage") && data.transitions.some((step) => !step.reason) && <label className="mt-4 block text-xs text-slate-400">{t("reason")}<textarea className={input + " mt-2 w-full"} value={reason} maxLength={500} rows={3} disabled={disabled} onChange={(event) => setReason(event.target.value)} /></label>}
          <div className="mt-4 space-y-3">{data.transitions.map((step) => <div key={step.action}><button className={controlButton + " w-full"} disabled={disabled || Boolean(step.reason)} onClick={() => void change(step.action, step.target)}>{t(step.action, { stage: label(step.target) })}<ArrowRight size={14} /></button>{step.reason && <p className="mt-2 text-xs leading-5 text-amber-200/80">{t(`gate.${step.reason}`)}</p>}</div>)}</div>
          {!data.transitions.length && <p className="mt-4 text-xs text-slate-400">{t("noTransitions")}</p>}{feedback && <div role={failure ? "alert" : "status"} className={`mt-3 text-sm ${failure ? "text-amber-200" : "text-emerald-300"}`}>{feedback}{failure && <button className={controlButton + " mt-2"} onClick={refresh}>{t("retry")}</button>}</div>}
        </section>
        <section className={panel}><h2 className="text-lg font-semibold">{t("budget")}</h2>{costs === null ? <p className="mt-4 text-sm text-slate-500">{t("restricted")}</p> : <><p className="mt-4 text-xs text-slate-400">{t("budgetAmount")}</p><p className="mt-2 font-mono text-xl">{project.configuration?.budgetMinor ? money(project.configuration.budgetMinor, "CNY") : t("noBudget")}</p><div className="mt-4 space-y-3">{costs.map((cost) => <div key={cost.currency} className="rounded-lg border border-[#2A2E3A] p-3"><p className="font-mono text-xs text-slate-500">{cost.currency}</p><p className="mt-2 text-sm text-slate-400">{t("spent")} <strong className="float-right font-mono text-slate-100">{money(cost.spentMinor, cost.currency)}</strong></p><p className="mt-2 text-sm text-slate-400">{t("held")} <strong className="float-right font-mono text-slate-100">{money(cost.heldMinor, cost.currency)}</strong></p></div>)}</div>{!costs.length && <p className="mt-4 text-sm text-slate-500">{t("noCosts")}</p>}<p className="mt-4 text-xs leading-5 text-slate-500">{t("costHint")}</p></>}</section>
        <section className={panel}><h2 className="text-lg font-semibold">{t("team")}</h2><dl className="mt-4 space-y-3 text-sm">{[["owner", project.ownerName], ["seats", project.configuration?.teamSeats ?? "—"], ["reviewSeats", project.configuration?.reviewSeats ?? "—"], ["schedule", project.configuration?.scheduleDays ? t("days", { count: project.configuration.scheduleDays }) : "—"]].map(([key, value]) => <div key={key} className="flex justify-between gap-3"><dt className="text-slate-400">{t(String(key))}</dt><dd className="max-w-40 break-all text-right">{value}</dd></div>)}</dl><p className="mt-4 text-xs leading-5 text-slate-500">{t("planningHint")}</p></section>
      </aside>
    </div>
    <p className="text-xs text-slate-500">{t("refreshed", { time: date(data.updatedAt) })}</p>
  </main>;
}
