import { useRef, useState, type FormEvent } from "react";
import { PreferenceApiError, type PreferenceApi, type PreferenceRecord } from "@/xingjing_creator_settings/api";
import { homeButton, homeField, useHomeTranslation } from "./i18n";
import { useWorkspaceNavigationGuard } from "./WorkspaceFrame";

export const homeModes = ["pilot", "series", "comic", "realistic"] as const;
export interface HomePreferences { showRecent: boolean; showTasks: boolean; showInspiration: boolean; defaultMode: typeof homeModes[number]; recentDays: 0 | 7 | 30; recentLimit: 3 | 6 | 9 }
export function homePreferences(value?: Record<string, unknown>): HomePreferences {
  return {
    showRecent: value?.showRecent !== false, showTasks: value?.showTasks !== false, showInspiration: value?.showInspiration !== false,
    defaultMode: homeModes.includes(value?.defaultMode as HomePreferences["defaultMode"]) ? value?.defaultMode as HomePreferences["defaultMode"] : "pilot",
    recentDays: [0, 7, 30].includes(Number(value?.recentDays)) ? Number(value?.recentDays) as HomePreferences["recentDays"] : 0,
    recentLimit: [3, 6, 9].includes(Number(value?.recentLimit)) ? Number(value?.recentLimit) as HomePreferences["recentLimit"] : 6,
  };
}
export function HomePreferencesEditor({ api, record, onSaved, onClose, onReload }: { api: PreferenceApi; record: PreferenceRecord | null; onSaved: (value: PreferenceRecord) => void; onClose: () => void; onReload: () => void }) {
  const { t } = useHomeTranslation();
  const [value, setValue] = useState(() => homePreferences(record?.value));
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<unknown>();
  const pending = useRef<{ body: string; key: string } | null>(null);
  useWorkspaceNavigationGuard(dirty || busy);
  const conflict = error instanceof PreferenceApiError && error.status === 409;
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy || conflict) return;
    setBusy(true); setError(undefined);
    const merged = { ...record?.value, ...value };
    const body = JSON.stringify([record?.version ?? 0, merged]);
    if (pending.current?.body !== body) pending.current = { body, key: crypto.randomUUID() };
    try { onSaved(await api.put("creator-home", merged, record?.version ?? 0, pending.current.key)); }
    catch (problem) { setError(problem); }
    finally { setBusy(false); }
  }
  return <section className="rounded-2xl border border-violet-300/25 bg-[#11141D] p-5">
    <h2 className="text-lg font-semibold">{t("customize")}</h2><p className="mt-2 text-sm text-slate-400">{t("customizeHint")}</p>
    <form onSubmit={(event) => void save(event)} onChange={() => setDirty(true)}>
      <fieldset disabled={busy || conflict} className="mt-5 grid gap-4 md:grid-cols-3">
        {(["showRecent", "showTasks", "showInspiration"] as const).map((field) => <label key={field} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value[field]} onChange={(event) => setValue({ ...value, [field]: event.target.checked })} className="accent-orange-400" />{t(field)}</label>)}
        <label className="grid gap-2 text-sm text-slate-400">{t("defaultMode")}<select className={homeField} value={value.defaultMode} onChange={(event) => setValue({ ...value, defaultMode: event.target.value as HomePreferences["defaultMode"] })}>{homeModes.map((mode) => <option key={mode} value={mode}>{t(mode)}</option>)}</select></label>
        <label className="grid gap-2 text-sm text-slate-400">{t("range")}<select className={homeField} value={value.recentDays} onChange={(event) => setValue({ ...value, recentDays: Number(event.target.value) as HomePreferences["recentDays"] })}>{[[0, "allTime"], [7, "week"], [30, "month"]].map(([days, key]) => <option key={days} value={days}>{t(String(key))}</option>)}</select></label>
        <label className="grid gap-2 text-sm text-slate-400">{t("recentLimit")}<select className={homeField} value={value.recentLimit} onChange={(event) => setValue({ ...value, recentLimit: Number(event.target.value) as HomePreferences["recentLimit"] })}>{[3, 6, 9].map((size) => <option key={size}>{size}</option>)}</select></label>
      </fieldset>
      {Boolean(error) && <p role="alert" className="mt-4 text-sm text-amber-200">{t(conflict ? "conflict" : error instanceof PreferenceApiError && error.status === 403 ? "forbidden" : "failed")}{conflict && <button type="button" className={homeButton + " ml-3"} onClick={onReload}>{t("refresh")}</button>}</p>}
      <div className="mt-5 flex gap-3"><button className={homeButton + " bg-orange-400/10 text-orange-200"} disabled={busy || conflict}>{t(busy ? "saving" : "save")}</button><button type="button" className={homeButton} disabled={busy} onClick={onClose}>{t("cancel")}</button></div>
    </form>
  </section>;
}
