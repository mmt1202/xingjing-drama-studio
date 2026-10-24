import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ChartNoAxesCombined, House, RefreshCw } from "lucide-react";
import { useWorkspaceNavigationGuard } from "@/xingjing_creator_home/WorkspaceFrame";
import { AuthSessionError } from "@/utils/auth";
import { PreferenceApiError, type PreferenceApi, type PreferenceRecord } from "./api";
import { useSettingsTranslation } from "./i18n";
import { persistSettingsDraft, readSettingsDraft, settingsValueIssue, settingsValues, type SettingsDraft, type SettingsResource, type SettingsScope } from "./preference-state";

function errorKey(error: unknown, saving = false) {
  if (error instanceof PreferenceApiError) {
    if (error.status === 409) return "conflict";
    if (error.status === 401) return "sessionExpired";
    if (error.status === 403) return "forbidden";
    if (error.status === 404) return "scopeUnavailable";
    if (error.status === 422) return "invalid";
  }
  return saving ? "saveFailed" : "unavailable";
}

export function PreferenceGroup({ api, scope, resource, canManage, onState }: {
  api: PreferenceApi; scope: SettingsScope; resource: SettingsResource; canManage: boolean;
  onState: (resource: SettingsResource, dirty: boolean, pending: boolean) => void;
}) {
  const { t, i18n } = useSettingsTranslation();
  const prefix = useId();
  const [record, setRecord] = useState<PreferenceRecord | null>();
  const [draft, setDraft] = useState(() => readSettingsDraft(scope, resource));
  const [recovered, setRecovered] = useState(Boolean(draft));
  const [readError, setReadError] = useState<unknown>();
  const [writeError, setWriteError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const [reading, setReading] = useState(false);
  const [saved, setSaved] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  const active = useRef(false);
  const writeBusy = useRef(false);
  const reader = useRef<AbortController | null>(null);
  const currentDraft = useRef(draft);
  const home = resource === "creator-home";
  const title = t(home ? "home" : "dashboard");
  const values = draft?.values ?? settingsValues(resource, record?.value);
  const issue = record ? settingsValueIssue(resource, record.value) : null;
  const changedVersion = draft && record !== undefined && (draft.base?.version ?? 0) !== (record?.version ?? 0);
  const conflict = writeError instanceof PreferenceApiError && writeError.status === 409 || Boolean(changedVersion && !draft?.intent);
  const denied = writeError instanceof PreferenceApiError && [401, 403, 404].includes(writeError.status);
  const readDenied = readError instanceof PreferenceApiError && [401, 403, 404].includes(readError.status);
  const locked = !canManage || record === undefined || reading || Boolean(readError) || pending || conflict || Boolean(issue) || Boolean(draft?.intent);
  useWorkspaceNavigationGuard(Boolean(draft) || pending);
  useEffect(() => { onState(resource, Boolean(draft), pending); }, [draft, onState, pending, resource]);
  useEffect(() => () => onState(resource, false, false), [onState, resource]);
  useEffect(() => { active.current = true; return () => { active.current = false; reader.current?.abort(); }; }, []);
  useEffect(() => {
    const abort = new AbortController();
    reader.current?.abort(); reader.current = abort;
    void api.get(resource, abort.signal).then((next) => {
      if (!abort.signal.aborted) { setRecord(next); setReadError(undefined); setReading(false); }
    }).catch((problem: unknown) => { if (!abort.signal.aborted) setReadError(problem); });
    return () => abort.abort();
  }, [api, resource]);

  function persist(next: SettingsDraft | null) {
    currentDraft.current = next;
    setDraft(next);
    if (!persistSettingsDraft(scope, resource, next)) setStorageUnavailable(true);
  }
  function change(field: string, value: unknown) {
    if (locked) return;
    const next = { ...values, [field]: value };
    const base = draft?.base ?? record ?? null;
    const untouched = JSON.stringify(next) === JSON.stringify(settingsValues(resource, base?.value));
    persist(untouched ? null : { ...scope, resource, base, values: next });
    setWriteError(undefined); setSaved(false); setRecovered(false);
  }
  async function readLatest(mode: "refresh" | "preserve" | "discard" = "refresh") {
    if (writeBusy.current) return;
    if (draft && mode === "refresh") { setConfirmDiscard(true); return; }
    if (mode === "discard") { persist(null); setWriteError(undefined); setRecovered(false); setSaved(false); }
    setConfirmDiscard(false); setReading(true); setReadError(undefined);
    reader.current?.abort();
    const abort = new AbortController(); reader.current = abort;
    try {
      const next = await api.get(resource, abort.signal);
      if (active.current && !abort.signal.aborted) setRecord(next);
    } catch (problem) { if (active.current && !abort.signal.aborted) setReadError(problem); }
    finally { if (active.current && !abort.signal.aborted) setReading(false); }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (writeBusy.current || !draft || !canManage || record === undefined || reading || readError || conflict || denied || issue) return;
    const attempt = draft.intent ?? { key: crypto.randomUUID(), value: { ...draft.base?.value, ...draft.values }, version: draft.base?.version ?? 0 };
    if (settingsValueIssue(resource, attempt.value)) return;
    const attempted = { ...draft, intent: attempt };
    persist(attempted);
    writeBusy.current = true; setPending(true); setWriteError(undefined); setSaved(false);
    try {
      const result = await api.put(resource, attempt.value, attempt.version, attempt.key);
      if (!active.current || currentDraft.current?.intent?.key !== attempt.key) return;
      setRecord(result); persist(null); setRecovered(false); setSaved(true);
    } catch (problem) { if (active.current) setWriteError(problem); }
    finally { writeBusy.current = false; if (active.current) setPending(false); }
  }
  function select(field: string, options: readonly (readonly [string | number, string])[], numeric = false) {
    const id = `${prefix}-${field}`;
    return <label className="xj-settings-field" htmlFor={id}><span>{t(field)}</span><select id={id} value={String(values[field])} onChange={(event) => change(field, numeric ? Number(event.target.value) : event.target.value)}>{options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>;
  }
  const date = record?.updatedAt && !Number.isNaN(Date.parse(record.updatedAt)) ? new Date(record.updatedAt).toLocaleString(i18n.resolvedLanguage) : "—";
  const Icon = home ? House : ChartNoAxesCombined;
  return <section className="xj-settings-panel" id={home ? "settings-home" : "settings-dashboard"} aria-labelledby={`${prefix}-heading`} aria-busy={reading || pending || record === undefined && !readError}>
    <header className="xj-settings-section-heading"><div><h2 id={`${prefix}-heading`}><Icon size={19} aria-hidden="true" />{title}</h2><p>{t(home ? "homeHelp" : "dashboardHelp")}</p></div><span className="xj-settings-badge">{t(canManage ? "editable" : "readonly")}</span></header>
    {record === undefined && !readError ? <div className="xj-settings-loading" role="status"><span />{t("loading")}</div> : null}
    {readError ? <div className="xj-settings-notice xj-settings-notice--error" role="alert"><p>{readError instanceof AuthSessionError ? readError.message : t(errorKey(readError))}</p>{readError instanceof PreferenceApiError && readError.requestId ? <small>{t("requestId", { id: readError.requestId })}</small> : null}<button type="button" className="xj-settings-button" disabled={reading || pending} onClick={() => void readLatest("preserve")}>{t("retry")}</button></div> : null}
    {!readDenied && (record !== undefined || draft) ? <form onSubmit={(event) => void save(event)}>
      {!canManage ? <p className="xj-settings-help">{t("readonlyHelp")}</p> : null}
      {recovered ? <p className="xj-settings-notice" role="status">{t("recovered")}</p> : null}
      {draft?.intent && !pending && !conflict ? <p className="xj-settings-notice" role="status">{t("uncertain")}</p> : null}
      {issue ? <p className="xj-settings-notice xj-settings-notice--error" role="alert">{t(issue)}</p> : null}
      <fieldset disabled={locked} className="xj-settings-fields">
        {home ? <><div className="xj-settings-toggles" role="group" aria-label={t("homeDisplay")}>{["showRecent", "showTasks", "showInspiration"].map((field) => <label key={field} className="xj-settings-toggle"><span>{t(field)}</span><input type="checkbox" checked={values[field] === true} onChange={(event) => change(field, event.target.checked)} /><span className="xj-settings-switch" aria-hidden="true" /></label>)}</div>
          {select("defaultMode", ["pilot", "series", "comic", "realistic"].map((mode) => [mode, t(mode)]))}
          {select("recentDays", [[0, t("allTime")], [7, t("last7")], [30, t("last30")]], true)}
          {select("recentLimit", [3, 6, 9].map((count) => [count, t("projectCount", { count })]), true)}
        </> : <>{select("range", ["week", "month", "quarter", "all"].map((range) => [range, t(range)]))}{select("groupBy", ["project", "episode", "model", "member"].map((group) => [group, t(group)]))}</>}
      </fieldset>
      <p className="xj-settings-help">{t(record === null && !draft ? "notSaved" : "defaults")}</p>
      {conflict || writeError ? <div className="xj-settings-notice xj-settings-notice--error" role="alert"><p>{writeError instanceof AuthSessionError ? writeError.message : t(conflict ? "conflict" : errorKey(writeError, true))}</p>{writeError instanceof PreferenceApiError && writeError.requestId ? <small>{t("requestId", { id: writeError.requestId })}</small> : null}</div> : null}
      {saved ? <p className="xj-settings-success" role="status">{t("saved")}</p> : null}
      {storageUnavailable ? <p className="xj-settings-notice" role="status">{t("storageUnavailable")}</p> : null}
      <footer className="xj-settings-form-footer"><div className="xj-settings-actions">{canManage ? <button type="submit" className="xj-settings-button xj-settings-button--primary" disabled={!draft || record === undefined || pending || reading || Boolean(readError) || conflict || denied || Boolean(issue)}>{t(pending ? "saving" : draft?.intent ? "retrySave" : "save")}</button> : null}<button type="button" className="xj-settings-button" disabled={pending || reading} onClick={() => void readLatest()}><RefreshCw size={14} aria-hidden="true" />{t(draft ? "discard" : "refresh")}</button>{draft ? <span className="xj-settings-unsaved">{t("dirty")}</span> : null}</div><div className="xj-settings-version"><span>{t("version", { version: draft ? draft.base?.version ?? 0 : record?.version ?? 0 })}</span>{record ? <time dateTime={record.updatedAt}>{t("updated", { time: date })}</time> : null}</div></footer>
    </form> : null}
    {confirmDiscard ? <div className="xj-settings-confirm" role="alert"><h3>{t("confirmDiscard")}</h3><p>{t(draft?.intent ? "attemptedDiscard" : "confirmDiscardHelp")}</p><div className="xj-settings-actions"><button type="button" className="xj-settings-button" onClick={() => setConfirmDiscard(false)}>{t("keepEditing")}</button><button type="button" className="xj-settings-button xj-settings-button--danger" onClick={() => void readLatest("discard")}>{t("discard")}</button></div></div> : null}
  </section>;
}
