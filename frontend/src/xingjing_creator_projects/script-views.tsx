import { ScriptRecoveryPanel } from "@/xingjing_script_recovery/ScriptRecoveryPanel";
import { getSessionEpoch } from "@/utils/auth";
import { ScriptSourceMapper } from "@/xingjing_source_planning/ScriptSourceMapper";
import { useEffect, useState } from "react";
import { useScriptTranslation } from "./script-i18n";
import { useRecognitionTranslation } from "./recognition-i18n";
import { isSourceFilePath } from "./source-input";
import { AiReadinessNotice, ManualScriptFormat } from "./ScriptExperience";
import { ScriptRecognitionPanel } from "./ScriptRecognitionPanel";
import {
  AiLimitNotice, NextStep, MutationFeedback, ScriptPageFrame, TextPreview, ValidationSummary,
  characterCount, completeScript, contentOf, currentVersion, scriptButton, scriptField, scriptPanel, scriptPrimary,
  aiCharacterLimit, useScriptMutation, type ScriptFrameContext, type ScriptPageProps,
} from "./script-workflow";
import type { ScriptComparison, ScriptDocument } from "./types";

export { MutationFeedback } from "./script-workflow";

export { ScriptImportPage } from "./ScriptImportPage";

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
  const { t: recognition } = useRecognitionTranslation();
  const [recognitionBusy, setRecognitionBusy] = useState(false);
  const tooLarge = characterCount(contentOf(script)) > aiCharacterLimit(script);
  const hasScenes = Boolean(currentVersion(script)?.scenes?.length);
  const writing = Boolean(mutation.pending || mutation.uncertain);
  useEffect(() => {
    onBlocked(writing || recognitionBusy);
    return () => onBlocked(false);
  }, [writing, recognitionBusy, onBlocked]);
  const analyze = () => void mutation.run(script.id, () => api.scriptAction(projectId, "analyze", {}, script.revision, script.id), value => {
    if (!completeScript(value) || value.id !== script.id || value.projectId !== projectId) throw new Error(t("invalidResponse"));
    frame.accept(value);
  });
  return <section className={scriptPanel}>
    <ScriptRecognitionPanel key={script.revision} api={api} script={script} frame={frame} page="lab" busy={writing} onBlocked={setRecognitionBusy} />
    {script.lockedVersionNumber ? <p className="mt-4 text-sm text-amber-200">{t("frozenEdit")}</p> : !tooLarge && !isSourceFilePath(contentOf(script)) ? <>
      <AiReadinessNotice frame={frame} />
      <button type="button" className={scriptPrimary + " mt-4"} disabled={!frame.canManage || !frame.aiReady || writing || recognitionBusy} onClick={analyze}>{mutation.pending ? t("analyzing") : hasScenes ? t("analyze") : recognition("adaptWithAi")}</button>
    </> : !script.recognition && !api.inspectScriptSource ? <AiLimitNotice script={script} /> : null}
    <MutationFeedback mutation={mutation} reload={frame.reload} allowRetry={frame.aiReady || mutation.uncertain} />
    {hasScenes && <><ValidationSummary script={script} /><AnalysisSummary script={script} /></>}
    {!hasScenes && !tooLarge && !script.lockedVersionNumber && <ManualScriptFormat />}
    <VersionText script={script} /><CheckNext frame={frame} currentStep="script-lab" />
  </section>;
}

export function DirectorAssetsPage(props: ScriptPageProps) {
  const { t } = useScriptTranslation();
  return <ScriptPageFrame {...props} page="director-assets" blocked={false}>{(frame) => <section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("assetsHelp")}</p><AnalysisSummary script={frame.script!} /><NextStep frame={frame} step="episodes" label="openEpisodes" /><NextStep frame={frame} step="ai-director" label="openDirector" /></section>}</ScriptPageFrame>;
}

export function SourceMappingPage(props: ScriptPageProps) {
  const [blocked, setBlocked] = useState(false);
  return <ScriptPageFrame {...props} page="source-mapping" blocked={blocked}>{(frame) => <ScriptSourceMapper key={`${frame.context.workspace.id}:${frame.context.user.id}:${frame.script!.id}`} frame={frame} projectId={props.projectId} onBlocked={setBlocked} />}</ScriptPageFrame>;
}

export function ScriptVersionComparePage(props: ScriptPageProps) {
  const [blocked, setBlocked] = useState(false);
  return <ScriptPageFrame {...props} page="versions" blocked={blocked}>{(frame) => <VersionActions key={frame.script!.id} {...props} frame={frame} onBlocked={setBlocked} />}</ScriptPageFrame>;
}
function VersionActions({ api, projectId, frame, onBlocked }: ScriptPageProps & { frame: ScriptFrameContext; onBlocked: (value: boolean) => void }) {
  const { t } = useScriptTranslation(), script = frame.script!, head = currentVersion(script), mutation = useScriptMutation();
  const [baseline, setBaseline] = useState(script.versions.at(-2)?.number ?? head?.number), [candidate, setCandidate] = useState(head?.number);
  const [comparison, setComparison] = useState<ScriptComparison>(), [confirm, setConfirm] = useState(false);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const busy = Boolean(mutation.pending);
  useEffect(() => { onBlocked(recoveryBlocked || busy || confirm || mutation.uncertain); return () => onBlocked(false); }, [recoveryBlocked, busy, confirm, mutation.uncertain, onBlocked]);
  const choose = (which: "baseline" | "candidate", value: string) => { if (which === "baseline") setBaseline(Number(value)); else setCandidate(Number(value)); setComparison(undefined); mutation.clear(); };
  return <><section className={scriptPanel}><p className="text-sm leading-7 text-slate-400">{t("versionHelp")}</p><div className="mt-5 grid gap-4 sm:grid-cols-2">{(["baseline", "candidate"] as const).map((which) => <label key={which} className="text-sm">{t(which)}<select className={scriptField} value={(which === "baseline" ? baseline : candidate) ?? ""} disabled={recoveryBlocked || busy || confirm || mutation.uncertain} onChange={(event) => choose(which, event.target.value)}>{script.versions.map((version) => <option key={version.number} value={version.number}>v{version.number} · {version.changeSummary}</option>)}</select></label>)}</div>
    {baseline === candidate ? <p className="mt-3 text-xs text-slate-500">{t("sameVersion")}</p> : null}
    <button type="button" className={scriptButton + " mt-4"} disabled={recoveryBlocked || frame.reading || busy || confirm || mutation.uncertain || baseline === undefined || candidate === undefined || baseline === candidate} onClick={() => void mutation.run("compare", () => api.scriptAction(projectId, "compareVersions", { baselineVersion: baseline, candidateVersion: candidate }, undefined, script.id), (value) => { if (!("changedFields" in value) || value.baselineVersion !== baseline || value.candidateVersion !== candidate || !Array.isArray(value.changedFields)) throw new Error(t("invalidResponse")); setComparison(value); })}>{t(mutation.pending === "compare" ? "comparing" : "compare")}</button>
    {comparison ? <div className="mt-4 rounded-xl border border-white/10 p-4"><h3 className="text-sm">{t("changedFields")} · v{comparison.baselineVersion} → v{comparison.candidateVersion}</h3>{comparison.changedFields.length ? <ul className="mt-3 space-y-2 text-xs text-slate-400">{comparison.changedFields.map((field) => <li key={field} className="break-all">{t("field_" + field, { ns: "scriptRecovery", defaultValue: field })}</li>)}</ul> : <p className="mt-3 text-sm text-slate-400">{t("noDifference")}</p>}</div> : null}
    {comparison && <div className="mt-4 grid gap-4 lg:grid-cols-2">{(["baseline", "candidate"] as const).map(side => {
      const number = side === "baseline" ? comparison.baselineVersion : comparison.candidateVersion;
      return <div key={side} className="min-w-0"><TextPreview label={t(side) + " · v" + number} text={script.versions.find(v => v.number === number)?.sourceContent ?? ""} /></div>;
    })}</div>}
    </section>
    <ScriptRecoveryPanel key={JSON.stringify([getSessionEpoch(), frame.context.workspace.id, frame.context.user.id, projectId, script.id])}
      frame={frame} projectId={projectId} externalBusy={busy || confirm || mutation.uncertain} onBlocked={setRecoveryBlocked} />
    <section className={scriptPanel}><ValidationSummary script={script} /><div className="mt-5 border-t border-white/10 pt-5"><p className="text-sm leading-7 text-slate-400">{t("freezeHelp")}</p>{script.lockedVersionNumber ? <p className="mt-3 text-violet-200">{t("frozenVersion", { number: script.lockedVersionNumber })}</p> : <><p className="mt-3 text-sm text-amber-200">{head?.validationErrors.length ? t("freezeBlocked") : null}</p>{confirm ? <div className="mt-4 rounded-xl border border-violet-300/30 p-4"><p className="text-sm">{t("freezeConfirm", { number: head?.number })}</p><div className="mt-4 flex flex-wrap gap-3"><button type="button" className={scriptPrimary} disabled={busy || !frame.canManage || !head || Boolean(head.validationErrors.length)} onClick={() => void mutation.run(script.id, () => api.scriptAction(projectId, "freeze", {}, script.revision, script.id), (value) => { if (!completeScript(value)) throw new Error(t("invalidResponse")); frame.accept(value); setConfirm(false); })}>{t(busy ? "freezing" : "freeze", { number: head?.number })}</button><button type="button" className={scriptButton} disabled={busy} onClick={() => { setConfirm(false); mutation.clear(); }}>{t("keep")}</button></div></div> : <button type="button" className={scriptPrimary + " mt-4"} disabled={recoveryBlocked || !frame.canManage || busy || !head || Boolean(head.validationErrors.length)} onClick={() => setConfirm(true)}>{t("freeze", { number: head?.number })}</button>}</> }</div><div className="mt-4"><MutationFeedback mutation={mutation} reload={frame.reload} /></div><CheckNext frame={frame} currentStep="versions" /></section></>;
}
