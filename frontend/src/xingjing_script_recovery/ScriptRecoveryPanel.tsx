import { useEffect, useMemo, useRef, useState } from "react";
import { CreatorApiError } from "@/xingjing_creator_projects/api";
import type { ScriptFrameContext } from "@/xingjing_creator_projects/script-workflow";
import { createSourcePlanApi, denied, uncertain } from "@/xingjing_source_planning/api";
import { button, card, field, primary } from "@/xingjing_source_planning/i18n";
import { storageKey } from "@/xingjing_source_planning/storage";
import { ChangePicker } from "./ChangePicker";
import { RecoveryReview } from "./RecoveryReview";
import { useRecoveryTranslation } from "./i18n";
import { readRecoveryDraft, type Draft, type Overview, type Preview, type Result, type Selection } from "./types";

export function ScriptRecoveryPanel({ frame, projectId, externalBusy, onBlocked }: {
  frame: ScriptFrameContext; projectId: string; externalBusy: boolean; onBlocked: (value: boolean) => void;
}) {
  const { t } = useRecoveryTranslation(), script = frame.script!;
  const head = script.versions.at(-1)!;
  const storage = "recovery:" + storageKey(frame.context.workspace.id, frame.context.user.id, projectId) + ":" + script.id;
  const api = useMemo(() => createSourcePlanApi(frame.context.workspace.id), [frame.context.workspace.id]);
  const [draft, setDraft] = useState<Draft | undefined>(() => readRecoveryDraft(storage));
  const [chosen, setChosen] = useState<number>();
  const [overview, setOverview] = useState<Overview>(), [preview, setPreview] = useState<Preview>();
  const [result, setResult] = useState<Result>(), [error, setError] = useState<unknown>();
  const [localError, setLocalError] = useState(""), [forbidden, setForbidden] = useState(false);
  const [reload, setReload] = useState(0), [busy, setBusy] = useState(false), [discard, setDiscard] = useState(false);
  const active = useRef(false), submitting = useRef(false), accessEpoch = useRef(0);
  const sourceVersion = draft?.selection.sourceVersion ?? chosen ?? script.versions.at(-2)?.number;
  const source = script.versions.find(v => v.number === sourceVersion);
  const base = script.versions.find(v => v.number === (draft?.selection.baseVersion ?? head.number));
  const pending = draft?.pending, changes = draft?.selection.changes ?? [];
  const conflict = Boolean(overview && (overview.revision !== script.revision || overview.baseVersion !== head.number
    || draft && (overview.revision !== draft.selection.revision || overview.baseVersion !== draft.selection.baseVersion)));
  const locked = busy || Boolean(pending) || externalBusy || conflict || !overview?.canConfirm || !frame.canManage;

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    onBlocked(Boolean(draft) || busy);
    return () => onBlocked(false);
  }, [draft, busy, onBlocked]);
  useEffect(() => {
    const request = new AbortController(), epoch = ++accessEpoch.current;
    void api.recovery(projectId, script.id, request.signal).then(value => {
      if (request.signal.aborted || epoch !== accessEpoch.current) return;
      if (value.scriptId !== script.id || !Number.isSafeInteger(value.revision)) throw new Error("Invalid recovery scope");
      setOverview(value); setForbidden(false); setError(undefined);
    }).catch((failure: unknown) => {
      if (request.signal.aborted || epoch !== accessEpoch.current) return;
      setOverview(undefined); setPreview(undefined); setError(failure);
      if (denied(failure)) { setForbidden(true); setResult(undefined); }
    });
    return () => request.abort();
  }, [api, projectId, script.id, script.revision, reload]);

  function persist(next?: Draft) {
    try {
      if (next) sessionStorage.setItem(storage, JSON.stringify(next)); else sessionStorage.removeItem(storage);
      setDraft(next); setLocalError(""); return true;
    } catch { setLocalError(t("storage")); return false; }
  }
  function edit(value: Partial<Selection>) {
    if (locked || sourceVersion === undefined) return;
    const selection = { revision: script.revision, baseVersion: head.number, sourceVersion, reason: "", changes,
      ...draft?.selection, ...value };
    if (persist(selection.reason || selection.changes.length ? { selection } : undefined)) {
      setPreview(undefined); setError(undefined); setResult(undefined); setDiscard(false);
    }
  }
  function failed(failure: unknown) {
    setError(failure); setPreview(undefined);
    if (denied(failure)) {
      accessEpoch.current++; setOverview(undefined); setForbidden(true); setResult(undefined); frame.reload();
    }
  }
  async function run(save: boolean) {
    if (submitting.current || !draft || !overview || externalBusy || forbidden || !frame.canManage) return;
    if (!pending && (locked || !changes.length || !draft.selection.reason.trim())) return;
    const selection = draft.selection;
    const operation = pending ?? (save && preview ? { key: crypto.randomUUID(), digest: preview.previewDigest } : undefined);
    if (save && (!operation || !persist({ selection, pending: operation }))) return;
    submitting.current = true; setBusy(true); setError(undefined);
    try {
      if (save && operation) {
        const saved = await api.confirmRecovery(projectId, script.id, { selection, previewDigest: operation.digest }, operation.key);
        if (!active.current) return;
        if (saved.parentScriptId !== script.id || !saved.scriptId || !Number.isSafeInteger(saved.version)
          || (saved.mode === "append" ? saved.scriptId !== script.id || saved.version !== selection.baseVersion + 1
            : saved.mode !== "fork" || saved.scriptId === script.id || saved.version !== 1)) throw new Error("Invalid recovery receipt");
        if (persist()) {
          setResult(saved); setPreview(undefined); setDiscard(false); setChosen(undefined); setOverview(undefined);
          frame.reload(); setReload(n => n + 1);
        }
      } else {
        const proposal = await api.previewRecovery(projectId, script.id, selection);
        if (!active.current) return;
        if (proposal.scriptId !== script.id || proposal.revision !== selection.revision
          || proposal.baseVersion !== selection.baseVersion || proposal.sourceVersion !== selection.sourceVersion
          || !/^[a-f0-9]{64}$/.test(proposal.previewDigest)) throw new Error("Invalid recovery preview");
        setPreview(proposal); setDiscard(false);
      }
    } catch (failure) {
      if (!active.current) return;
      failed(failure);
      if (save && !uncertain(failure) && !denied(failure)) persist({ selection });
      if (failure instanceof CreatorApiError && failure.code === "SCRIPT_RECOVERY_VERSION_CHANGED") {
        frame.reload(); setReload(n => n + 1);
      }
    } finally { submitting.current = false; if (active.current) setBusy(false); }
  }
  const refresh = () => { setOverview(undefined); setPreview(undefined); frame.reload(); setReload(n => n + 1); };
  const editable = frame.canManage && !script.lockedVersionNumber;
  const editor = <button type="button" className={button + " mt-3"} disabled={externalBusy || frame.blocked || !frame.canNavigate}
    onClick={() => frame.navigate("source", script.id)}>{t(editable ? "openEditor" : "viewText")}</button>;
  const clear = () => {
    if (persist()) { setChosen(undefined); setPreview(undefined); setDiscard(false); setError(undefined); refresh(); }
  };
  return <section className={card} aria-label={t("title")}>
    <h2 className="text-lg font-semibold">{t("title")}</h2>
    {error || localError ? <div className="mt-3 text-sm text-amber-200" role="alert">
      {error ? <p>{forbidden ? t("denied") : error instanceof CreatorApiError ? error.message : t("network")}</p> : null}
      {localError ? <p>{localError}</p> : null}
    </div> : null}
    {error || forbidden || conflict ? <button type="button" className={button + " mt-3"} disabled={busy || externalBusy} onClick={refresh}>{t("refresh")}</button> : null}
    {!forbidden && <>
      {result ? <div role="status" className="mt-3 text-emerald-200"><p>{t(result.mode === "fork" ? "savedFork" : "saved", { version: result.version })}</p>
        {result.mode === "fork" ? <button type="button" className={button + " mt-2"} disabled={!frame.canNavigate || frame.blocked} onClick={() => frame.navigate("source", result.scriptId)}>{t("openCopy")}</button> : null}</div> : null}
      {!overview && !error ? <p role="status" className="mt-3 text-sm text-slate-400">{t("loading")}</p> : null}
      {overview && <>
        {script.recoveryOrigin ? <p className="mt-3 text-sm text-slate-400">{t("origin", { base: script.recoveryOrigin.base_version, source: script.recoveryOrigin.source_version })} <button type="button" className="text-violet-300 underline" disabled={frame.blocked || !frame.canNavigate} onClick={() => frame.navigate("versions", script.recoveryOrigin!.parent_script_id)}>{t("openOriginal")}</button></p> : null}
        {script.versions.length < 2 && !draft ? <div className="mt-3 text-sm text-slate-400"><p>{t(script.lockedVersionNumber ? "onlyOneFrozen" : "onlyOne", { version: head.number })}</p>{editor}</div> : <>
          <p className="mt-2 text-sm leading-7 text-slate-400">{t("intro")}</p>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 sm:items-end">
            <label className="text-sm">{t("sourceVersion")}<select className={field} value={sourceVersion ?? ""} disabled={Boolean(draft) || busy || externalBusy}
              onChange={e => { setChosen(Number(e.target.value)); setResult(undefined); setPreview(undefined); setError(undefined); }}>
              {script.versions.filter(v => v.number < head.number).map(v => <option key={v.number} value={v.number}>v{v.number} · {v.changeSummary}</option>)}</select></label>
            <p className="pb-2 text-sm text-slate-300">{t("base", { version: head.number })}</p>
          </div>
          <p className="mt-3 text-sm text-violet-200">{t(overview.mode === "fork" ? "fork" : "append", { version: head.number + 1 })}</p>
          {!overview.canConfirm ? <p className="mt-3 text-sm text-amber-200">{t("readonly")}</p> : null}
          {conflict ? <p role="alert" className="mt-3 text-sm text-amber-200">{t("conflict")}</p> : null}
          {source && base && (source.scenes.length || base.scenes.length) ? <>
            {overview.canConfirm && <ChangePicker key={source.number + ":" + base.number} source={source} base={base}
              disabled={locked} changes={changes} onAdd={change => edit({ changes: [...changes, change] })} />}
          </> : <div className="mt-4 text-sm text-slate-400"><p>{t("empty")}</p>{editor}</div>}
          {changes.length ? <div className="mt-5">
            <h3 className="text-sm font-medium">{t("queue", { count: changes.length })}</h3>
            <ol className="mt-2 space-y-2">{changes.map((c, i) => <li key={i} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-black/20 px-3 py-2 text-sm">
              <span>{t(c.action)} · {t(c.part)} · {c.sourceId ? "v" + sourceVersion + " " + c.sourceId + " → " : ""}{c.targetId === "@start" ? t("start") : c.targetId}</span>
              <button type="button" className="min-h-8 px-2 text-slate-400 underline" disabled={locked} onClick={() => edit({ changes: changes.filter((_, index) => index !== i) })}>{t("drop")}</button>
            </li>)}</ol>
          </div> : null}
          {draft ? <div className="mt-4 space-y-3">
            <label className="block text-sm">{t("reason")}<textarea className={field} value={draft.selection.reason} maxLength={1000} disabled={locked} placeholder={t("reasonHint")} onChange={e => edit({ reason: e.target.value })} /></label>
            {pending ? <><p role="status" className="text-sm text-amber-200">{t("pending")}</p>
              <button type="button" className={primary} disabled={busy || externalBusy || !frame.canManage} onClick={() => void run(true)}>{t("retry")}</button></>
              : <>
                {!preview || conflict ? <button type="button" className={primary} disabled={locked || !changes.length || !draft.selection.reason.trim()} onClick={() => void run(false)}>{t("preview")}</button>
                  : <><RecoveryReview preview={preview} /><button type="button" className={primary} disabled={locked} onClick={() => void run(true)}>{t(preview.mode === "fork" ? "confirmFork" : "confirmAppend", { version: preview.version })}</button></>}
                <button type="button" className={button + " ml-2"} disabled={busy || externalBusy} onClick={() => discard ? clear() : setDiscard(true)}>{t(discard ? "discardAgain" : "discard")}</button>
                {discard ? <button type="button" className={button + " ml-2"} onClick={() => setDiscard(false)}>{t("keep")}</button> : null}
              </>}
          </div> : null}
        </>}
      </>}
    </>}
  </section>;
}
