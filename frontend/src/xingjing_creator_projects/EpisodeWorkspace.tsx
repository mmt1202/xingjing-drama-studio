import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, GripVertical, Plus, RefreshCw } from "lucide-react";
import { CreatorApiError } from "./api";
import { CreatorPage } from "./shared";
import type { CreatorProjectsPort } from "./types";
import { episodeDraft, plannedEpisode, type Episode, type EpisodeCosts, type EpisodeDraft, type EpisodesApi, type EpisodesSnapshot } from "./episodes-api";
import { useEpisodesTranslation } from "./episodes-i18n";
import { EpisodeDetails, episodeButton as button, episodeInput as input } from "./EpisodeDetails";

interface Props { api: EpisodesApi; creatorApi: CreatorProjectsPort; projectId: string; mode: "editor" | "library"; onNavigate: (target: string) => void }
export function EpisodeWorkspace(props: Props) {
  const { t } = useEpisodesTranslation();
  return <CreatorPage api={props.creatorApi} permission="project.view" title={t(props.mode)}>{(context) => <Workspace key={`${context.workspace.id}:${props.projectId}:${props.mode}`} {...props} canManage={context.permissions.includes("project.manage")} />}</CreatorPage>;
}

const signature = (items: Episode[]) => JSON.stringify(items.map(episodeDraft));
function validItems(items: Episode[], sources: EpisodesSnapshot["sources"]) {
  return items.every((item) => item.title.trim() && Number.isInteger(item.targetDuration) && item.targetDuration >= 1 && item.targetDuration <= 3600
    && (item.budgetMinor === null || Number.isSafeInteger(item.budgetMinor) && item.budgetMinor >= 0 && item.budgetMinor <= 1_000_000_000)
    && (!item.sourceRange || Number.isInteger(item.sourceRange.start) && Number.isInteger(item.sourceRange.end) && item.sourceRange.start >= 0 && item.sourceRange.end > item.sourceRange.start && item.sourceRange.end <= Array.from(sources.find((doc) => doc.path === item.sourceRange?.source_file)?.text ?? "").length));
}
function totals(values: Record<string, Record<string, number>> | undefined) {
  const amounts: Record<string, number> = {};
  for (const breakdown of Object.values(values ?? {})) for (const [currency, value] of Object.entries(breakdown)) amounts[currency] = (amounts[currency] ?? 0) + value;
  return Object.entries(amounts).map(([currency, value]) => `${currency} ${value.toFixed(2)}`).join(" · ");
}

function Workspace({ api, projectId, mode, onNavigate, canManage }: Props & { canManage: boolean }) {
  const { t } = useEpisodesTranslation();
  const [data, setData] = useState<EpisodesSnapshot>();
  const [draft, setDraft] = useState<Episode[]>([]);
  const [active, setActive] = useState<number>();
  const [selected, setSelected] = useState<number[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<Error>();
  const [search, setSearch] = useState(() => sessionStorage.getItem(`episodes:${projectId}:search`) ?? "");
  const [status, setStatus] = useState(() => sessionStorage.getItem(`episodes:${projectId}:status`) ?? "");
  const [page, setPage] = useState(1);
  const [source, setSource] = useState("");
  const [count, setCount] = useState(1);
  const [preview, setPreview] = useState<EpisodeDraft[]>();
  const [checking, setChecking] = useState(false);
  const [costs, setCosts] = useState<EpisodeCosts>();
  const [costError, setCostError] = useState(false);
  const [costBusy, setCostBusy] = useState(false);
  const draftRef = useRef(draft);
  const mounted = useRef(false);
  const saving = useRef(false);
  const dragging = useRef<number | undefined>(undefined);

  const load = useCallback(() => api.list(projectId).then((result) => {
      if (!mounted.current) return;
      setError(undefined);
      setData(result); setDraft(result.items); draftRef.current = result.items; setDirty(false);
      const remembered = Number(sessionStorage.getItem(`episodes:${projectId}:active`));
      setActive(result.items.find((item) => item.episode === remembered)?.episode ?? result.items[0]?.episode);
      setSource(result.sources[0]?.path ?? ""); setCount(Math.max(1, Math.min(200, result.plannedCount - result.items.length)));
      setPreview(undefined); setSelected([]); setChecking(false);
    }).catch((reason: Error) => { if (mounted.current) setError(reason); })
      .finally(() => { if (mounted.current) setBusy(false); }), [api, projectId]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; }; }, [load]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, busy]);

  const save = useCallback(async () => {
    if (!data || saving.current || !canManage || !validItems(draftRef.current, data.sources)) return;
    saving.current = true; setBusy(true); setError(undefined);
    const sent = draftRef.current;
    try {
      const result = await api.save(projectId, data.revision, sent);
      if (!mounted.current) return;
      setData((current) => ({ ...current, ...result }));
      if (signature(sent) === signature(draftRef.current)) {
        setDraft(result.items); draftRef.current = result.items; setDirty(false);
      }
      setCosts(undefined);
    } catch (reason) { if (mounted.current) setError(reason as Error); }
    finally { saving.current = false; if (mounted.current) setBusy(false); }
  }, [api, projectId, data, canManage]);
  useEffect(() => {
    if (!dirty || busy || error || preview) return;
    const timer = window.setTimeout(() => void save(), 1100);
    return () => window.clearTimeout(timer);
  }, [draft, dirty, busy, error, preview, save]);

  function edit(items: Episode[]) {
    draftRef.current = items; setDraft(items); setDirty(true); setChecking(false);
    if (!(error instanceof CreatorApiError && error.status === 409)) setError(undefined);
  }
  function inspect(number: number) { setActive(number); sessionStorage.setItem(`episodes:${projectId}:active`, String(number)); }
  function go(target: string) { if (!dirty && !busy || window.confirm(t("leave"))) onNavigate(target); }
  function move(from: number, to: number) {
    if (!canManage || preview || from === to || to < 0 || to >= draft.length) return;
    const items = [...draft]; const [item] = items.splice(from, 1); items.splice(to, 0, item); edit(items);
  }
  function add(copy = false) {
    if (!data) return;
    const number = Math.max(data.nextEpisode, ...draft.map((item) => item.episode + 1));
    const previous = draft[draft.length - 1];
    const item = plannedEpisode({ episode: number, title: t("untitled", { number }), hook: copy ? previous?.hook ?? "" : "", storyBeats: copy ? [...(previous?.storyBeats ?? [])] : [], nextEpisodeTeaser: copy ? previous?.nextEpisodeTeaser ?? "" : "", targetDuration: copy ? previous?.targetDuration ?? data.defaultDuration : data.defaultDuration, budgetMinor: copy ? previous?.budgetMinor ?? null : null, sourceRange: null });
    edit([...draft, item]); inspect(number); setSearch(""); setStatus(""); setPage(Math.ceil((draft.length + 1) / 20));
  }
  async function split() {
    if (!data) return;
    setBusy(true); setError(undefined);
    try { const result = await api.preview(projectId, data.revision, source, count); if (mounted.current) setPreview(result); }
    catch (reason) { if (mounted.current) setError(reason as Error); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function readCosts() {
    setCostBusy(true); setCostError(false);
    try { const result = await api.costs(projectId); if (mounted.current) setCosts(result); }
    catch { if (mounted.current) setCostError(true); }
    finally { if (mounted.current) setCostBusy(false); }
  }
  function downloadDraft() {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ projectId, revision: data?.revision, items: draft.map(episodeDraft) }, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `episodes-${projectId}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const filtered = draft.filter((item) => (!status || item.status === status) && `${item.episode} ${item.title} ${item.hook}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const currentPage = Math.min(page, pages);
  const visible = filtered.slice((currentPage - 1) * 20, currentPage * 20);
  const current = draft.find((item) => item.episode === active);
  const canAdd = canManage && !preview && !busy && draft.length < 500 && !(data?.contentMode === "ad" && draft.length > 0);
  const invalid = data && !validItems(draft, data.sources);
  const errorCode = error instanceof CreatorApiError ? error.code : "";
  const errorMessage = errorCode.startsWith("EPISODE_") && t(errorCode) !== errorCode ? t(errorCode) : error instanceof CreatorApiError && error.status === 409 ? t("conflict") : t("error");
  const statusLabel = (value: string) => value === "completed" ? t("completedStatus") : value === "scripted" ? t("scriptedStatus") : value === "in_production" ? t("in_production") : t("draft");
  const issuesFor = (item: Episode) => [...(item.hook.trim() ? [] : ["hook_missing"]), ...(item.sourceRange ? [] : ["source_missing"]), ...(item.issues.includes("source_changed") ? ["source_changed"] : []), ...(item.scriptStatus === "generated" ? [] : ["script_missing"])];

  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-lg font-semibold">{data?.projectName}</p><p className="mt-1 text-sm text-slate-400">{t("intro")}</p></div><div className="flex flex-wrap items-center gap-2"><button className={button} onClick={() => go("control")}>{t("control")}</button><button className={button} onClick={() => go(mode === "editor" ? "episodes" : "episode-split-editor")}>{t(mode === "editor" ? "library" : "editor")}</button><span role="status" className={`px-2 text-xs ${error || invalid ? "text-amber-300" : dirty || busy ? "text-slate-400" : "text-emerald-300"}`}>{busy ? t("saving") : dirty ? t("unsaved") : data ? t("saved") : t("loading")}</span></div></div>
    {!canManage && <p className="text-sm text-amber-200">{t("readonly")}</p>}
    {error && <div role="alert" className="rounded-xl border border-amber-700/60 bg-amber-950/30 p-4 text-sm text-amber-100"><p>{errorMessage}</p>{error instanceof CreatorApiError && <p className="mt-2 font-mono text-xs">{error.code}{error.requestId ? ` · ${error.requestId}` : ""}</p>}<div className="mt-3 flex gap-2">{data && <button className={button} onClick={downloadDraft}>{t("downloadDraft")}</button>}<button disabled={busy} className={button} onClick={() => { if (!dirty || window.confirm(t("discard"))) void load(); }}>{t("reload")}</button>{data && !(error instanceof CreatorApiError && error.status === 409) && <button className={button} disabled={busy || !!invalid} onClick={() => void save()}>{t("retry")}</button>}</div></div>}
    {!data && !error && <p className="p-8 text-center text-slate-400">{t("loading")}</p>}
    {data && <>
      <div className="grid grid-cols-2 divide-x divide-[#2A2E3A] rounded-xl border border-[#2A2E3A] bg-[#12141B] py-4 md:grid-cols-4">{[["total", draft.length], ["scripted", draft.filter((e) => e.scriptStatus === "generated").length], ["inProduction", draft.filter((e) => e.status === "in_production").length], ["completed", draft.filter((e) => e.status === "completed").length]].map(([label, value]) => <div key={label} className="px-5"><p className="text-xs text-slate-400">{t(String(label))}</p><p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p></div>)}</div>
      {mode === "editor" && <section className="rounded-xl border border-[#2A2E3A] bg-[#12141B] p-5"><div className="flex flex-wrap items-end gap-3"><div className="min-w-52 flex-1"><h2 className="font-semibold">{t("split")}</h2><p className="mt-2 max-w-3xl text-xs leading-6 text-slate-400">{t("splitHelp")}</p></div><label className="text-xs text-slate-400">{t("source")}<select className={input} value={source} onChange={(e) => setSource(e.target.value)} disabled={!canManage || busy || !!preview}><option value="">{t("noSource")}</option>{data.sources.map((doc) => <option key={doc.path} value={doc.path}>{doc.path}</option>)}</select></label><label className="w-24 text-xs text-slate-400">{t("count")}<input type="number" min={1} max={200} className={input} value={count} onChange={(e) => setCount(Number(e.target.value))} disabled={!canManage || busy || !!preview} /></label><button className={`${button} bg-[#FF6B4A] font-semibold text-[#1B100C]`} disabled={!canManage || busy || dirty || !!preview || !source || !Number.isInteger(count) || count < 1 || count > 200 || data.contentMode === "ad"} onClick={() => void split()}>{t("preview")}</button></div>
        {preview && <div className="mt-5 border-t border-[#2A2E3A] pt-4"><h3 className="text-sm font-semibold">{t("result", { count: preview.length })}</h3><ol className="mt-3 max-h-64 space-y-2 overflow-y-auto">{preview.map((item) => <li key={item.episode} className="flex gap-3 text-sm"><span className="font-mono text-[#FF9B83]">{item.episode}</span><span className="flex-1">{item.title}</span><span className="text-xs tabular-nums text-slate-400">{item.sourceRange?.start}–{item.sourceRange?.end}</span></li>)}</ol><div className="mt-4 flex gap-3"><button className={`${button} bg-[#FF6B4A] text-[#1B100C]`} disabled={draft.length + preview.length > 500} onClick={() => { edit([...draft, ...preview.map(plannedEpisode)]); inspect(preview[0].episode); setPreview(undefined); }}>{t("apply")}</button><button className={button} onClick={() => setPreview(undefined)}>{t("cancel")}</button></div></div>}
      </section>}
      <div className="flex flex-wrap items-center gap-2"><input aria-label={t("search")} placeholder={t("search")} className={`${input} !mt-0 !w-auto min-w-60 flex-1`} value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); sessionStorage.setItem(`episodes:${projectId}:search`, e.target.value); }} /><select aria-label={t("all")} className={`${input} !mt-0 !w-auto`} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); sessionStorage.setItem(`episodes:${projectId}:status`, e.target.value); }}><option value="">{t("all")}</option>{["draft", "scripted", "in_production", "completed"].map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select><button className={button} disabled={!canAdd} onClick={() => add()}><Plus size={15} className="mr-1 inline" />{t("add")}</button><button className={button} title={t("copyHelp")} disabled={!canAdd || draft.length === 0} onClick={() => add(true)}>{t("copy")}</button><button className={button} disabled={!canManage || !dirty || busy || !!preview || !!invalid || (error instanceof CreatorApiError && error.status === 409)} onClick={() => void save()}>{t("save")}</button></div>
      {invalid && <p role="status" className="text-sm text-amber-300">{t("invalid")}</p>}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <section className="min-w-0 overflow-hidden rounded-xl border border-[#2A2E3A] bg-[#12141B]">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2A2E3A] p-4"><span className="text-xs text-slate-400">{t("selected", { count: selected.length })}</span><div className="flex gap-2"><button className={button} disabled={!selected.length || dirty || busy} onClick={() => setChecking(true)}>{t("health")}</button><button className={button} disabled={costBusy || dirty} onClick={() => void readCosts()}><RefreshCw size={14} className={`mr-1 inline ${costBusy ? "animate-spin" : ""}`} />{t("refreshCosts")}</button></div></div>
          <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-[#2A2E3A] text-xs text-slate-400"><tr><th className="p-3"><input type="checkbox" aria-label={t("selectAll")} checked={visible.length > 0 && visible.every((item) => selected.includes(item.episode))} onChange={(e) => setSelected(e.target.checked ? [...new Set([...selected, ...visible.map((item) => item.episode)])] : selected.filter((id) => !visible.some((item) => item.episode === id)))} /></th><th className="p-3">{t("order")}</th><th className="min-w-48 p-3">{t("title")}</th><th className="p-3">{t("all")}</th><th className="p-3">{t("progress")}</th><th className="min-w-36 p-3">{t("costs")}</th></tr></thead><tbody>{visible.map((item) => {
            const position = draft.findIndex((candidate) => candidate.episode === item.episode);
            const cost = costs?.episodes.find((candidate) => candidate.episode === item.episode);
            return <tr key={item.episode} className={`border-b border-[#252936] ${active === item.episode ? "bg-[#FF6B4A]/8" : "hover:bg-white/3"}`} onDragOver={(e) => { if (canManage) e.preventDefault(); }} onDrop={(e) => { e.preventDefault(); if (dragging.current !== undefined) move(draft.findIndex((entry) => entry.episode === dragging.current), position); dragging.current = undefined; }}><td className="p-3"><input type="checkbox" aria-label={t("select", { number: item.episode })} checked={selected.includes(item.episode)} onChange={(e) => setSelected(e.target.checked ? [...selected, item.episode] : selected.filter((number) => number !== item.episode))} /></td><td className="p-3"><div className="flex items-center gap-1"><button draggable={canManage && !preview} aria-label={t("drag")} className="cursor-grab text-slate-500" disabled={!canManage || !!preview} onDragStart={(event) => { event.dataTransfer.setData("text/plain", String(item.episode)); event.dataTransfer.effectAllowed = "move"; dragging.current = item.episode; }} onDragEnd={() => { dragging.current = undefined; }}><GripVertical size={16} /></button><span className="w-5 font-mono tabular-nums">{position + 1}</span><div className="flex flex-col"><button aria-label={`${t("up")} ${item.episode}`} disabled={!canManage || !!preview || position === 0} className="p-1 disabled:opacity-20" onClick={() => move(position, position - 1)}><ArrowUp size={12} /></button><button aria-label={`${t("down")} ${item.episode}`} disabled={!canManage || !!preview || position === draft.length - 1} className="p-1 disabled:opacity-20" onClick={() => move(position, position + 1)}><ArrowDown size={12} /></button></div></div></td><td className="p-3"><button className="text-left hover:text-[#FF9B83]" aria-label={t("inspect", { number: item.episode })} onClick={() => inspect(item.episode)}><span className="block max-w-72 truncate font-medium">{item.title}</span><span className="mt-1 block text-xs text-slate-500">{t("episode")} {item.episode} · {item.targetDuration}s · {item.shots} {t("shots")}</span></button></td><td className="whitespace-nowrap p-3 text-xs text-slate-300">{statusLabel(item.status)}</td><td className="p-3 text-xs tabular-nums text-slate-400"><p>{data.generationMode === "reference_video" ? "—" : `${item.storyboards.completed}/${item.storyboards.total}`}</p><p className="mt-1">{item.videos.completed}/{item.videos.total}</p></td><td className="p-3 text-xs tabular-nums text-slate-400"><p>{totals(cost?.totals.estimate) || t("unknown")}</p><p className="mt-1">{totals(cost?.totals.actual) || t("unknown")}</p></td></tr>;
          })}</tbody></table></div>
          {!visible.length && <div className="p-10 text-center"><p>{t(draft.length ? "noMatch" : "empty")}</p>{!draft.length && <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-slate-400">{t("emptyHelp")}</p>}</div>}
          <div className="flex items-center justify-between gap-3 p-4 text-xs text-slate-400"><span>{t("page", { current: currentPage, total: pages })}</span><div className="flex gap-2"><button className={button} disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>{t("previous")}</button><button className={button} disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}>{t("next")}</button></div></div>
        </section>
        {current && <EpisodeDetails episode={current} sources={data.sources} disabled={!canManage || !!preview} update={(patch) => edit(draft.map((item) => item.episode === current.episode ? { ...item, ...patch } : item))} />}
      </div>
      <p className="text-xs leading-6 text-slate-500">{t("estimateHelp")}{data.generationMode === "reference_video" ? ` ${t("reference")}` : ""}</p>{costError && <p role="alert" className="text-sm text-amber-300">{t("costsFailed")}</p>}
      {checking && <section className="rounded-xl border border-[#2A2E3A] bg-[#12141B] p-5"><h2 className="font-semibold">{t("healthTitle")}</h2><p className="mt-2 text-xs text-slate-400">{t("healthHelp")}</p><ul className="mt-4 space-y-3">{draft.filter((item) => selected.includes(item.episode)).map((item) => <li key={item.episode} className="flex flex-wrap gap-3 text-sm"><strong>{item.episode} · {item.title}</strong><span className={issuesFor(item).length ? "text-amber-300" : "text-emerald-300"}>{issuesFor(item).map((issue) => t(issue)).join(" · ") || t("healthy")}</span></li>)}</ul></section>}
      <nav className="flex flex-wrap gap-3 border-t border-[#2A2E3A] pt-5">{[["script-lab", "script"], ["shots", "storyboard"], ["release-package", "release"]].map(([target, label]) => <button key={target} className={button} onClick={() => go(target)}>{t(label)} →</button>)}</nav>
    </>}
  </div>;
}
