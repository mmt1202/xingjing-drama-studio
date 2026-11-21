import { useEffect, useRef, useState } from "react";
import { BookOpen, CheckCircle2, FilePlus2, FileText, FolderOpen, Upload } from "lucide-react";
import { CreatorPage } from "./shared";
import { useOptionalProjectFlow } from "./ProjectFlowShell";
import { ImportCatalog, ImportDocument } from "./ScriptImportDocuments";
import { useImportTranslation } from "./import-i18n";
import { useLibraryTranslation } from "./script-library-i18n";
import { useRecognitionTranslation } from "./recognition-i18n";
import { useScriptExperienceTranslation } from "./script-experience-i18n";
import { useScriptTranslation } from "./script-i18n";
import { isSourceFilePath, matchesScriptRecognition } from "./source-input";
import { aiCharacterLimit, completeScript, currentVersion, MutationFeedback, scriptButton, scriptField, scriptPrimary, useContentAiReadiness, useScriptGuard, useScriptMutation, type ScriptPageProps } from "./script-workflow";
import type { ScriptDocument } from "./types";
import "./script-import.css";

type ImportProps = ScriptPageProps & { onDocumentSelected?: (id: string, replace: boolean) => void; onReturnLibrary?: () => void };
type View = "new" | "current" | "browse";
const fileTypes = ".txt,.md,.markdown,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export function ScriptImportPage(props: ImportProps) {
  const { t } = useImportTranslation();
  return <CreatorPage api={props.api} permission="script.view" title={t("title")} className="xj-script-intake">{context =>
    <ImportWorkspace key={props.projectId} {...props} workspaceCanManage={context.permissions.includes("script.manage")} />
  }</CreatorPage>;
}

function ImportWorkspace({ api, projectId, selectedScriptId, onNavigate, onDocumentSelected, onReturnLibrary, workspaceCanManage }: ImportProps & { workspaceCanManage: boolean }) {
  const { t } = useImportTranslation(), { t: script } = useScriptTranslation(), { t: copy } = useRecognitionTranslation();
  const { t: experience } = useScriptExperienceTranslation();
  const { t: library } = useLibraryTranslation();
  const flow = useOptionalProjectFlow();
  const canManage = flow ? flow.data.permissions.includes("script.manage") : workspaceCanManage;
  const [choice, setChoice] = useState<{ route?: string; view: View; id?: string }>();
  const view = choice && choice.route === selectedScriptId ? choice.view : selectedScriptId ? "current" : "new";
  const id = (choice?.route === selectedScriptId && choice?.id) || selectedScriptId;
  const [method, setMethod] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File>(), [title, setTitle] = useState(""), [body, setBody] = useState("");
  const [autoAdapt, setAutoAdapt] = useState(false), [discard, setDiscard] = useState(false), [localError, setLocalError] = useState("");
  const [receipt, setReceipt] = useState<{ id: string; document?: ScriptDocument }>();
  const [catalogRevision, setCatalogRevision] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null), notified = useRef<string | undefined>(undefined), submitted = useRef(false);
  const importing = useScriptMutation(), adapting = useScriptMutation();
  const ai = useContentAiReadiness(api, projectId, view === "new" || Boolean(adapting.pending || adapting.error));
  const saving = Boolean(importing.pending || adapting.pending);
  const inFlight = saving || adapting.uncertain;
  const dirty = Boolean(file || body || title);
  const blocked = inFlight || importing.uncertain || view === "new" && dirty;
  useScriptGuard(blocked);
  const locked = !canManage || inFlight || importing.uncertain;
  const sourceFile = method === "file" ? file : undefined;
  const pasted = method === "paste" ? body.trim() : "";
  const path = Boolean(pasted && isSourceFilePath(pasted));
  const firstLine = pasted.split(/[\r\n]/, 1)[0].replace(/^#{1,6}\s+/, "");
  const suggested = sourceFile ? sourceFile.name.replace(/\.[^.]+$/, "").slice(0, 200) : Array.from(firstLine).slice(0, 32).join("");
  const navigate = onNavigate ? (step: string, docId: string, episode?: number) =>
    onNavigate(step + "?" + new URLSearchParams({ scriptId: docId, ...(episode ? { episodeId: String(episode) } : {}) }).toString()) : undefined;
  useEffect(() => {
    if (!receipt || notified.current === receipt.id || saving || adapting.error) return;
    notified.current = receipt.id;
    onDocumentSelected?.(receipt.id, true);
  }, [receipt, saving, adapting.error, onDocumentSelected]);

  function resetForm() {
    setFile(undefined); setBody(""); setTitle(""); setAutoAdapt(false); setLocalError(""); setDiscard(false);
    if (fileInput.current) fileInput.current.value = "";
  }
  function switchView(next: View, selected = id) {
    if (blocked) return;
    if (next === "new") { setReceipt(undefined); importing.clear(); adapting.clear(); submitted.current = false; }
    setChoice({ route: selectedScriptId, view: next, id: selected });
  }
  function chooseFile(files: FileList | null) {
    if (locked || !files?.length) return;
    importing.clear();
    const candidate = files[0];
    const error = files.length !== 1 ? t("oneFile") : !/\.(txt|md|markdown|docx)$/i.test(candidate.name) ? script("supported") :
      !candidate.size ? t("emptyFile") : candidate.size > 20 * 1024 * 1024 ? t("large") : "";
    setLocalError(error); setFile(error ? undefined : candidate);
    if (error && fileInput.current) fileInput.current.value = "";
  }
  function submit() {
    if (submitted.current || locked || path || (!sourceFile && !pasted)) return;
    submitted.current = true; setLocalError("");
    const name = title.trim() || suggested || t("fallbackTitle");
    const shouldAdapt = autoAdapt && ai.aiReady;
    void importing.run("import", () => sourceFile ? api.importScriptFile(projectId, name, sourceFile) :
      api.scriptAction(projectId, "import", { title: name, filename: "manual-script.txt", mediaType: "text/plain", content: body }), value => {
      if (!("id" in value) || typeof value.id !== "string" || !value.id || (completeScript(value) && value.projectId !== projectId)) throw new Error(script("invalidResponse"));
      setReceipt({ id: value.id, document: completeScript(value) ? value : undefined });
      setChoice({ route: selectedScriptId, view: "current", id: value.id });
      setCatalogRevision(n => n + 1); resetForm();
      if (!completeScript(value)) return;
      const info = value.recognition;
      if (shouldAdapt && matchesScriptRecognition(info, value) &&
        (info.kind === "prose" || info.kind === "chapters") && info.characterCount <= aiCharacterLimit(value) && !value.lockedVersionNumber) {
        void adapting.run("analyze", () => api.scriptAction(projectId, "analyze", {}, value.revision, value.id), next => {
          if (!completeScript(next) || next.projectId !== projectId || next.id !== value.id || currentVersion(next)?.number !== info.version + 1) throw new Error(script("invalidResponse"));
          setReceipt({ id: next.id, document: next });
        });
      }
    }).finally(() => { submitted.current = false; });
  }
  const chooseDocument = (docId: string) => {
    if (blocked) return;
    setChoice({ route: selectedScriptId, view: "current", id: docId });
    onDocumentSelected?.(docId, false);
  };
  return <div className="xj-intake-body">
    {onReturnLibrary ? <div><button type="button" className={scriptButton} disabled={blocked} onClick={onReturnLibrary}><FolderOpen size={16}/>{library("library")}</button></div> : <div className="xj-intake-switch" role="group" aria-label={t("title")}>
      <button type="button" aria-pressed={view === "new"} disabled={blocked && view !== "new"} onClick={() => switchView("new")}><FilePlus2 size={16} />{t("create")}</button>
      <button type="button" aria-pressed={view !== "new"} disabled={blocked} onClick={() => switchView(id ? "current" : "browse")}><FolderOpen size={16} />{t("existing")}</button>
    </div>}
    {view === "current" && id ? <>
      {receipt?.id === id && <p role="status" className="flex items-center gap-2 text-sm text-emerald-300"><CheckCircle2 size={16} />{script("imported")}</p>}
      {adapting.pending && <p role="status" className="text-sm text-violet-200">{copy("processing")}</p>}
      {adapting.error && receipt?.id === id && <MutationFeedback mutation={adapting} reload={() => {
        setReceipt({ id });
        setCatalogRevision(n => n + 1);
        ai.recheckReadiness();
        if (!adapting.uncertain) adapting.clear();
      }} allowRetry={ai.aiReady || adapting.uncertain} />}
      <ImportDocument key={id + ":" + catalogRevision} api={api} projectId={projectId} id={id} supplied={receipt?.id === id ? receipt.document : undefined}
        episode={flow?.data.episodes.find(e => e.scriptId === id)?.episode} blocked={inFlight}
        onNew={() => { setMethod("file"); switchView("new"); }} onChoose={() => switchView("browse")} navigate={navigate} />
    </> : view === "browse" ? <div className="xj-intake-card"><p className="mb-4 text-sm text-slate-300">{t("existingHint")}</p>
      <ImportCatalog api={api} projectId={projectId} revision={catalogRevision} onSelect={chooseDocument} /></div> :
    <form className="xj-intake-card space-y-4" onSubmit={e => { e.preventDefault(); submit(); }}>
      <p className="text-sm text-slate-300">{t("intro")}</p>
      <div className="xj-intake-methods" role="group" aria-label={t("create")}>{(["file", "paste"] as const).map(value => <button key={value} type="button" aria-pressed={method === value} disabled={locked} onClick={() => { setMethod(value); setLocalError(""); importing.clear(); }}>{value === "file" ? <Upload size={16} /> : <FileText size={16} />}{t(value)}</button>)}</div>
      {method === "file" ? <label className="xj-intake-upload" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); chooseFile(e.dataTransfer.files); }}>
        <input className="sr-only" aria-label={script("file")} ref={fileInput} type="file" accept={fileTypes} disabled={locked} onChange={e => chooseFile(e.target.files)} />
        <Upload aria-hidden="true" size={22} /><strong>{file?.name ?? t("pick")}</strong><span>{file ? t("replace") + " · " + file.size.toLocaleString() + " B" : t("formats")}</span>
      </label> : <label className="block text-sm">{t("body")}<textarea className={scriptField + " min-h-32"} disabled={locked} placeholder={t("bodyHint")} value={body} onChange={e => { setBody(e.target.value); setLocalError(""); importing.clear(); }} /></label>}
      {path && <p role="alert" className="text-sm text-amber-200">{copy("pathHint")} <button type="button" className="underline" disabled={locked} onClick={() => { setMethod("file"); setLocalError(""); }}>{t("file")}</button></p>}
      {localError && <p role="alert" className="text-sm text-amber-200">{localError}</p>}
      <details className="xj-intake-options"><summary>{t("options")}</summary>
        <label className="mt-3 block text-sm">{t("titleLabel")}<input className={scriptField} disabled={locked} maxLength={200} value={title} placeholder={suggested || t("defaultTitle")} onChange={e => { setTitle(e.target.value); importing.clear(); }} /></label>
        <label className="mt-4 flex items-center gap-2 text-sm"><input type="checkbox" className="accent-violet-500" checked={autoAdapt && ai.aiReady} disabled={locked || !ai.aiReady} onChange={e => { setAutoAdapt(e.target.checked); importing.clear(); }} />{copy("automatic")}</label>
        <p className="mt-2 text-xs leading-6 text-slate-400">{ai.readinessChecking || (!ai.aiReadiness && !ai.readinessError) ? t("checking") : ai.aiReady ? copy("automaticHelp") + " " + t("aiHelp") : ai.readinessError || ai.aiReadiness?.status === "temporarily_unavailable" ? experience("temporarilyUnavailable") : copy("autoUnavailable")}</p>
        {!ai.aiReady && !ai.readinessChecking && (ai.aiReadiness || ai.readinessError) ? <button type="button" className={scriptButton} onClick={ai.recheckReadiness}>{t("aiRetry")}</button> : null}
      </details>
      {!canManage && <p role="status" className="text-sm text-amber-200">{script("readonly")}</p>}
      <div className="flex flex-wrap items-center gap-3"><button type="submit" className={scriptPrimary} disabled={locked || (!sourceFile && !pasted) || path}><BookOpen size={16} />{script(importing.pending ? "importing" : "importAction")}</button><span className="text-xs text-slate-400">{t("newHint")}</span></div>
      {importing.error && <MutationFeedback mutation={importing} reload={() => setCatalogRevision(n => n + 1)} />}
      {dirty && <div>{discard ? <><p className="text-xs text-amber-200">{script(importing.uncertain ? "uncertainDiscard" : "discardConfirm")}</p><button type="button" className={scriptButton} disabled={inFlight} onClick={() => { resetForm(); importing.clear(); }}>{script("discardYes")}</button><button type="button" className={scriptButton} onClick={() => setDiscard(false)}>{script("keep")}</button></> : <button type="button" disabled={inFlight} className="text-xs text-slate-400 underline" onClick={() => setDiscard(true)}>{script("cancel")}</button>}</div>}
      <ImportCatalog compact api={api} projectId={projectId} revision={catalogRevision} onSelect={chooseDocument} />
    </form>}
  </div>;
}
