import { useRef, useState, type FormEvent } from "react";
import { Settings2 } from "lucide-react";
import { PreferenceApiError, type PreferenceApi, type PreferenceRecord } from "@/xingjing_creator_settings/api";
import { useWorkspaceNavigationGuard } from "@/xingjing_creator_home/WorkspaceFrame";
import { analyticsButton, analyticsField, analyticsPanel, useAnalyticsTranslation } from "./i18n";
import { dashboardDefaults, groups, ranges, type DashboardDefaults } from "./query";

export function DashboardPreferences({ api, record, onSaved, onClose, onReload }: {
  api: PreferenceApi;
  record: PreferenceRecord | null;
  onSaved: (record: PreferenceRecord) => void;
  onClose: () => void;
  onReload: () => void;
}) {
  const { t } = useAnalyticsTranslation();
  const [value, setValue] = useState(() => dashboardDefaults(record?.value));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const busy = useRef(false);
  const receipt = useRef<{ body: string; key: string } | null>(null);
  const conflict = error instanceof PreferenceApiError && error.status === 409;
  const dirty = JSON.stringify(value) !== JSON.stringify(dashboardDefaults(record?.value));
  useWorkspaceNavigationGuard(dirty || pending);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy.current || conflict) return;
    busy.current = true; setPending(true); setError(undefined);
    const payload = { range: value.range, groupBy: value.groupBy };
    const body = JSON.stringify([record?.version ?? 0, payload]);
    if (receipt.current?.body !== body) receipt.current = { body, key: crypto.randomUUID() };
    try { onSaved(await api.put("data-dashboard", payload, record?.version ?? 0, receipt.current.key)); }
    catch (reason) { setError(reason); }
    finally { busy.current = false; setPending(false); }
  }

  return <section className={analyticsPanel + " border-violet-300/30"}>
    <h2 className="flex items-center gap-2 text-lg font-semibold"><Settings2 size={18} className="text-violet-300" />{t("defaults")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("preferenceHelp")}</p>
    <form onSubmit={(event) => void save(event)}>
      <fieldset disabled={pending || conflict} className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="space-y-2 text-xs text-slate-400"><span>{t("range")}</span><select className={analyticsField} value={value.range} onChange={(event) => setValue((current) => ({ ...current, range: event.target.value as DashboardDefaults["range"] }))}>{ranges.map((range) => <option key={range} value={range}>{t(range)}</option>)}</select></label>
        <label className="space-y-2 text-xs text-slate-400"><span>{t("groupBy")}</span><select className={analyticsField} value={value.groupBy} onChange={(event) => setValue((current) => ({ ...current, groupBy: event.target.value as DashboardDefaults["groupBy"] }))}>{groups.map((group) => <option key={group} value={group}>{t(group)}</option>)}</select></label>
      </fieldset>
      {error ? <p role="alert" className="mt-4 text-sm leading-6 text-amber-200">{t(conflict ? "preferenceConflict" : error instanceof PreferenceApiError && error.status === 403 ? "forbidden" : "unavailable")}{error instanceof PreferenceApiError && error.requestId ? <span className="mt-1 block text-xs">{t("requestId", { id: error.requestId })}</span> : null}</p> : null}
      <div className="mt-5 flex flex-wrap items-center gap-3"><button disabled={pending || conflict} className={analyticsButton + " bg-orange-400/10 text-orange-200"}>{t(pending ? "saving" : "save")}</button><button type="button" disabled={pending} className={analyticsButton} onClick={conflict ? onReload : onClose}>{t(conflict ? "discardReload" : "cancel")}</button><span className="text-xs text-slate-500">{t("preferenceVersion", { version: record?.version ?? 0 })}</span></div>
    </form>
  </section>;
}
