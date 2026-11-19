import { useEffect, useState } from "react";
import type { CreatorProjectsPort, ScriptDocument, ScriptRecognition } from "./types";
import { useRecognitionTranslation } from "./recognition-i18n";
import { narrationDraft } from "./source-input";
import { aiCharacterLimit, completeScript, currentVersion, MutationFeedback, scriptButton, scriptPrimary, useScriptMutation, type ScriptFrameContext } from "./script-workflow";

interface Props {
  api: CreatorProjectsPort; script: ScriptDocument; frame: ScriptFrameContext;
  page: "import" | "lab" | "director"; busy?: boolean; onBlocked?: (value: boolean) => void;
}

function matches(info: ScriptRecognition | undefined, script: ScriptDocument): info is ScriptRecognition {
  return Boolean(info && info.scriptId === script.id && info.revision === script.revision
    && info.version === currentVersion(script)?.number && ["screenplay", "chapters", "prose", "path"].includes(info.kind)
    && [info.characterCount, info.sceneCount, info.chapterCount, info.paragraphCount].every(n => Number.isSafeInteger(n) && n >= 0)
    && Array.isArray(info.chapterTitles));
}

export function ScriptRecognitionPanel({ api, script, frame, page, busy = false, onBlocked }: Props) {
  const { t } = useRecognitionTranslation(), save = useScriptMutation();
  const [loaded, setLoaded] = useState<ScriptRecognition>(), [error, setError] = useState(false), [reload, setReload] = useState(0);
  const [preview, setPreview] = useState<string>();
  const supplied = matches(script.recognition, script) ? script.recognition : undefined;
  const info = supplied ?? (matches(loaded, script) ? loaded : undefined);
  const writing = Boolean(save.pending || save.uncertain);
  useEffect(() => { onBlocked?.(writing); return () => onBlocked?.(false); }, [writing, onBlocked]);
  useEffect(() => {
    if (supplied || !api.inspectScriptSource) return;
    const controller = new AbortController();
    api.inspectScriptSource(script.projectId, script.id, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (!matches(result, script)) throw new Error("SCRIPT_RECOGNITION_CHANGED");
      setLoaded(result); setError(false);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [api, script, supplied, reload]);
  if (!info) {
    if (!api.inspectScriptSource) return null;
    return error ? <div role="alert" className="my-4 text-sm text-amber-200"><p>{t("failed")}</p><button className={scriptButton + " mt-2"} onClick={() => { setError(false); frame.reload(); setReload(n => n + 1); }}>{t("retry")}</button></div>
      : <p role="status" className="my-4 text-sm text-slate-400">{t("loading")}</p>;
  }
  const version = currentVersion(script)!;
  const narrative = info.kind === "chapters" || info.kind === "prose";
  const long = info.characterCount > aiCharacterLimit(script);
  const editable = frame.canManage && !script.lockedVersionNumber && !busy && !writing;
  const needsReparse = info.kind === "screenplay" && (info.requiresReparse || info.sceneCount !== version.scenes.length);
  const planSource = (narrative && (page === "import" || info.chapterCount > 1)) || (long && info.kind !== "path");
  const originalId = script.recoveryOrigin?.original_script_id ?? script.id;
  const originalDiffers = originalId !== script.id || version.sourceContent !== script.sourceDocument.content;
  const navigate = (step: string) => frame.navigate(step, script.id);
  const canLeave = frame.canNavigate && !frame.blocked && !busy && !writing;
  const revise = (content: string, summary: string) => {
    if (!editable) return;
    void save.run("recognize", () => api.scriptAction(script.projectId, "revise", { content, changeSummary: summary }, script.revision, script.id), value => {
      if (!completeScript(value) || value.id !== script.id || value.projectId !== script.projectId || currentVersion(value)?.number !== version.number + 1) throw new Error(t("wrong"));
      setPreview(undefined); frame.accept(value);
    });
  };
  return <section aria-label={t("heading")} className="my-4 space-y-3 rounded-xl border border-violet-300/20 bg-violet-300/5 p-4">
    <h3 className="font-semibold">{t("heading")} · {t(info.kind)}</h3>
    <p className="text-sm text-slate-300">{t("counts", { characters: info.characterCount.toLocaleString(), paragraphs: info.paragraphCount.toLocaleString() })}
      {info.chapterCount > 0 && <> · {t("chaptersFound", { count: info.chapterCount })}</>}
      {info.sceneCount > 0 && <> · {t("scenes", { count: info.sceneCount })}</>}</p>
    {info.chapterTitles.length > 0 && <p className="break-words text-xs leading-6 text-slate-400">{info.chapterTitles.join(" · ")}</p>}
    {info.kind === "path" && <p className="text-sm text-amber-200">{t("pathHint")}</p>}
    {(narrative || planSource) && <p className="text-sm leading-6 text-slate-300">{t(long ? "longHelp" : "simpleNext", { limit: aiCharacterLimit(script).toLocaleString() })}</p>}
    {planSource && originalDiffers && <p className="text-xs text-slate-400">{t("originalRanges")}</p>}
    <div className="flex flex-wrap gap-2">
      {planSource && <button type="button" className={long || info.chapterCount > 1 ? scriptPrimary : scriptButton} disabled={!canLeave} onClick={() => frame.navigate("episode-split-editor", originalId)}>{t("split")}</button>}
      {narrative && !long && !script.lockedVersionNumber && page !== "lab" && <button type="button" className={scriptButton} disabled={!canLeave} onClick={() => navigate("script-lab")}>{t("continueLab")}</button>}
      {narrative && !long && !script.lockedVersionNumber && page === "lab" && preview === undefined && <button type="button" className={scriptButton} disabled={!editable} onClick={() => setPreview(narrationDraft(version.sourceContent, t("readingHeading"), t("narrationPrefix")))}>{t("reading")}</button>}
      {needsReparse && !script.lockedVersionNumber && (page === "lab"
        ? <button type="button" className={scriptPrimary} disabled={!editable} onClick={() => revise(version.sourceContent, t("formatSummary"))}>{t("reparse")}</button>
        : <button type="button" className={scriptPrimary} disabled={!canLeave} onClick={() => navigate("script-lab")}>{t("continueLab")}</button>)}
      {info.kind === "screenplay" && !needsReparse && !script.lockedVersionNumber && <button type="button" className={scriptPrimary} disabled={!canLeave} onClick={() => navigate(version.validationErrors.length ? "source" : "versions")}>{t(version.validationErrors.length ? "fix" : "review")}</button>}
      {info.kind === "path" && <button type="button" className={scriptButton} disabled={!canLeave} onClick={() => navigate("script-import")}>{t("selectFile")}</button>}
    </div>
    {preview !== undefined && <div className="space-y-3"><p className="text-sm leading-6 text-slate-300">{t("readingHelp")}</p><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/25 p-3 font-sans text-sm">{preview}</pre><div className="flex flex-wrap gap-2"><button type="button" className={scriptPrimary} disabled={!editable} onClick={() => revise(preview, t("readingSummary"))}>{t("readingSave")}</button><button type="button" disabled={writing} className={scriptButton} onClick={() => setPreview(undefined)}>{t("cancel")}</button></div></div>}
    <MutationFeedback mutation={save} reload={frame.reload} />
  </section>;
}
