import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, CheckCircle2, RefreshCw } from "lucide-react";
import { AuthSessionError } from "@/utils/auth";
import { CreatorApiError } from "./api";
import { CreatorPage } from "./shared";
import { useProjectNavigationBlocker } from "./ProjectFlowShell";
import { useScriptTranslation } from "./script-i18n";
import type { ContentAiReadiness, CreatorProjectsPort, ScriptDocument, ScriptVersion, SessionContext } from "./types";
import { useScriptExperienceTranslation } from "./script-experience-i18n";

export interface ScriptPageProps { api: CreatorProjectsPort; projectId: string; selectedScriptId?: string; selectedDirectorCandidateId?: string; onNavigate?: (target: string) => void }
export const scriptSteps = [
  ["script-import", "import"], ["source", "source"], ["script-checkup", "checkup"], ["script-lab", "lab"], ["rewrite", "rewrite"], ["versions", "versions"], ["ai-director", "director"], ["director-assets", "assets"], ["source-mapping", "mapping"],
] as const;
export type ScriptStep = typeof scriptSteps[number][0];
export const scriptPanel = "rounded-[14px] border border-white/10 bg-[var(--xj-surface,#11141d)] p-5";
export const scriptButton = "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-white/15 px-4 py-2 text-sm text-slate-200 hover:border-violet-300/50 focus-visible:outline-2 focus-visible:outline-violet-300 disabled:cursor-not-allowed disabled:opacity-40";
export const scriptPrimary = scriptButton + " xj-shell-button--primary text-white";
export const scriptField = "mt-2 w-full min-w-0 rounded-xl border border-white/15 bg-[var(--xj-bg-elevated,#0c1018)] px-3 py-2.5 text-sm text-slate-100 outline-none focus-visible:border-violet-300 disabled:opacity-50";
export const AI_CHARACTER_LIMIT = 20_000;
export const aiCharacterLimit = (script: ScriptDocument) => Number.isSafeInteger(script.aiInputLimitCharacters) && (script.aiInputLimitCharacters ?? 0) > 0 ? script.aiInputLimitCharacters! : AI_CHARACTER_LIMIT;
export const characterCount = (text: string) => Array.from(text).length;
export const currentVersion = (script: ScriptDocument): ScriptVersion | undefined => script.versions.at(-1);
export const contentOf = (script: ScriptDocument) => currentVersion(script)?.sourceContent ?? script.sourceDocument.content;
export function completeScript(value: unknown): value is ScriptDocument {
  if (!value || typeof value !== "object") return false;
  const script = value as Partial<ScriptDocument>;
  return typeof script.id === "string" && typeof script.projectId === "string" && typeof script.title === "string" && Number.isSafeInteger(script.revision)
    && Array.isArray(script.versions) && script.versions.every((version) => typeof version.number === "number" && typeof version.sourceContent === "string" && Array.isArray(version.validationErrors))
    && typeof script.sourceDocument?.content === "string" && Boolean(script.directorProfile);
}
export function scriptTarget(step: string, scriptId?: string) { return step + (scriptId ? "?" + new URLSearchParams({ scriptId }).toString() : ""); }
export function useScriptGuard(blocked: boolean) {
  useProjectNavigationBlocker(blocked);
  useEffect(() => {
    if (!blocked) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [blocked]);
}
export function useScriptMutation() {
  const [pending, setPending] = useState<string>();
  const [error, setError] = useState<{ scope: string; reason: unknown }>();
  const [saved, setSaved] = useState<string>();
  const latch = useRef(false), mounted = useRef(false);
  const original = useRef<(() => Promise<void>) | undefined>(undefined);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const run = async <T,>(scope: string, operation: () => Promise<T>, accept: (value: T) => void) => {
    if (latch.current) return;
    latch.current = true; setPending(scope); setError(undefined); setSaved(undefined);
    original.current = () => run(scope, operation, accept);
    try { const result = await operation(); if (mounted.current) { accept(result); setSaved(scope); original.current = undefined; } }
    catch (reason) { if (mounted.current) setError({ scope, reason }); }
    finally { latch.current = false; if (mounted.current) setPending(undefined); }
  };
  const terminalFailure = error?.reason instanceof CreatorApiError && ["CONTENT_AI_MODEL_FAILED", "CONTENT_AI_CONFIGURATION_UNAVAILABLE"].includes(error.reason.code);
  const uncertain = Boolean(error && !terminalFailure && (!(error.reason instanceof CreatorApiError) || error.reason.status >= 500 || error.reason.code === "CONTENT_AI_REQUEST_IN_PROGRESS"));
  return { pending, error, saved, uncertain, run, retry: () => original.current?.(), clear: () => { if (!latch.current) { setError(undefined); setSaved(undefined); original.current = undefined; } } };
}
export function ScriptError({ error, retry, reload }: { error: unknown; retry?: () => void; reload?: () => void }) {
  const { t } = useScriptTranslation();
  const { t: experience } = useScriptExperienceTranslation();
  const problem = error instanceof CreatorApiError ? error : undefined;
  const code = problem?.code;
  const key = code === "VERSION_CONFLICT" ? "conflict" : code === "SCRIPT_FROZEN" ? "frozenError" : code === "SCRIPT_NOT_FROZEN" ? "notFrozenError" : code === "CONTENT_AI_REQUEST_IN_PROGRESS" ? "inProgress"
    : code === "SCRIPT_VALIDATION_FAILED" ? "validationError" : code === "CONTENT_AI_INPUT_TOO_LARGE" ? problem?.details?.inputScope === "instruction" ? "instructionTooLarge" : "tooLarge" : problem?.status === 403 ? "forbidden" : problem?.status === 503 ? "unavailable" : "failed";
  const message = code === "CONTENT_AI_MODEL_FAILED" ? experience("modelFailed") : code === "CONTENT_AI_CONFIGURATION_UNAVAILABLE" ? experience("configurationFailed") : t(key, { limit: problem?.details?.maxCharacters ?? 2000 });
  return <div role="alert" className="rounded-xl border border-amber-400/30 bg-amber-400/[.04] p-4 text-sm leading-6 text-amber-100"><p>{error instanceof AuthSessionError ? error.message : message}</p>{error instanceof Error && !(error instanceof AuthSessionError) && error.message && error.message !== code && error.message !== message ? <p className="mt-2 whitespace-pre-wrap text-xs text-slate-400">{error.message}</p> : null}{code ? <p className="mt-2 break-all font-mono text-xs text-slate-500">{t("diagnostic", { code })}</p> : null}{problem?.requestId ? <p className="mt-1 font-mono text-xs text-slate-500">{t("requestId", { id: problem.requestId })}</p> : null}<div className="mt-3 flex flex-wrap gap-2">{retry ? <button type="button" className={scriptButton} onClick={retry}>{t("retry")}</button> : null}{reload ? <button type="button" className={scriptButton} onClick={reload}>{t(code === "VERSION_CONFLICT" ? "conflictRead" : "refresh")}</button> : null}</div></div>;
}
export function TextPreview({ text, label }: { text: string; label?: string }) {
  const { t } = useScriptTranslation();
  const [expanded, setExpanded] = useState(false);
  const characters = Array.from(text), clipped = characters.length > 1600;
  return <div><p className="mb-2 text-xs text-slate-500">{label} · {t("textLength", { count: characters.length })}</p><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-white/10 bg-black/10 p-4 font-sans text-sm leading-7 text-slate-300">{expanded ? text : characters.slice(0, 1600).join("")}</pre>{clipped ? <><p className="mt-2 text-xs text-slate-500">{t("preview", { count: 1600 })}</p><button type="button" className={scriptButton + " mt-2"} onClick={() => setExpanded(!expanded)}>{t(expanded ? "collapse" : "expand")}</button></> : null}</div>;
}
export function ValidationSummary({ script }: { script: ScriptDocument }) {
  const { t } = useScriptTranslation();
  const { t: experience } = useScriptExperienceTranslation();
  const version = currentVersion(script);
  if (!version) return <p className="text-sm text-amber-200">{t("detailPending")}</p>;
  return <section className="mt-4 rounded-xl border border-white/10 p-4"><h3 className="text-sm font-medium">{t("validation")} · {t("version", { number: version.number })}</h3><p className={"mt-2 text-sm " + (version.validationErrors.length ? "text-amber-200" : "text-emerald-300")}>{t(version.validationErrors.length ? "validationIssues" : "validationPass", { count: version.validationErrors.length })}</p>{version.validationErrors.length ? <ul className="mt-3 space-y-2 text-xs text-slate-400">{version.validationErrors.map((issue, index) => <li key={index}>{issue.code === "SCENE_HEADING_REQUIRED" ? experience("missingScene") : issue.code === "SCRIPT_PREFIX_CONTENT_UNPARSED" ? experience("unstructuredText") : issue.code === "PARAGRAPH_TEXT_REQUIRED" ? experience("emptyParagraph") : issue.message || issue.code}<details className="mt-1"><summary className="cursor-pointer text-slate-500">{experience("technicalDetails")}</summary><span className="font-mono">{issue.code}{issue.field ? " · " + issue.field : ""}</span></details></li>)}</ul> : null}</section>;
}
export function AiLimitNotice({ script }: { script: ScriptDocument }) {
  const { t } = useScriptTranslation();
  const count = characterCount(contentOf(script));
  return <div className="mt-4 text-xs leading-6 text-slate-400"><p>{t("textLength", { count })}</p>{count > aiCharacterLimit(script) ? <p role="status" className="mt-2 rounded-xl border border-amber-300/20 p-3 text-amber-200">{t("aiLimit", { limit: aiCharacterLimit(script) })}</p> : null}</div>;
}
export interface ScriptFrameContext {
  script?: ScriptDocument; scripts: ScriptDocument[]; context: SessionContext; canManage: boolean; reading: boolean; canNavigate: boolean; blocked: boolean;
  navigate: (step: string, scriptId?: string) => void; reload: () => void; accept: (document: ScriptDocument) => void;
  aiReadiness?: ContentAiReadiness; aiReady: boolean; readinessChecking: boolean; readinessError?: unknown; recheckReadiness: () => void;
}
export function ScriptPageFrame({ api, projectId, selectedScriptId, onNavigate, page, blocked, children }: ScriptPageProps & { page: ScriptStep; blocked: boolean; children: (value: ScriptFrameContext) => ReactNode }) {
  const { t } = useScriptTranslation();
  const [result, setResult] = useState<{ api: CreatorProjectsPort; projectId: string; items: ScriptDocument[]; total: number }>();
  const [explicitDetail, setDetail] = useState<{ api: CreatorProjectsPort; document: ScriptDocument }>();
  const [selection, setSelection] = useState<{ prop: string | undefined; id: string | undefined }>({ prop: selectedScriptId, id: selectedScriptId });
  const [error, setError] = useState<unknown>();
  const [reading, setReading] = useState(true);
  const [query, setQuery] = useState(""), [locked, setLocked] = useState("all");
  const [reload, setReload] = useState(0);
  const [readiness, setReadiness] = useState<{ api: CreatorProjectsPort; projectId: string; value: ContentAiReadiness }>();
  const [readinessError, setReadinessError] = useState<unknown>();
  const [readinessChecking, setReadinessChecking] = useState(false), [readinessRevision, setReadinessRevision] = useState(0);
  const needsAi = page === "script-lab" || page === "rewrite" || page === "ai-director";
  useEffect(() => {
    if (!needsAi) return;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      setReadinessChecking(true); setReadinessError(undefined);
      if (!api.getContentAiReadiness) { setReadinessError(new Error("CONTENT_AI_READINESS_UNAVAILABLE")); setReadinessChecking(false); return; }
      void api.getContentAiReadiness(projectId, abort.signal).then((value) => {
        if (abort.signal.aborted) return;
        if (!value || !["ready", "configuration_unavailable", "temporarily_unavailable"].includes(value.status) || typeof value.ready !== "boolean" || value.ready !== (value.status === "ready") || value.checksProviderConnection !== false) throw new Error("INVALID_CONTENT_AI_READINESS");
        setReadiness({ api, projectId, value });
      }).catch((error: unknown) => { if (!abort.signal.aborted) setReadinessError(error); }).finally(() => { if (!abort.signal.aborted) setReadinessChecking(false); });
    }, 0);
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [api, projectId, needsAi, readinessRevision]);
  const aiReadiness = readiness?.api === api && readiness.projectId === projectId ? readiness.value : undefined;
  const sequence = useRef(0);
  const itemsRef = useRef<ScriptDocument[]>([]);
  const items = result?.api === api && result.projectId === projectId ? result.items : undefined;
  useEffect(() => { itemsRef.current = items ?? []; }, [items]);
  const preferred = selection.prop === selectedScriptId ? selection.id : selectedScriptId;
  const selected = preferred !== undefined ? preferred : items?.length === 1 ? items[0].id : undefined;
  const detail = explicitDetail?.api === api ? explicitDetail.document : undefined;
  const script = items?.find((item) => item.id === selected) ?? (detail?.projectId === projectId && detail.id === selected ? detail : undefined);
  useScriptGuard(blocked);
  const read = useCallback(async (append = false) => {
    const request = ++sequence.current; setReading(true); setError(undefined);
    try {
      const page = await api.listScripts(projectId, { offset: append ? itemsRef.current.length : 0, limit: 50, query: query.trim() || undefined, locked: locked === "all" ? undefined : locked === "locked" });
      if (request !== sequence.current) return;
      if (page.items.some((item) => !completeScript(item) || item.projectId !== projectId)) throw new Error(t("invalidResponse"));
      setResult((previous) => ({ api, projectId, items: append && previous?.api === api && previous.projectId === projectId ? [...new Map([...previous.items, ...page.items].map((item) => [item.id, item])).values()] : page.items, total: page.total ?? page.items.length }));
    } catch (problem) { if (request === sequence.current) setError(problem); }
    finally { if (request === sequence.current) setReading(false); }
  }, [api, projectId, query, locked, t]);
  useEffect(() => { const timer = window.setTimeout(() => { void read(); }, 0); return () => { window.clearTimeout(timer); sequence.current += 1; }; }, [read, reload]);
  useEffect(() => {
    if (!items || !selected || items.some((item) => item.id === selected) || !api.getScript) return;
    const abort = new AbortController();
    void api.getScript(projectId, selected, abort.signal).then((document) => { if (!abort.signal.aborted) { if (!completeScript(document) || document.projectId !== projectId || document.id !== selected) throw new Error(t("invalidResponse")); setDetail({ api, document }); } }).catch((problem: unknown) => { if (!abort.signal.aborted) setError(problem); });
    return () => abort.abort();
  }, [api, projectId, selected, items, reload, t]);
  const navigate = (step: string, id = script?.id) => { if (!blocked) onNavigate?.(scriptTarget(step, id)); };
  const accept = (document: ScriptDocument) => {
    if (!completeScript(document) || document.projectId !== projectId) { setReload((value) => value + 1); return; }
    sequence.current += 1; setReading(false); setError(undefined); setDetail({ api, document }); setSelection({ prop: selectedScriptId, id: document.id });
    setResult((previous) => ({ api, projectId, items: previous?.api === api && previous.projectId === projectId ? [document, ...previous.items.filter((item) => item.id !== document.id)] : [document], total: previous?.api === api && previous.projectId === projectId ? previous.total + (previous.items.some((item) => item.id === document.id) ? 0 : 1) : 1 }));
  };
  return <CreatorPage api={api} permission="script.view" title={t(scriptSteps.find(([step]) => step === page)![1])}>{(context) => <div className="space-y-5">
    <nav aria-label={t("workflow")} className="flex flex-wrap gap-2">{scriptSteps.map(([step, key]) => <button type="button" key={step} className={scriptButton + (step === page ? " border-violet-300/50 bg-violet-400/10 text-violet-100" : "")} aria-current={step === page ? "step" : undefined} disabled={blocked || !onNavigate} onClick={() => navigate(step)}>{t(key)}</button>)}</nav>
    {blocked ? <p role="status" className="text-xs leading-6 text-amber-200">{t("unsaved")}</p> : null}
    <div className="flex flex-wrap items-end gap-3"><label className="min-w-56 flex-1 text-xs text-slate-400">{t("choose")}<select className={scriptField} value={selected ?? ""} disabled={blocked || reading} onChange={(event) => { const id = event.target.value || undefined; setSelection({ prop: selectedScriptId, id }); setDetail(undefined); if (id) navigate(page, id); }}><option value="">{t("choosePlaceholder")}</option>{selected && !items?.some((item) => item.id === selected) ? <option value={selected}>{script?.title ?? selected}</option> : null}{items?.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.id} · v{currentVersion(item)?.number ?? "—"}</option>)}</select></label><button type="button" className={scriptButton} disabled={blocked || reading} onClick={() => setReload((value) => value + 1)}><RefreshCw size={15} aria-hidden="true" />{t("refresh")}</button></div>
    {script ? <section className={scriptPanel}><p className="text-xs text-slate-500">{t("current")}</p><h2 className="mt-2 break-words text-lg font-semibold">{script.title}</h2><p className="mt-2 break-all font-mono text-[11px] text-slate-500">{t("scriptId")}：{script.id}</p><div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-400"><span>{t("version", { number: currentVersion(script)?.number ?? "—" })}</span><span>{t("revision", { number: script.revision })}</span><span className={script.lockedVersionNumber ? "text-violet-200" : "text-amber-200"}>{script.lockedVersionNumber ? t("frozenVersion", { number: script.lockedVersionNumber }) : t("notFrozen")}</span></div></section> : null}
    {!context.permissions.includes("script.manage") ? <p role="status" className="text-sm text-slate-400">{t("readonly")}</p> : null}
    {error ? <ScriptError error={error} reload={() => { void read(); }} /> : null}
    {!items && !error ? <p role="status" className="py-8 text-slate-400">{t("loading")}</p> : null}
    {page === "script-import" && items?.length === 0 ? <p role="status" className="text-sm text-slate-500">{t("noScripts")}</p> : null}
    {page !== "script-import" && !script && items ? <section className={scriptPanel}><p role="status" className="text-sm text-amber-100">{t(preferred !== undefined ? "unavailableSelection" : !items.length ? "noScripts" : "chooseHint")}</p><button type="button" className={scriptButton + " mt-4"} disabled={blocked || !onNavigate} onClick={() => navigate("script-import")}>{t("openImport")}</button></section> : null}
    {page === "script-import" || script ? children({ script, scripts: items ?? [], context, canManage: context.permissions.includes("script.manage") && !error && !reading, reading, canNavigate: Boolean(onNavigate), blocked, navigate, accept, reload: () => { void read(); }, aiReadiness, aiReady: Boolean(aiReadiness?.ready && !readinessError && !readinessChecking), readinessChecking, readinessError, recheckReadiness: () => setReadinessRevision((value) => value + 1) }) : null}
    <details className="text-xs text-slate-500"><summary className="cursor-pointer">{t("search")}</summary><div className="mt-3 flex flex-wrap gap-3"><label className="flex-1">{t("search")}<input className={scriptField} value={query} placeholder={t("searchHint")} disabled={blocked} onChange={(event) => setQuery(event.target.value)} /></label><label>{t("lockFilter")}<select className={scriptField} value={locked} disabled={blocked} onChange={(event) => setLocked(event.target.value)}><option value="all">{t("all")}</option><option value="editable">{t("editable")}</option><option value="locked">{t("frozen")}</option></select></label></div></details>
    {items && items.length < (result?.total ?? 0) ? <button type="button" className={scriptButton} disabled={blocked || reading} onClick={() => void read(true)}>{t(reading ? "loading" : "loadMore")}</button> : null}
  </div>}</CreatorPage>;
}
export function NextStep({ frame, step, label, scriptId }: { frame: ScriptFrameContext; step: string; label: string; scriptId?: string }) {
  const { t } = useScriptTranslation();
  return <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-white/10 pt-4"><span className="text-xs text-slate-500">{t("next")}</span><button type="button" className={scriptButton} disabled={!frame.canNavigate || frame.blocked} onClick={() => frame.navigate(step, scriptId)}>{t(label)}<ArrowRight size={15} aria-hidden="true" /></button></div>;
}
export function SavedNotice() { const { t } = useScriptTranslation(); return <p role="status" className="mt-4 flex items-center gap-2 text-sm text-emerald-300"><CheckCircle2 size={15} aria-hidden="true" />{t("updated")}</p>; }
