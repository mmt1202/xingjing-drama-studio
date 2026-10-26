import { useEffect, useRef, useState } from "react";
import { CreatorApiError } from "./api";
import { useScriptTranslation } from "./script-i18n";
import { AiReadinessNotice, ManualScriptFormat } from "./ScriptExperience";
import {
  AiLimitNotice, NextStep, SavedNotice, ScriptError, ScriptPageFrame, TextPreview, ValidationSummary,
  characterCount, completeScript, contentOf, currentVersion, scriptButton, scriptField, scriptPanel, scriptPrimary,
  aiCharacterLimit, useScriptMutation, type ScriptFrameContext, type ScriptPageProps,
} from "./script-workflow";
import type { ScriptComparison, ScriptDocument, ScriptTextMapping } from "./types";

export function MutationFeedback({ mutation, reload, allowRetry = true }: { mutation: ReturnType<typeof useScriptMutation>; reload: () => void; allowRetry?: boolean }) {
  const { t } = useScriptTranslation();
  const [confirm, setConfirm] = useState(false);
  const conflict = mutation.error?.reason instanceof CreatorApiError && mutation.error.reason.code === "VERSION_CONFLICT";
  return <>{mutation.error ? <ScriptError error={mutation.error.reason} retry={conflict || mutation.pending || !allowRetry ? undefined : () => void mutation.retry()} reload={mutation.pending ? undefined : reload} /> : null}{mutation.uncertain && !mutation.pending ? <div className="mt-3">{confirm ? <><p className="text-xs leading-6 text-amber-200">{t("uncertainDiscard")}</p><div className="mt-3 flex gap-2"><button type="button" className={scriptButton} onClick={() => { setConfirm(false); mutation.clear(); }}>{t("acknowledgeResult")}</button><button type="button" className={scriptButton} onClick={() => setConfirm(false)}>{t("keep")}</button></div></> : <button type="button" className={scriptButton} onClick={() => setConfirm(true)}>{t("reviewRecovery")}</button>}</div> : null}{mutation.saved && mutation.saved !== "compare" ? <SavedNotice /> : null}</>;
}

export function ScriptImportPage(props: ScriptPageProps) {
  const { t } = useScriptTranslation();
  const [file, setFile] = useState<File>(), [title, setTitle] = useState(""), [content, setContent] = useState("");
  const [localError, setLocalError] = useState(""), [success, setSuccess] = useState<{ id: string; document?: ScriptDocument }>();
  const [detailError, setDetailError] = useState<unknown>();
  const input = useRef<HTMLInputElement>(null), submitted = useRef(false), completed = useRef(false);
  const [discard, setDiscard] = useState(false);
  const mutation = useScriptMutation();
  const blocked = Boolean(mutation.pending || (!success && (file || title || content || mutation.uncertain)));
  useEffect(() => {
    if (!success || success.document || !props.api.getScript) return;
    const abort = new AbortController();
    void props.api.getScript(props.projectId, success.id, abort.signal).then((document) => {
      if (abort.signal.aborted) return;
      if (!completeScript(document) || document.id !== success.id || document.projectId !== props.projectId) throw new Error(t("invalidResponse"));
      setSuccess({ id: document.id, document });
    }).catch((error: unknown) => { if (!abort.signal.aborted) setDetailError(error); });
    return () => abort.abort();
  }, [success, props.api, props.projectId, t]);
  return <ScriptPageFrame {...props} page="script-import" blocked={blocked}>{(frame) => {
    const locked = !frame.canManage || Boolean(mutation.pending) || mutation.uncertain;
    const submit = () => {
      if (submitted.current || completed.current || success || locked) return;
      if (!file && !content.trim()) { setLocalError(t("importRequired")); return; }
      submitted.current = true; setLocalError("");
      void mutation.run("import", () => file
        ? props.api.importScriptFile(props.projectId, title.trim() || file.name.replace(/\.[^.]+$/, ""), file)
        : props.api.scriptAction(props.projectId, "import", { title: title.trim() || t("unnamed"), filename: "manual-script.txt", mediaType: "text/plain", content }), (value) => {
        if (!("id" in value) || typeof value.id !== "string") throw new Error(t("invalidResponse"));
        completed.current = true;
        setSuccess({ id: value.id, document: completeScript(value) ? value : undefined });
        setFile(undefined); setTitle(""); setContent(""); if (input.current) input.current.value = "";
        if (completeScript(value)) frame.accept(value);
      }).finally(() => { submitted.current = false; });
    };
    return <section className={scriptPanel + " max-w-4xl"}><p className="mb-5 text-sm leading-7 text-slate-400">{t("importHelp")}</p>{success ? <>
      <h2 role="status" className="text-lg font-semibold text-emerald-300">{t("imported")}</h2><p className="mt-3 text-sm text-slate-400">{t("importTerminal")}</p><p className="mt-3 break-all text-xs text-slate-500">{t("scriptId")}：{success.id}</p>
      {success.document ? <><h3 className="mt-4 font-medium">{success.document.title}</h3><p className="mt-2 text-xs text-slate-400">{t("originalLength", { count: characterCount(success.document.sourceDocument.content) })}</p><ValidationSummary script={success.document} /><AiLimitNotice script={success.document} /></> : <p className="mt-4 text-sm text-amber-200">{t("detailPending")}</p>}
      {detailError ? <ScriptError error={detailError} reload={() => { setDetailError(undefined); setSuccess({ id: success.id }); }} /> : null}
      <NextStep frame={frame} step="source" label="openSource" scriptId={success.id} /><NextStep frame={frame} step="script-lab" label="openLab" scriptId={success.id} />
      <button type="button" className={scriptButton + " mt-5"} disabled={!frame.canManage || Boolean(mutation.pending)} onClick={() => { completed.current = false; setSuccess(undefined); setDetailError(undefined); mutation.clear(); }}>{t("importAgain")}</button>
    </> : <form onSubmit={(event) => { event.preventDefault(); submit(); }} className="space-y-5">
      <label className="block text-sm">{t("title")}<input className={scriptField} maxLength={200} value={title} disabled={locked} onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="block text-sm">{t("file")}<input ref={input} className="mt-3 block max-w-full text-sm text-slate-400" type="file" accept=".txt,.md,.markdown,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document" disabled={locked} onChange={(event) => {
        const chosen = event.target.files?.[0]; setLocalError("");
        if (chosen && !/\.(txt|md|markdown|docx)$/i.test(chosen.name)) { setLocalError(t("supported")); event.target.value = ""; setFile(undefined); return; }
        setFile(chosen); if (chosen && !title.trim()) setTitle(chosen.name.replace(/\.[^.]+$/, ""));
      }} /></label><p className="text-xs text-slate-500">{t("supported")}</p>
      <label className="block text-sm">{t("paste")}<textarea className={scriptField + " min-h-56"} value={content} disabled={locked || Boolean(file)} onChange={(event) => setContent(event.target.value)} /></label>
      {localError ? <p role="alert" className="text-sm text-amber-200">{localError}</p> : null}
      <button className={scriptPrimary} disabled={locked || (!file && !content.trim())}>{t(mutation.pending ? "importing" : "importAction")}</button>
      <MutationFeedback mutation={mutation} reload={frame.reload} />
      {file || title || content ? <div>{discard ? <><p className="text-xs leading-6 text-amber-200">{t(mutation.uncertain ? "uncertainDiscard" : "discardConfirm")}</p><div className="mt-3 flex flex-wrap gap-2"><button type="button" className={scriptButton} disabled={Boolean(mutation.pending)} onClick={() => { setFile(undefined); setTitle(""); setContent(""); if (input.current) input.current.value = ""; setDiscard(false); mutation.clear(); }}>{t("discardYes")}</button><button type="button" className={scriptButton} disabled={Boolean(mutation.pending)} onClick={() => setDiscard(false)}>{t("keep")}</button></div></> : <button type="button" className={scriptButton} disabled={Boolean(mutation.pending)} onClick={() => setDiscard(true)}>{t("cancel")}</button>}</div> : null}
    </form>}</section>;
  }}</ScriptPageFrame>;
}

export function AnalysisSummary({ script }: { script: ScriptDocument }) {
  const { t } = useScriptTranslation(), analysis = currentVersion(script)?.analysis;
  if (!analysis) return <p className="mt-4 text-sm text-slate-400">{t("noAnalysis")}</p>;
  const metrics = [["characterCount", analysis.characterCount], ["sceneCount", analysis.sceneCount], ["dialogueCount", analysis.dialogueCount], ["episodeCount", analysis.estimatedEpisodeCount], ["duration", analysis.estimatedDurationMs / 60_000]] as const;
  return <div className="mt-5 space-y-5"><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{metrics.map(([label, value]) => <div key={label} className="rounded-xl border border-white/10 p-3"><p className="text-xs text-slate-500">{t(label)}</p><p className="mt-2 text-xl">{typeof value === "number" && Number.isFinite(value) ? Number.isInteger(value) ? value : value.toFixed(1) : "—"}</p></div>)}</div><div className="grid gap-5 md:grid-cols-2">
    <article><h3 className="text-sm font-medium">{t("characters")}</h3><p className="mt-2 text-sm leading-7 text-slate-400">{analysis.characters?.map((item) => `${item.name} (${item.appearances})`).join(" · ") || t("noResult")}</p></article>
    <article><h3 className="text-sm font-medium">{t("locations")} / {t("props")}</h3><p className="mt-2 text-sm leading-7 text-slate-400">{[...(analysis.locations ?? []), ...(analysis.props ?? [])].join(" · ") || t("noResult")}</p></article>
    <article><h3 className="text-sm font-medium">{t("relationships")}</h3><p className="mt-2 text-sm leading-7 text-slate-400">{analysis.relationships?.map((item) => `${item.source} — ${item.relation} — ${item.target}`).join("; ") || t("noResult")}</p></article>
    <article><h3 className="text-sm font-medium">{t("sensitive")}</h3><p className="mt-2 text-sm leading-7 text-slate-400">{analysis.sensitiveTerms?.join(" · ") || t("noSensitive")}</p></article>
  </div>{analysis.storyBeats?.length ? <section><h3 className="text-sm font-medium">{t("beats")}</h3><div className="mt-3 space-y-3">{analysis.storyBeats.map((beat, index) => <article key={index} className="rounded-xl border border-white/10 p-3"><h4 className="text-sm">{beat.label}</h4><p className="mt-2 text-sm leading-6 text-slate-400">{beat.summary}</p></article>)}</div></section> : null}</div>;
}

export function VersionText({ script }: { script: ScriptDocument }) {
  const { t } = useScriptTranslation(), version = currentVersion(script);
  return <details className="mt-5"><summary className="cursor-pointer text-sm text-violet-200">{t("viewText")}</summary><div className="mt-4 space-y-4">{version?.sourcePreamble ? <TextPreview text={version.sourcePreamble} label={t("preamble")} /> : null}<TextPreview text={contentOf(script)} label={t("workingText")} />{version?.scenes?.length ? <details><summary className="cursor-pointer text-xs text-slate-500">{t("sceneCount")} · {version.scenes.length}</summary><div className="mt-3 max-h-96 space-y-4 overflow-auto">{version.scenes.map((scene) => <article key={scene.sceneId} className="rounded-xl border border-white/10 p-3"><h4 className="text-sm">{scene.sceneId} · {scene.heading.location} {scene.heading.timeOfDay} {scene.heading.setting}</h4>{scene.paragraphs.map((paragraph) => <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-slate-400" key={paragraph.paragraphId}>{paragraph.speaker ? paragraph.speaker + ": " : ""}{paragraph.text}</p>)}</article>)}</div></details> : null}</div></details>;
}

function CheckNext({ frame, currentStep }: { frame: ScriptFrameContext; currentStep?: string }) {
  if (!frame.script) return null;
  const script = frame.script;
  const target = script.lockedVersionNumber ? "ai-director" : currentVersion(script)?.validationErrors.length ? "script-lab" : "versions";
  const label = script.lockedVersionNumber ? "openDirector" : currentVersion(script)?.validationErrors.length ? "openLab" : "openVersions";
  return <>{target !== currentStep ? <NextStep frame={frame} step={target} label={label} /> : null}<NextStep frame={frame} step="source" label="openSource" /></>;
}

export function ScriptCheckupPage(props: ScriptPageProps) {
  const { t } = useScriptTranslation();
  return <ScriptPageFrame {...props} page="script-checkup" blocked={false}>{(frame) => <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("checkHelp")}</p><ValidationSummary script={frame.script!} /><AnalysisSummary script={frame.script!} /><VersionText script={frame.script!} /><CheckNext frame={frame} /></section>}</ScriptPageFrame>;
}

export function ScriptLabPage(props: ScriptPageProps) {
  const [blocked, setBlocked] = useState(false);
  return <ScriptPageFrame {...props} page="script-lab" blocked={blocked}>{(frame) => <ScriptLabActions key={frame.script!.id} {...props} frame={frame} onBlocked={setBlocked} />}</ScriptPageFrame>;
}
function ScriptLabActions({ api, projectId, frame, onBlocked }: ScriptPageProps & { frame: ScriptFrameContext; onBlocked: (value: boolean) => void }) {
  const { t } = useScriptTranslation(), mutation = useScriptMutation(), script = frame.script!;
  const tooLarge = characterCount(contentOf(script)) > aiCharacterLimit(script);
  useEffect(() => { onBlocked(Boolean(mutation.pending || mutation.uncertain)); return () => onBlocked(false); }, [mutation.pending, mutation.uncertain, onBlocked]);
  return <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("labHelp")}</p>{script.lockedVersionNumber ? <p className="mt-4 text-sm text-amber-200">{t("frozenEdit")}</p> : null}<AiReadinessNotice frame={frame} /><AiLimitNotice script={script} /><button type="button" className={scriptPrimary + " mt-5"} disabled={!frame.canManage || !frame.aiReady || Boolean(script.lockedVersionNumber) || Boolean(mutation.pending) || mutation.uncertain || tooLarge} onClick={() => void mutation.run(script.id, () => api.scriptAction(projectId, "analyze", {}, script.revision, script.id), (value) => { if (!completeScript(value) || value.id !== script.id) throw new Error(t("invalidResponse")); frame.accept(value); })}>{t(mutation.pending ? "analyzing" : "analyze")}</button><div className="mt-4"><MutationFeedback mutation={mutation} reload={frame.reload} allowRetry={frame.aiReady || mutation.uncertain} /></div><ValidationSummary script={script} /><ManualScriptFormat /><AnalysisSummary script={script} /><VersionText script={script} /><CheckNext frame={frame} currentStep="script-lab" /></section>;
}

export function DirectorAssetsPage(props: ScriptPageProps) {
  const { t } = useScriptTranslation();
  return <ScriptPageFrame {...props} page="director-assets" blocked={false}>{(frame) => <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("assetsHelp")}</p><AnalysisSummary script={frame.script!} /><NextStep frame={frame} step="episodes" label="openEpisodes" /><NextStep frame={frame} step="ai-director" label="openDirector" /></section>}</ScriptPageFrame>;
}

function MappingList({ mappings, text }: { mappings: ScriptTextMapping[]; text: string }) {
  const { t } = useScriptTranslation(), characters = Array.from(text);
  return mappings.length ? <div className="mt-3 max-h-96 space-y-3 overflow-auto">{mappings.map((mapping, index) => <article key={`${mapping.targetId}:${index}`} className="rounded-xl border border-white/10 p-3"><p className="break-all font-mono text-xs text-slate-500">{mapping.targetId} · [{mapping.sourceStart}, {mapping.sourceEnd})</p><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-300">{characters.slice(mapping.sourceStart, Math.min(mapping.sourceEnd, mapping.sourceStart + 280)).join("")}{mapping.sourceEnd - mapping.sourceStart > 280 ? "…" : ""}</p></article>)}</div> : <p className="mt-3 text-sm text-slate-500">{t("noMappings")}</p>;
}
export function SourceMappingPage(props: ScriptPageProps) {
  const { t } = useScriptTranslation();
  return <ScriptPageFrame {...props} page="source-mapping" blocked={false}>{(frame) => {
    const script = frame.script!, version = currentVersion(script);
    return <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("mappingHelp")}</p><h3 className="mt-5 text-sm font-medium">{t("versionPositions")}</h3><MappingList mappings={version?.scriptMappings ?? []} text={version?.sourceContent ?? ""} /><h3 className="mt-5 text-sm font-medium">{t("originalMappings")}</h3>{version?.sourceMappingCoordinateSystem === "original_source" ? <MappingList mappings={version.sourceMappings ?? []} text={script.sourceDocument.content} /> : <p className="mt-3 text-sm text-amber-200">{t("unmapped")}</p>}{version?.unmappedParagraphIds?.length ? <p className="mt-3 text-xs text-slate-500">{t("unmappedCount", { count: version.unmappedParagraphIds.length })}</p> : null}<NextStep frame={frame} step="source" label="openSource" /></section>;
  }}</ScriptPageFrame>;
}

export function ScriptVersionComparePage(props: ScriptPageProps) {
  const [blocked, setBlocked] = useState(false);
  return <ScriptPageFrame {...props} page="versions" blocked={blocked}>{(frame) => <VersionActions key={frame.script!.id} {...props} frame={frame} onBlocked={setBlocked} />}</ScriptPageFrame>;
}
function VersionActions({ api, projectId, frame, onBlocked }: ScriptPageProps & { frame: ScriptFrameContext; onBlocked: (value: boolean) => void }) {
  const { t } = useScriptTranslation(), script = frame.script!, head = currentVersion(script), mutation = useScriptMutation();
  const [baseline, setBaseline] = useState(script.versions.at(-2)?.number ?? head?.number), [candidate, setCandidate] = useState(head?.number);
  const [comparison, setComparison] = useState<ScriptComparison>(), [confirm, setConfirm] = useState(false);
  const busy = Boolean(mutation.pending);
  useEffect(() => { onBlocked(busy || confirm || mutation.uncertain); return () => onBlocked(false); }, [busy, confirm, mutation.uncertain, onBlocked]);
  const choose = (which: "baseline" | "candidate", value: string) => { if (which === "baseline") setBaseline(Number(value)); else setCandidate(Number(value)); setComparison(undefined); mutation.clear(); };
  return <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("versionHelp")}</p><div className="mt-5 grid gap-4 sm:grid-cols-2">{(["baseline", "candidate"] as const).map((which) => <label key={which} className="text-sm">{t(which)}<select className={scriptField} value={(which === "baseline" ? baseline : candidate) ?? ""} disabled={busy || confirm || mutation.uncertain} onChange={(event) => choose(which, event.target.value)}>{script.versions.map((version) => <option key={version.number} value={version.number}>v{version.number} · {version.changeSummary}</option>)}</select></label>)}</div>
    {baseline === candidate ? <p className="mt-3 text-xs text-slate-500">{t("sameVersion")}</p> : null}
    <button type="button" className={scriptButton + " mt-4"} disabled={frame.reading || busy || confirm || mutation.uncertain || baseline === undefined || candidate === undefined || baseline === candidate} onClick={() => void mutation.run("compare", () => api.scriptAction(projectId, "compareVersions", { baselineVersion: baseline, candidateVersion: candidate }, undefined, script.id), (value) => { if (!("changedFields" in value) || value.baselineVersion !== baseline || value.candidateVersion !== candidate || !Array.isArray(value.changedFields)) throw new Error(t("invalidResponse")); setComparison(value); })}>{t(mutation.pending === "compare" ? "comparing" : "compare")}</button>
    {comparison ? <div className="mt-4 rounded-xl border border-white/10 p-4"><h3 className="text-sm">{t("changedFields")} · v{comparison.baselineVersion} → v{comparison.candidateVersion}</h3>{comparison.changedFields.length ? <ul className="mt-3 space-y-2 text-xs text-slate-400">{comparison.changedFields.map((field) => <li key={field} className="break-all">{field}</li>)}</ul> : <p className="mt-3 text-sm text-slate-400">{t("noDifference")}</p>}</div> : null}
    <ValidationSummary script={script} /><div className="mt-5 border-t border-white/10 pt-5"><p className="text-sm leading-7 text-slate-400">{t("freezeHelp")}</p>{script.lockedVersionNumber ? <p className="mt-3 text-violet-200">{t("frozenVersion", { number: script.lockedVersionNumber })}</p> : <><p className="mt-3 text-sm text-amber-200">{head?.validationErrors.length ? t("freezeBlocked") : null}</p>{confirm ? <div className="mt-4 rounded-xl border border-violet-300/30 p-4"><p className="text-sm">{t("freezeConfirm", { number: head?.number })}</p><div className="mt-4 flex flex-wrap gap-3"><button type="button" className={scriptPrimary} disabled={busy || !frame.canManage || !head || Boolean(head.validationErrors.length)} onClick={() => void mutation.run(script.id, () => api.scriptAction(projectId, "freeze", {}, script.revision, script.id), (value) => { if (!completeScript(value)) throw new Error(t("invalidResponse")); frame.accept(value); setConfirm(false); })}>{t(busy ? "freezing" : "freeze", { number: head?.number })}</button><button type="button" className={scriptButton} disabled={busy} onClick={() => { setConfirm(false); mutation.clear(); }}>{t("keep")}</button></div></div> : <button type="button" className={scriptPrimary + " mt-4"} disabled={!frame.canManage || busy || !head || Boolean(head.validationErrors.length)} onClick={() => setConfirm(true)}>{t("freeze", { number: head?.number })}</button>}</> }</div><div className="mt-4"><MutationFeedback mutation={mutation} reload={frame.reload} /></div><CheckNext frame={frame} currentStep="versions" /></section>;
}
