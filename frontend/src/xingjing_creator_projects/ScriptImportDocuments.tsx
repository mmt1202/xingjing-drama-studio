import { useEffect, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, FileText, Search } from "lucide-react";
import { useImportTranslation } from "./import-i18n";
import { useRecognitionTranslation } from "./recognition-i18n";
import { useScriptTranslation } from "./script-i18n";
import { matchesScriptRecognition } from "./source-input";
import { aiCharacterLimit, characterCount, completeScript, contentOf, currentVersion, ScriptError, scriptButton, scriptField, scriptPrimary } from "./script-workflow";
import type { CreatorProjectsPort, ScriptCatalogItem, ScriptDocument, ScriptRecognition } from "./types";

export function ImportCatalog({ api, projectId, compact, revision, onSelect }: {
  api: CreatorProjectsPort; projectId: string; compact?: boolean; revision: number; onSelect: (id: string) => void;
}) {
  const { t } = useImportTranslation(), { t: script } = useScriptTranslation();
  const [query, setQuery] = useState(""), [page, setPage] = useState(0), [retry, setRetry] = useState(0);
  const limit = compact ? 1 : 8;
  const key = JSON.stringify([projectId, query.trim(), page, limit, retry, revision]);
  const [response, setResponse] = useState<{ key: string; items?: ScriptCatalogItem[]; total?: number; error?: unknown }>();
  useEffect(() => {
    const abort = new AbortController();
    const timer = window.setTimeout(async () => {
      const options = { query: query.trim(), offset: page * limit, limit };
      try {
        const result = api.listScriptCatalog ? await api.listScriptCatalog(projectId, options, abort.signal) :
          await api.listScripts(projectId, options).then(({ items, total }) => ({
            items: items.map((d) => ({ id: d.id, title: d.title, filename: d.sourceDocument.filename, lockedVersionNumber: d.lockedVersionNumber })), total: total ?? items.length,
          }));
        if (abort.signal.aborted) return;
        if (page > 0 && options.offset >= result.total) setPage(Math.max(0, Math.ceil(result.total / limit) - 1));
        else setResponse({ key, ...result });
      } catch (error) { if (!abort.signal.aborted) setResponse({ key, error }); }
    }, query ? 220 : 0);
    return () => { window.clearTimeout(timer); abort.abort(); };
  }, [api, projectId, key, query, page, limit]);
  const data = response?.key === key ? response : undefined;
  if (compact) return data?.total !== undefined ? <p className="text-xs text-slate-400">{data.total ? t("total", { count: data.total }) : script("noScripts")}</p> : null;
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / limit));
  return <section className="space-y-4">
    <label className="relative block"><Search size={17} aria-hidden="true" className="pointer-events-none absolute top-3 left-3 text-slate-400" />
      <span className="sr-only">{t("search")}</span><input type="search" className={scriptField + " mt-0 pl-10"} value={query} placeholder={t("search")} onChange={e => { setQuery(e.target.value); setPage(0); }} />
    </label>
    {data?.error ? <ScriptError error={data.error} retry={() => setRetry(n => n + 1)} /> : !data ? <p role="status">{script("loading")}</p> :
      data.items?.length ? <ul className="divide-y divide-white/10">{data.items.map(item => <li key={item.id}>
        <button type="button" className="xj-intake-document" onClick={() => onSelect(item.id)}><FileText size={19} aria-hidden="true" />
          <span className="min-w-0 flex-1"><strong className="block break-words">{item.title}</strong><small className="block truncate text-slate-400">{item.filename}{item.lockedVersionNumber ? " · " + script("frozenVersion", { number: item.lockedVersionNumber }) : ""}</small></span><ChevronRight size={16} aria-hidden="true" /></button>
      </li>)}</ul> : <p role="status" className="text-sm text-slate-400">{query ? t("noMatches") : script("noScripts")}</p>}
    {data?.total && pages > 1 ? <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
      <span>{t("page", { page: page + 1, pages, count: data.total })}</span><div className="flex gap-2">
        <button className={scriptButton} disabled={!page} aria-label={t("previous")} onClick={() => setPage(n => n - 1)}><ChevronLeft size={16} /></button>
        <button className={scriptButton} disabled={page + 1 >= pages} aria-label={t("next")} onClick={() => setPage(n => n + 1)}><ChevronRight size={16} /></button>
      </div></div> : null}
  </section>;
}

function nextStep(document: ScriptDocument, info?: ScriptRecognition, episode?: number) {
  const version = currentVersion(document);
  if (document.lockedVersionNumber) return { target: "versions", label: "frozen", help: "frozenHelp" } as const;
  if (info?.kind === "path") return { target: "new", label: "change", help: null } as const;
  if (info?.kind === "screenplay") {
    if (!version || info.requiresReparse || info.sceneCount !== version.scenes?.length) return { target: "script-lab", label: "structure", help: "screenplayHelp" } as const;
    if (version.validationErrors.length) return { target: "source", label: "revise", help: "screenplayHelp" } as const;
    return { target: "versions", label: "freeze", help: "freezeHelp" } as const;
  }
  if (info?.kind === "chapters" || info?.kind === "prose") {
    if (episode && info.characterCount > aiCharacterLimit(document)) return { target: "source", label: "trim", help: "trimHelp" } as const;
    if (!episode && (info.chapterCount > 1 || info.characterCount > aiCharacterLimit(document))) return { target: "episode-split-editor", label: "split", help: "splitHelp" } as const;
    return { target: "script-lab", label: episode ? "continueEpisode" : "adapt", help: episode ? "episodeHelp" : "proseHelp" } as const;
  }
  return { target: "source", label: "openText", help: null } as const;
}

export function ImportDocument({ api, projectId, id, supplied, episode, blocked, onNew, onChoose, navigate }: {
  api: CreatorProjectsPort; projectId: string; id: string; supplied?: ScriptDocument; episode?: number; blocked: boolean;
  onNew: () => void; onChoose: () => void; navigate?: (page: string, id: string, episode?: number) => void;
}) {
  const { t } = useImportTranslation(), { t: script } = useScriptTranslation(), { t: recognition } = useRecognitionTranslation();
  const [read, setRead] = useState<ScriptDocument>(), [error, setError] = useState<unknown>(), [retry, setRetry] = useState(0);
  useEffect(() => {
    if (supplied) return;
    const abort = new AbortController();
    const get = api.getScript ? api.getScript(projectId, id, abort.signal) : api.listScripts(projectId, { query: id }).then(r => r.items.find(item => item.id === id));
    void get.then(document => {
      if (abort.signal.aborted) return;
      if (!completeScript(document) || document.id !== id || document.projectId !== projectId) throw new Error(t("noSelection"));
      setRead(document); setError(undefined);
    }).catch((cause: unknown) => { if (!abort.signal.aborted) setError(cause); });
    return () => abort.abort();
  }, [api, projectId, id, supplied, retry, t]);
  const document = supplied ?? read;
  if (!document) return <div className="space-y-4">
    {error ? <ScriptError error={error} retry={() => { setError(undefined); setRetry(n => n + 1); }} /> : <p role="status">{script("loading")}</p>}
    <button type="button" className={scriptButton} disabled={blocked} onClick={onChoose}>{t("switch")}</button>
  </div>;
  const info = matchesScriptRecognition(document.recognition, document) ? document.recognition : undefined;
  const next = nextStep(document, info, episode), version = currentVersion(document);
  const targetId = next.target === "episode-split-editor" ? document.recoveryOrigin?.original_script_id ?? id : id;
  return <article className="xj-intake-current">
    <p className="text-xs font-medium text-violet-300">{t("saved")}</p>
    <h2 className="mt-2 break-words text-2xl font-semibold">{document.title}</h2>
    <p className="mt-2 text-sm text-slate-400">{[info && recognition(info.kind), t("chars", { count: info?.characterCount ?? characterCount(contentOf(document)) }), script("version", { number: version?.number ?? 1 })].filter(Boolean).join(" · ")}</p>
    {info && (info.sceneCount > 0 || info.chapterCount > 0) ? <p className="mt-1 text-sm text-slate-400">{[info.chapterCount > 0 && recognition("chaptersFound", { count: info.chapterCount }), info.sceneCount > 0 && recognition("scenes", { count: info.sceneCount })].filter(Boolean).join(" · ")}</p> : null}
    {(next.help || info?.kind === "path") && <p className="mt-4 text-sm leading-6 text-slate-300">{next.help ? t(next.help, { episode }) : recognition("pathHint")}</p>}
    <div className="mt-5 flex flex-wrap gap-2"><button type="button" className={scriptPrimary} disabled={blocked || (next.target !== "new" && !navigate)} onClick={() => next.target === "new" ? onNew() : navigate?.(next.target, targetId, next.target === "episode-split-editor" ? undefined : episode)}>{t(next.label)}<ArrowRight size={16} aria-hidden="true" /></button>
      {next.target !== "source" && <button type="button" className={scriptButton} disabled={blocked || !navigate} onClick={() => navigate?.("source", id, episode)}>{t("openText")}</button>}
      <button type="button" className={scriptButton} disabled={blocked} onClick={onChoose}>{t("switch")}</button></div>
    <details className="mt-5 text-xs text-slate-400"><summary className="cursor-pointer">{t("metadata")}</summary><p className="mt-2 break-all">{document.sourceDocument.filename === "manual-script.txt" ? t("pasted") : document.sourceDocument.filename} · {script("scriptId")} {id}</p></details>
  </article>;
}
