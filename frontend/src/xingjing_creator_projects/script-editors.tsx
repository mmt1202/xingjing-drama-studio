import { useEffect, useState } from "react";
import { useScriptTranslation } from "./script-i18n";
import {
  AiLimitNotice, NextStep, ScriptPageFrame, TextPreview, ValidationSummary, aiCharacterLimit, characterCount,
  completeScript, contentOf, currentVersion, scriptButton, scriptField, scriptPanel, scriptPrimary,
  useScriptMutation, type ScriptFrameContext, type ScriptPageProps,
} from "./script-workflow";
import { MutationFeedback, VersionText } from "./script-views";
import { AiReadinessNotice, ManualScriptFormat } from "./ScriptExperience";
export { AiDirectorPage } from "./DirectorWorkspace";

function DiscardInput({ disabled, uncertain, onDiscard }: { disabled: boolean; uncertain: boolean; onDiscard: () => void }) {
  const { t } = useScriptTranslation(), [confirm, setConfirm] = useState(false);
  return confirm ? <div className="mt-4 rounded-xl border border-amber-300/30 p-4"><p className="text-sm leading-6 text-amber-100">{t(uncertain ? "uncertainDiscard" : "discardConfirm")}</p><div className="mt-4 flex gap-3"><button type="button" className={scriptButton} disabled={disabled} onClick={() => { setConfirm(false); onDiscard(); }}>{t("discardYes")}</button><button type="button" className={scriptButton} disabled={disabled} onClick={() => setConfirm(false)}>{t("keep")}</button></div></div> : <button type="button" className={scriptButton + " mt-4"} disabled={disabled} onClick={() => setConfirm(true)}>{t("cancel")}</button>;
}

export function OriginalSourcePage(props: ScriptPageProps) {
  const [blocked, setBlocked] = useState(false);
  return <ScriptPageFrame {...props} page="source" blocked={blocked}>{(frame) => <SourceEditor key={frame.script!.id} {...props} frame={frame} onBlocked={setBlocked} />}</ScriptPageFrame>;
}
interface TextDraft { baseRevision: number; baseContent: string; value: string; summary: string }
function SourceEditor({ api, projectId, frame, onBlocked }: ScriptPageProps & { frame: ScriptFrameContext; onBlocked: (value: boolean) => void }) {
  const { t } = useScriptTranslation(), script = frame.script!, mutation = useScriptMutation();
  const [draft, setDraft] = useState<TextDraft>(), [excerpt, setExcerpt] = useState("");
  const value = draft?.value ?? contentOf(script), summary = draft?.summary ?? "";
  const dirty = Boolean(draft && (draft.value !== draft.baseContent || draft.summary)) || Boolean(excerpt);
  const busy = Boolean(mutation.pending), mismatch = Boolean(draft && draft.baseRevision !== script.revision);
  const disabled = !frame.canManage || Boolean(script.lockedVersionNumber) || busy || mutation.uncertain;
  useEffect(() => { onBlocked(dirty || busy || mutation.uncertain); return () => onBlocked(false); }, [dirty, busy, mutation.uncertain, onBlocked]);
  const edit = (patch: Partial<TextDraft>) => { setDraft((previous) => ({ baseRevision: script.revision, baseContent: contentOf(script), value: contentOf(script), summary: "", ...previous, ...patch })); mutation.clear(); };
  const save = () => {
    if (!draft || disabled || mismatch || excerpt || !value.trim() || !dirty) return;
    const original = draft;
    void mutation.run(script.id, () => api.scriptAction(projectId, "revise", { content: original.value, changeSummary: original.summary.trim() || t("defaultSummary") }, original.baseRevision, script.id), (document) => {
      if (!completeScript(document)) throw new Error(t("invalidResponse")); frame.accept(document); setDraft(undefined); setExcerpt("");
    });
  };
  return <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("sourceHelp")}</p>{script.lockedVersionNumber ? <p className="mt-4 text-sm leading-7 text-amber-200">{t("frozenEdit")}</p> : null}
    <ManualScriptFormat /><details className="mt-5"><summary className="cursor-pointer text-sm text-violet-200">{t("original")} · {t("originalLength", { count: characterCount(script.sourceDocument.content) })}</summary><div className="mt-3"><TextPreview text={script.sourceDocument.content} label={t("original")} /></div></details>
    {currentVersion(script)?.sourcePreamble ? <div className="mt-5"><TextPreview text={currentVersion(script)!.sourcePreamble!} label={t("preamble")} /></div> : null}
    <form className="mt-5 space-y-5" onSubmit={(event) => { event.preventDefault(); save(); }}><label className="block text-sm">{t("workingText")}<textarea className={scriptField + " h-80 resize-y"} value={value} disabled={disabled} onChange={(event) => edit({ value: event.target.value })} /></label><p className="text-xs text-slate-500">{t("textLength", { count: characterCount(value) })}</p>{characterCount(value) > aiCharacterLimit(script) ? <p className="text-xs leading-6 text-amber-200">{t("aiLimit", { limit: aiCharacterLimit(script) })}</p> : null}
      <details className="rounded-xl border border-white/10 p-4"><summary className="cursor-pointer text-sm">{t("excerpt")}</summary><p className="mt-3 text-xs leading-6 text-slate-500">{t("excerptHelp")}</p><label className="mt-3 block text-sm">{t("excerpt")}<textarea className={scriptField + " h-44"} value={excerpt} disabled={disabled} onChange={(event) => setExcerpt(event.target.value)} /></label><div className="mt-3 flex flex-wrap gap-3"><button type="button" className={scriptButton} disabled={disabled || !excerpt.trim()} onClick={() => { edit({ value: excerpt }); setExcerpt(""); }}>{t("applyExcerpt")}</button><button type="button" className={scriptButton} disabled={disabled || !excerpt} onClick={() => setExcerpt("")}>{t("clearExcerpt")}</button></div></details>
      <label className="block text-sm">{t("changeSummary")}<input className={scriptField} maxLength={1000} value={summary} disabled={disabled} onChange={(event) => edit({ summary: event.target.value })} /></label>{excerpt ? <p role="status" className="text-xs text-amber-200">{t("excerptNotApplied")}</p> : null}{!value.trim() ? <p role="status" className="text-sm text-amber-200">{t("emptyContent")}</p> : null}
      {mismatch ? <div className="rounded-xl border border-amber-300/30 p-4"><p className="text-sm text-amber-100">{t("conflictBase", { base: draft?.baseRevision, current: script.revision })}</p><details className="mt-4"><summary className="cursor-pointer text-sm">{t("conflictLatest")}</summary><div className="mt-3"><TextPreview text={contentOf(script)} /></div></details><p className="mt-4 text-xs leading-6 text-slate-400">{t("rebaseHelp")}</p><button type="button" className={scriptButton + " mt-3"} disabled={disabled} onClick={() => { setDraft((previous) => previous ? { ...previous, baseRevision: script.revision, baseContent: contentOf(script) } : previous); mutation.clear(); }}>{t("rebase")}</button></div> : null}
      <button className={scriptPrimary} disabled={disabled || mismatch || !dirty || !value.trim() || Boolean(excerpt)}>{t(busy ? "saving" : "save")}</button>
    </form><div className="mt-4"><MutationFeedback mutation={mutation} reload={frame.reload} /></div>{dirty || mutation.uncertain ? <DiscardInput disabled={busy} uncertain={mutation.uncertain} onDiscard={() => { setDraft(undefined); setExcerpt(""); mutation.clear(); }} /> : null}<ValidationSummary script={script} /><NextStep frame={frame} step="script-lab" label="openLab" /><NextStep frame={frame} step="versions" label="openVersions" /></section>;
}

export function AiRewritePage(props: ScriptPageProps) {
  const [blocked, setBlocked] = useState(false);
  return <ScriptPageFrame {...props} page="rewrite" blocked={blocked}>{(frame) => <RewriteEditor key={frame.script!.id} {...props} frame={frame} onBlocked={setBlocked} />}</ScriptPageFrame>;
}
function RewriteEditor({ api, projectId, frame, onBlocked }: ScriptPageProps & { frame: ScriptFrameContext; onBlocked: (value: boolean) => void }) {
  const { t } = useScriptTranslation(), script = frame.script!, mutation = useScriptMutation();
  const [draft, setDraft] = useState<{ instruction: string; baseRevision: number }>();
  const instructionLimit = 2000, instructionLength = characterCount(draft?.instruction ?? "");
  const busy = Boolean(mutation.pending), dirty = Boolean(draft?.instruction), mismatch = Boolean(draft && draft.baseRevision !== script.revision);
  const disabled = !frame.canManage || Boolean(script.lockedVersionNumber) || busy || mutation.uncertain;
  useEffect(() => { onBlocked(dirty || busy || mutation.uncertain); return () => onBlocked(false); }, [dirty, busy, mutation.uncertain, onBlocked]);
  return <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("rewriteHelp")}</p>{script.lockedVersionNumber ? <p className="mt-4 text-sm text-amber-200">{t("frozenEdit")}</p> : null}<AiReadinessNotice frame={frame} /><AiLimitNotice script={script} /><form className="mt-5 space-y-4" onSubmit={(event) => {
    event.preventDefault(); if (disabled || !frame.aiReady || mismatch || !draft?.instruction.trim() || instructionLength > instructionLimit || characterCount(contentOf(script)) > aiCharacterLimit(script)) return;
    const original = draft;
    void mutation.run(script.id, () => api.scriptAction(projectId, "rewrite", { instruction: original.instruction.trim() }, original.baseRevision, script.id), (document) => { if (!completeScript(document)) throw new Error(t("invalidResponse")); frame.accept(document); setDraft(undefined); });
  }}><label className="block text-sm">{t("instruction")}<textarea className={scriptField + " min-h-40"} placeholder={t("instructionPlaceholder")} maxLength={4000} value={draft?.instruction ?? ""} disabled={disabled} onChange={(event) => { setDraft((previous) => ({ baseRevision: previous?.baseRevision ?? script.revision, instruction: event.target.value })); mutation.clear(); }} /></label><p className={"text-xs " + (instructionLength > instructionLimit ? "text-amber-200" : "text-slate-500")}>{t("instructionCount", { count: instructionLength, limit: instructionLimit })}</p>{instructionLength > instructionLimit ? <p role="status" className="text-xs text-amber-200">{t("instructionTooLarge", { limit: instructionLimit })}</p> : null}{mismatch ? <div><p className="text-sm text-amber-200">{t("conflictBase", { base: draft?.baseRevision, current: script.revision })}</p><VersionText script={script} /><p className="mt-3 text-xs text-slate-500">{t("rebaseHelp")}</p><button type="button" className={scriptButton + " mt-3"} disabled={disabled} onClick={() => { setDraft((previous) => previous ? { ...previous, baseRevision: script.revision } : previous); mutation.clear(); }}>{t("rebase")}</button></div> : null}<button className={scriptPrimary} disabled={disabled || !frame.aiReady || mismatch || !draft?.instruction.trim() || instructionLength > instructionLimit || characterCount(contentOf(script)) > aiCharacterLimit(script)}>{t(busy ? "rewriting" : "rewriteAction")}</button></form><div className="mt-4"><MutationFeedback mutation={mutation} reload={frame.reload} allowRetry={frame.aiReady || mutation.uncertain} /></div>{dirty || mutation.uncertain ? <DiscardInput disabled={busy} uncertain={mutation.uncertain} onDiscard={() => { setDraft(undefined); mutation.clear(); }} /> : null}<p className="mt-5 text-xs text-slate-500">{t("resultSummary")} · {currentVersion(script)?.changeSummary || "—"}</p><ValidationSummary script={script} /><VersionText script={script} /><NextStep frame={frame} step="script-lab" label="openLab" /><NextStep frame={frame} step="versions" label="openVersions" /></section>;
}
