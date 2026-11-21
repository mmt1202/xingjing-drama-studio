import { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { ModalShell } from "@/components/ui/ModalShell";
import { AssetsStoryboardApiError } from "./api";
import type { AssetItem, AssetsStoryboardPort, ShotItem, WorkspaceProjectScope } from "./contracts";
import { assetDisplayName, ShotAssets } from "./ShotReviewList";
import { useShotReviewTranslation } from "./shot-review-i18n";
import { button } from "./ui";
import { useDraftProtection } from "./useDraftProtection";

type References = ShotItem["assetReferences"];
const fieldClass = "mt-1.5 block w-full rounded-lg border border-white/15 bg-[#0e111a] px-3 py-2.5 text-sm text-slate-100 focus-visible:outline-2 focus-visible:outline-violet-300";
const mainButton = "rounded-lg bg-violet-200 px-4 py-2 text-sm font-semibold text-violet-950 hover:bg-violet-100 disabled:opacity-45";

export function useShotAssetChoices(api: AssetsStoryboardPort, { workspaceId, projectId }: WorkspaceProjectScope, enabled: boolean) {
  const [result, setResult] = useState<{ assets?: readonly AssetItem[]; error?: boolean; key: string }>();
  const key = JSON.stringify([workspaceId, projectId]);
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    async function load() {
      const assets: AssetItem[] = [];
      const seen = new Set<string>();
      let token: string | undefined;
      do {
        const page = await api.listAssets({ workspaceId, projectId }, { pageSize: 100, pageToken: token }, abort.signal);
        if (abort.signal.aborted) return;
        assets.push(...page.items);
        token = page.nextToken ?? undefined;
        if (token && (seen.has(token) || !page.items.length)) throw new Error("INVALID_PAGE_TOKEN");
        if (token) seen.add(token);
      } while (token);
      setResult({ key, assets });
    }
    void load().catch(() => { if (!abort.signal.aborted) setResult({ key, error: true }); });
    return () => abort.abort();
  }, [api, enabled, workspaceId, projectId, key]);
  return result?.key === key ? result : undefined;
}

function fields(shot: ShotItem) {
  return { shotNo: shot.shotNo, shotSize: shot.shotSize, cameraMove: shot.cameraMove,
    duration: String(shot.durationMs / 1000), dialogue: shot.dialogue ?? "", prompt: shot.prompt ?? "" };
}
type Fields = ReturnType<typeof fields>;
const fieldLabels = { shotNo: "number", shotSize: "size", cameraMove: "move", duration: "duration", dialogue: "dialogue", prompt: "prompt" } as const;
function sameRefs(a: References, b: References) {
  return a.length === b.length && a.every(ref => b.some(item => item.id === ref.id && item.versionId === ref.versionId && item.type === ref.type));
}

export function ShotDetailsEditor({ shot, scope, api, writable, onClose, onSaved }: {
  shot: ShotItem; scope: WorkspaceProjectScope; api: AssetsStoryboardPort; writable: boolean;
  onClose: () => void; onSaved: (number: string) => void;
}) {
  const { t } = useShotReviewTranslation();
  const titleId = useId();
  const [base, setBase] = useState(shot);
  const [values, setValues] = useState(() => fields(shot));
  const [refs, setRefs] = useState<References>(shot.assetReferences);
  const [search, setSearch] = useState("");
  const [pick, setPick] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [busy, setBusy] = useState(false);
  const latch = useRef(false);
  const [notice, setNotice] = useState("");
  const canEdit = writable && base.status !== "frozen";
  const options = useShotAssetChoices(api, scope, pick && canEdit);
  const original = fields(base);
  const keys = Object.keys(original) as (keyof Fields)[];
  const dirty = keys.some(key => values[key] !== original[key]) || !sameRefs(refs, base.assetReferences);
  const millis = Math.round(Number(values.duration) * 1000);
  const valid = Boolean(values.shotNo.trim()) && /^\d+(?:\.\d{1,3})?$/.test(values.duration) && Number.isSafeInteger(millis) && millis >= 1;
  useDraftProtection(busy || dirty);
  function close() {
    if (latch.current) return;
    if (dirty) setDiscard(true);
    else onClose();
  }
  async function reload() {
    if (latch.current) return;
    latch.current = true;
    setBusy(true);
    try {
      const page = await api.listShots(scope, { storyboardId: base.storyboardId, search: base.id, pageSize: 200 });
      const latest = page.items.find(item => item.id === base.id && item.storyboardId === base.storyboardId);
      if (!latest) throw new Error("SHOT_NOT_FOUND");
      const updated = fields(latest);
      for (const key of keys) if (values[key] !== original[key]) updated[key] = values[key];
      if (sameRefs(refs, base.assetReferences)) setRefs(latest.assetReferences);
      setValues(updated); setBase(latest); setNotice("reloaded");
    } catch { setNotice("conflict"); }
    finally { latch.current = false; setBusy(false); }
  }
  async function save() {
    if (!canEdit || latch.current || !dirty || !valid || notice === "conflict") return;
    const changes: Record<string, unknown> = {};
    for (const key of keys) if (values[key] !== original[key]) {
      if (key === "duration") changes.durationMs = millis;
      else changes[key] = key === "shotNo" ? values[key].trim() : values[key];
    }
    if (!sameRefs(refs, base.assetReferences)) changes.assetReferences = refs.map(({ id, versionId, type }) => ({ id, versionId, type }));
    latch.current = true; setBusy(true);
    try {
      const result = await api.shotAction(scope, { action: "batchUpdate", version: base.version, payload: { itemIds: [base.id], changes } });
      if (result.batch?.failed || !result.shots?.some(item => item.id === base.id && item.storyboardId === base.storyboardId)) throw new Error("SHOT_SAVE_FAILED");
      onSaved(values.shotNo.trim());
    } catch (e) {
      setNotice(e instanceof AssetsStoryboardApiError && (e.code === "VERSION_CONFLICT" || e.code === "STORYBOARD_FROZEN") ? "conflict" :
        e instanceof AssetsStoryboardApiError && e.code === "DUPLICATE_SHOT_NUMBER" ? "duplicate" : "failed");
    } finally { latch.current = false; setBusy(false); }
  }
  const available = options?.assets?.filter(asset => asset.currentVersionId && !refs.some(ref => ref.id === asset.id) &&
    (asset.name + " " + t(asset.type)).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <ModalShell open onClose={close} closeOnBackdrop={false} closeOnEscape={!busy} labelledBy={titleId}
    className="w-[42rem] overflow-hidden rounded-2xl border border-white/15 bg-[#151822] text-slate-100 shadow-2xl" style={{ colorScheme: "dark" }}>
    <form onSubmit={event => { event.preventDefault(); void save(); }} className="flex max-h-[88dvh] flex-col">
      <header className="flex items-start justify-between gap-3 border-b border-white/10 px-5 py-4">
        <div><h2 id={titleId} className="text-xl font-semibold">{t(canEdit ? "editNamed" : "shot", { no: base.shotNo })}</h2>
        <p className="mt-1 text-sm text-slate-400">{t(!writable ? "readonly" : base.status === "frozen" ? "lockedHelp" : "editHelp")}</p></div>
        <button type="button" className={button} aria-label={t("cancel")} onClick={close} disabled={busy}><X size={16}/></button>
      </header>
      <div className="min-h-0 space-y-4 overflow-y-auto p-5">
        <fieldset disabled={!canEdit || busy} className="grid min-w-0 gap-3 sm:grid-cols-2">
          {keys.map(key => <label key={key} className={"text-sm text-slate-300 " + (key === "dialogue" || key === "prompt" ? "sm:col-span-2" : "")}>{t(fieldLabels[key])}
            {key === "dialogue" || key === "prompt" ? <textarea rows={3} value={values[key]} className={fieldClass} onChange={e => setValues({ ...values, [key]: e.target.value })}/>
            : <input type={key === "duration" ? "number" : "text"} step={key === "duration" ? "0.001" : undefined} min={key === "duration" ? "0.001" : undefined}
                required={key === "shotNo" || key === "duration"} value={values[key]} className={fieldClass} onChange={e => setValues({ ...values, [key]: e.target.value })}/>}
          </label>)}
        </fieldset>
        {canEdit ? <details onToggle={e => setPick(e.currentTarget.open)} className="rounded-xl border border-white/10 p-3">
          <summary className="cursor-pointer text-sm font-medium">{t("references")} · {t("linked", { n: refs.length })}</summary>
          <div className="mt-3 space-y-2 text-sm">
            {refs.map(ref => <label key={ref.id + ":" + ref.versionId} className="flex items-center gap-2">
              <input type="checkbox" checked disabled={busy || !options?.assets} onChange={() => setRefs(refs.filter(item => item !== ref))}/>
              {assetDisplayName(ref) ?? options?.assets?.find(a => a.id === ref.id)?.name ?? t("unknownRef", { kind: t(ref.type) })}</label>)}
            {options?.assets ? <><input aria-label={t("assetSearch")} placeholder={t("assetSearch")} className={fieldClass} value={search} onChange={e => setSearch(e.target.value)}/>
              <div className="max-h-40 space-y-2 overflow-y-auto">{available?.map(asset => <label key={asset.id} className="flex items-center gap-2">
                <input type="checkbox" checked={false} disabled={busy || !asset.currentVersionId} onChange={() => setRefs([...refs, { id: asset.id, type: asset.type, versionId: asset.currentVersionId, name: asset.name }])}/>
                {asset.name} <span className="text-xs text-slate-400">{t(asset.type)}</span></label>)}
                {!available?.length && <p className="text-slate-400">{t("noAssetsFound")}</p>}</div></> :
                <p role="status" className="text-slate-400">{t(options?.error ? "assetUnavailable" : "assetLoading")}</p>}
          </div></details> : <ShotAssets shot={base}/>}
        {dirty && !valid && <p role="status" className="text-sm text-amber-200">{t("valid")}</p>}
        {notice && <div role="status" className="rounded-lg border border-amber-300/25 p-3 text-sm text-amber-100">
          {t(notice)} {notice === "conflict" && <button type="button" className={button} onClick={() => void reload()} disabled={busy}>{t("reloadEdit")}</button>}
          {notice === "reloaded" && <details className="mt-2"><summary className="cursor-pointer">{t("latest")}</summary><dl className="mt-2 space-y-1">
            {keys.map(key => <div key={key}><dt className="inline font-semibold">{t(fieldLabels[key])} </dt><dd className="inline whitespace-pre-wrap">{original[key] || t("unset")}</dd></div>)}</dl><ShotAssets shot={base}/></details>}
        </div>}
      </div>
      <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-white/10 px-5 py-3">
        {discard ? <><span role="status" className="mr-auto text-sm text-amber-200">{t("dirty")}</span><button type="button" className={button} onClick={onClose}>{t("discard")}</button>
          <button type="button" className={mainButton} onClick={() => setDiscard(false)}>{t("keep")}</button></> :
        <><button type="button" disabled={busy} className={button} onClick={close}>{t("cancel")}</button>{canEdit && <button type="submit" disabled={!dirty || !valid || busy || notice === "conflict"} className={mainButton}>{t(busy ? "saving" : "save")}</button>}</>}
      </footer>
    </form>
  </ModalShell>;
}
