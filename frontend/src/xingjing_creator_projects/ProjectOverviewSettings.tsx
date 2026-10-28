import { useEffect, useState, type FormEvent } from "react";
import { CreatorApiError } from "./api";
import type { CreatorProjectsPort } from "./types";
import { controlButton, useProjectFlow, useProjectNavigationBlocker } from "./ProjectFlowShell";
import { useControlTranslation } from "./control-i18n";
import { projectOwnerId } from "./project-owner";

const field = "mt-2 w-full rounded-lg border border-[#2A2E3A] bg-[#07080C] px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-orange-400 disabled:opacity-50";
export function ProjectOverviewSettings({ api, onClose }: { api: CreatorProjectsPort; onClose: () => void }) {
  const { data, ownerDisplayName, refresh } = useProjectFlow();
  const { t } = useControlTranslation();
  const project = data.project;
  const config = project.configuration;
  const canBudget = data.permissions.includes("billing.view");
  const [name, setName] = useState(project.name);
  const initialOwnerId = projectOwnerId(project);
  const [owner, setOwner] = useState(initialOwnerId);
  const [target, setTarget] = useState(project.targetPlatform);
  const [budget, setBudget] = useState(config?.budgetMinor ? String(config.budgetMinor / 100) : "");
  const [schedule, setSchedule] = useState(config?.scheduleDays ? String(config.scheduleDays) : "");
  const [seats, setSeats] = useState(config?.teamSeats ?? 1);
  const [reviewSeats, setReviewSeats] = useState(config?.reviewSeats ?? 1);
  const [members, setMembers] = useState<{ id: string; name: string }[]>([]);
  const [memberError, setMemberError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<unknown>();
  useProjectNavigationBlocker(dirty || busy);
  useEffect(() => {
    let active = true;
    void api.getProjectSetupOptions?.().then((result) => { if (active) setMembers(result.members); })
      .catch(() => { if (active) setMemberError(true); });
    return () => { active = false; };
  }, [api]);
  const conflict = error instanceof CreatorApiError && error.status === 409;
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || conflict) return;
    setBusy(true); setError(undefined);
    try {
      await api.projectAction(project.id, "updateOverview", {
        name: name.trim(), ownerId: owner, targetPlatform: target,
        ...(canBudget ? { budgetMinor: budget ? Math.round(Number(budget) * 100) : null } : {}),
        scheduleDays: schedule ? Number(schedule) : null, teamSeats: seats, reviewSeats,
      }, project.version);
      refresh(); onClose();
    } catch (problem) { setError(problem); }
    finally { setBusy(false); }
  }
  return <section className="rounded-xl border border-orange-400/30 bg-[#12141B] p-5">
    <h2 className="text-lg font-semibold">{t("editConfig")}</h2><p className="mt-2 text-xs leading-5 text-slate-400">{t("editConfigHint")}</p>
    <form onSubmit={(event) => void save(event)} onChange={() => setDirty(true)}>
      <fieldset disabled={busy || conflict} className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <label className="text-xs text-slate-400">{t("projectName")}<input className={field} required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label className="text-xs text-slate-400">{t("owner")}<select className={field} value={owner} onChange={(event) => setOwner(event.target.value)}>{!members.some((m) => m.id === initialOwnerId) && <option value={initialOwnerId}>{ownerDisplayName}</option>}{members.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}</select></label>
        <label className="text-xs text-slate-400">{t("target")}<select className={field} value={target} disabled={project.productionStatus === "completed"} onChange={(event) => setTarget(event.target.value)}>{["douyin", "kuaishou", "wechat", "bilibili", "web"].map((platform) => <option key={platform} value={platform}>{platform}</option>)}</select></label>
        {canBudget && <label className="text-xs text-slate-400">{t("budgetAmount")}<input type="number" className={field} min="0.01" max="1000000000" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} /></label>}
        <label className="text-xs text-slate-400">{t("schedule")}<input type="number" className={field} min={1} max={3650} step={1} value={schedule} onChange={(event) => setSchedule(event.target.value)} /></label>
        <label className="text-xs text-slate-400">{t("seats")}<input type="number" className={field} required min={1} max={1000} value={seats} onChange={(event) => setSeats(Number(event.target.value))} /></label>
        <label className="text-xs text-slate-400">{t("reviewSeats")}<input type="number" className={field} required min={1} max={1000} value={reviewSeats} onChange={(event) => setReviewSeats(Number(event.target.value))} /></label>
      </fieldset>
      {memberError && <p className="mt-3 text-xs text-amber-200">{t("memberUnavailable")}</p>}
      {Boolean(error) && <div role="alert" className="mt-4 text-sm text-amber-200">{t(conflict ? "conflict" : error instanceof CreatorApiError && error.status === 403 ? "forbidden" : "invalidConfig")}{conflict && <button type="button" className={controlButton + " ml-3"} onClick={() => { refresh(); onClose(); }}>{t("retry")}</button>}</div>}
      <div className="mt-5 flex gap-3"><button className={controlButton + " bg-orange-500/15 text-orange-200"} disabled={busy || conflict}>{t(busy ? "savingConfig" : "saveConfig")}</button><button type="button" className={controlButton} disabled={busy} onClick={onClose}>{t("cancel")}</button></div>
    </form>
  </section>;
}
