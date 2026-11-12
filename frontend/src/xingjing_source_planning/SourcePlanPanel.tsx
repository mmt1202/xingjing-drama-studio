import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EpisodeSource, EpisodesSnapshot } from "@/xingjing_creator_projects/episodes-api";
import { episodeSourceLabel } from "@/xingjing_creator_projects/episodes-api";
import { candidateInput, createSourcePlanApi, denied, uncertain, type Candidate, type Catalog, type PendingOperation, type Plan, type PlanHistory, type SourcePlanApi } from "./api";
import { CandidateEditor, validCandidates } from "./CandidateEditor";
import { button, card, field, primary, problemText, useSourcePlanTranslation } from "./i18n";
import { readDraft, storageKey, storeDraft, type PlanDraft } from "./storage";

interface Props {
  workspaceId: string; actorId: string; projectId: string; episodes: EpisodesSnapshot; initialSource: string;
  blocked: boolean; canManage: boolean; onBusy: (value: boolean) => void; onBlocked: (value: boolean) => void;
  onRefresh: () => Promise<void>; onNavigate: (target: string) => void; api?: SourcePlanApi;
}
export function SourcePlanPanel(props: Props) {
  const { t } = useSourcePlanTranslation();
  const api = useMemo(() => props.api ?? createSourcePlanApi(props.workspaceId), [props.api, props.workspaceId]);
  const storage = storageKey(props.workspaceId, props.actorId, props.projectId);
  const [saved, setSaved] = useState<PlanDraft>(() => readDraft(storage));
  const savedRef = useRef(saved), mounted = useRef(true), flight = useRef(false), accessEpoch = useRef(0);
  const [history, setHistory] = useState<PlanHistory>(), [historyError, setHistoryError] = useState<unknown>();
  const [sourceId, setSourceId] = useState(props.initialSource), [count, setCount] = useState(Math.max(1, Math.min(200, props.episodes.plannedCount - props.episodes.items.length)));
  const [catalogState, setCatalog] = useState<{ sourceId: string; data?: Catalog; error?: unknown }>();
  const [planState, setPlan] = useState<{ id: string; data?: Plan; error?: unknown }>();
  const [chapterIds, setChapters] = useState<string[]>([]), [chapterLimit, setChapterLimit] = useState(100);
  const [reviewing, setReviewing] = useState(false);
  const [busy, setBusy] = useState(false), [reload, setReload] = useState(0);
  const [error, setError] = useState<unknown>(), [accessError, setAccessError] = useState<unknown>(), [storageFailed, setStorageFailed] = useState(false);
  const source = props.episodes.sources.find(item => item.path === sourceId);
  const catalog = catalogState?.sourceId === sourceId ? catalogState.data : undefined;
  const plan = planState && planState.id === saved.planId ? planState.data : undefined;
  const candidates = plan ? saved.candidates ?? plan.proposal.candidates.map(candidateInput) : [];
  const included = saved.included ?? candidates.map(item => item.id);
  const modified = Boolean(plan && !plan.result && (JSON.stringify(candidates) !== JSON.stringify(plan.proposal.candidates.map(candidateInput)) || included.length !== candidates.length));
  const pending = saved.pending;
  const { onBlocked, onBusy } = props;
  useEffect(() => { onBlocked(modified || Boolean(pending) || busy || reviewing); return () => onBlocked(false); }, [modified, pending, busy, reviewing, onBlocked]);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!modified && !pending && !busy && !reviewing) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [modified, pending, busy, reviewing]);
  const persist = useCallback((value: PlanDraft): boolean => {
    const retained = storeDraft(storage, value);
    savedRef.current = value; setSaved(value); setStorageFailed(!retained);
    return retained;
  }, [storage]);
  const deny = useCallback((reason: unknown) => {
    if (!denied(reason)) return;
    accessEpoch.current += 1; setAccessError(reason); setHistory(undefined); setPlan(undefined); setCatalog(undefined);
  }, []);
  useEffect(() => {
    const controller = new AbortController(), epoch = accessEpoch.current;
    api.list(props.projectId, controller.signal).then(value => {
      if (!controller.signal.aborted && epoch === accessEpoch.current) { setHistory(value); setHistoryError(undefined); setAccessError(undefined); }
    }).catch(reason => { if (!controller.signal.aborted && epoch === accessEpoch.current) { setHistoryError(reason); deny(reason); } });
    return () => controller.abort();
  }, [api, props.projectId, reload, deny]);
  useEffect(() => {
    if (!sourceId) return;
    const controller = new AbortController(), epoch = accessEpoch.current;
    api.catalog(props.projectId, sourceId, controller.signal).then(value => {
      if (!controller.signal.aborted && epoch === accessEpoch.current) { setCatalog({ sourceId, data: value }); setChapters(value.chapters.map(item => item.id)); setChapterLimit(100); }
    }).catch(reason => { if (!controller.signal.aborted && epoch === accessEpoch.current) { setCatalog({ sourceId, error: reason }); deny(reason); } });
    return () => controller.abort();
  }, [api, props.projectId, sourceId, source?.digest, reload, deny]);
  useEffect(() => {
    if (!saved.planId) return;
    const id = saved.planId, controller = new AbortController(), epoch = accessEpoch.current;
    api.get(props.projectId, id, controller.signal).then(value => {
      if (controller.signal.aborted || epoch !== accessEpoch.current) return;
      setPlan({ id, data: value });
      if (value.pendingConfirmation && !savedRef.current.pending) {
        const recovery = value.pendingConfirmation;
        const changed = new Map(recovery.payload.candidates.map(item => [item.id, item]));
        persist({ planId: id, candidates: value.proposal.candidates.map(item => changed.get(item.id) ?? candidateInput(item)), included: recovery.payload.candidates.map(item => item.id), pending: { kind: "confirm", planId: id, key: recovery.key, payload: recovery.payload } });
      }
    }).catch(reason => { if (!controller.signal.aborted && epoch === accessEpoch.current) { setPlan({ id, error: reason }); deny(reason); } });
    return () => controller.abort();
  }, [api, props.projectId, saved.planId, reload, deny, persist]);

  async function execute(operation: PendingOperation) {
    if (flight.current || props.blocked || !props.canManage || history?.canManage !== true || accessError) return;
    if (!persist({ ...savedRef.current, pending: operation })) return;
    flight.current = true; setBusy(true); setError(undefined);
    try {
      if (operation.kind === "create") {
        const result = await api.create(props.projectId, operation.payload, operation.key);
        if (!result?.id || result.projectId !== props.projectId || result.source.digest !== operation.payload.sourceDigest || !Array.isArray(result.proposal?.candidates)) throw new Error(t("resultMismatch"));
        if (mounted.current) {
          persist({ planId: result.id, candidates: result.proposal.candidates.map(candidateInput), included: result.proposal.candidates.map(item => item.id) });
          setPlan({ id: result.id, data: result }); setReload(value => value + 1);
        }
      } else {
        const result = await api.confirm(props.projectId, operation.planId, operation.payload, operation.key);
        if (result?.planId !== operation.planId || !Array.isArray(result.items) || !result.episodeRevision) throw new Error(t("resultMismatch"));
        if (mounted.current) {
          persist({ ...savedRef.current, pending: undefined });
          setPlan(previous => previous?.id === operation.planId && previous.data ? { id: previous.id, data: { ...previous.data, result, confirmationPending: false, pendingConfirmation: null } } : previous);
          setReload(value => value + 1);
          await props.onRefresh();
        }
      }
    } catch (reason) {
      if (mounted.current) {
        if (!uncertain(reason)) persist({ ...savedRef.current, pending: undefined });
        setError(reason); deny(reason);
      }
    } finally { flight.current = false; if (mounted.current) setBusy(false); }
  }
  const canWrite = props.canManage && history?.canManage === true && !props.blocked && !busy && !pending && !modified && !reviewing;
  const choose = (id: string) => { persist({ planId: id }); setError(undefined); };
  const update = (next: Candidate[], selected: string[]) => { persist({ ...savedRef.current, candidates: next, included: selected }); setError(undefined); };
  const selectedCharacters = catalog?.uncoveredRanges.reduce((sum, [start, end]) => sum + (catalog.chapters.filter(item => chapterIds.includes(item.id)).reduce((total, chapter) => total + Math.max(0, Math.min(end, chapter.end) - Math.max(start, chapter.start)), 0)), 0) ?? 0;
  const refresh = async () => { if (!props.blocked && !busy && !modified && !reviewing) { await props.onRefresh(); if (mounted.current) setReload(value => value + 1); } };
  const readError = saved.planId && planState?.id === saved.planId ? planState.error : undefined;
  const catalogError = catalogState?.sourceId === sourceId ? catalogState.error : undefined;
  const displaySources: EpisodeSource[] = props.episodes.sources;
  return <section className={card}>
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">{t("title")}</h2><p className="mt-2 max-w-3xl text-sm leading-7 text-slate-400">{t("intro")}</p></div><button className={button} disabled={busy || props.blocked || modified || reviewing} onClick={() => void refresh()}>{t("refresh")}</button></div>
    {accessError ? <p role="alert" className="mt-4 text-sm text-amber-200">{problemText(accessError, t)}</p> : <>
      {pending && <div role="status" className="mt-5 rounded-xl border border-amber-300/25 bg-amber-500/5 p-4"><p className="text-sm leading-7 text-amber-100">{t("pending")}</p><button className={button + " mt-3"} disabled={busy || props.blocked || !props.canManage || history?.canManage !== true} onClick={() => void execute(pending)}>{t(busy ? "working" : "retry")}</button></div>}
      {storageFailed && <p role="alert" className="mt-4 text-sm text-amber-200">{t("storageRequired")}</p>}
      {error && <p role="alert" className="mt-4 text-sm text-amber-200">{problemText(error, t)}</p>}
      {props.blocked && <p className="mt-4 text-xs text-slate-500">{t("blocked")}</p>}
      {history?.canManage === false && <p className="mt-4 text-sm text-amber-200">{t("readonly")}</p>}
      {displaySources.length ? <div className="mt-5 grid gap-4 md:grid-cols-[1fr_160px_auto]"><label className="text-sm text-slate-400">{t("sourceLabel")}<select className={field} value={sourceId} disabled={props.blocked || busy || !!pending || modified || reviewing} onChange={event => { setSourceId(event.target.value); setError(undefined); }}><option value="">{t("sourcePlaceholder")}</option>{displaySources.map(item => <option key={item.path} value={item.path}>{episodeSourceLabel(item, displaySources)}</option>)}</select></label><label className="text-sm text-slate-400">{t("count")}<input className={field} type="number" min={1} max={200} value={count} disabled={!canWrite} onChange={event => setCount(Number(event.target.value))} /></label><button className={primary + " self-end"} disabled={!canWrite || !catalog || catalog.sourceChanged || catalog.episodeRevision !== props.episodes.revision || !chapterIds.length || !Number.isInteger(count) || count < 1 || count > 200 || selectedCharacters === 0} onClick={() => { if (catalog) void execute({ kind: "create", key: crypto.randomUUID(), payload: { sourceId, sourceDigest: catalog.source.digest, episodeRevision: catalog.episodeRevision, count, chapterIds } }); }}>{t(busy && pending?.kind === "create" ? "creating" : "create")}</button></div> : <p className="mt-5 text-sm text-slate-400">{t("noSources")}</p>}
      <p className="mt-3 text-xs leading-6 text-slate-500">{t("countHelp")}</p>
      {catalogError ? <p role="alert" className="mt-4 text-sm text-amber-200">{problemText(catalogError, t)}</p> : sourceId && !catalog ? <p role="status" className="mt-4 text-sm text-slate-400">{t("loading")}</p> : catalog ? <details className="mt-5" open><summary className="cursor-pointer text-sm text-violet-200">{t("chapters")} · {t("chapterCount", { count: catalog.chapters.length })}</summary><p className="mt-3 text-sm text-slate-300">{t("uncovered", { count: selectedCharacters })}</p>{catalog.sourceChanged || catalog.episodeRevision !== props.episodes.revision ? <p className="mt-3 text-sm text-amber-200">{t("sourceChanged")}</p> : null}<div className="mt-3 flex flex-wrap gap-2"><button className={button} disabled={!canWrite} onClick={() => setChapters(catalog.chapters.map(item => item.id))}>{t("selectAll")}</button><button className={button} disabled={!canWrite} onClick={() => setChapters([])}>{t("clearSelected")}</button><span className="self-center text-xs text-slate-500">{t("selectedChapters", { count: chapterIds.length })}</span></div><div className="mt-3 max-h-72 space-y-2 overflow-auto">{catalog.chapters.slice(0, chapterLimit).map(chapter => <label key={chapter.id} className="flex items-center gap-3 rounded-lg border border-white/5 px-3 py-2 text-sm"><input type="checkbox" checked={chapterIds.includes(chapter.id)} disabled={!canWrite} onChange={event => setChapters(event.target.checked ? [...chapterIds, chapter.id] : chapterIds.filter(id => id !== chapter.id))} /><span className="flex-1">{chapter.ordinal}. {chapter.title ?? t(chapter.kind)}</span><span className="text-xs tabular-nums text-slate-500">{chapter.start}–{chapter.end}</span></label>)}</div>{chapterLimit < catalog.chapters.length && <button className={button + " mt-3"} onClick={() => setChapterLimit(value => value + 100)}>{t("showMoreChapters")}</button>}</details> : null}
      <details className="mt-5 border-t border-white/10 pt-4"><summary className="cursor-pointer text-sm text-slate-300">{t("history")}</summary>{historyError ? <p role="alert" className="mt-3 text-sm text-amber-200">{problemText(historyError, t)}</p> : history?.items.length ? <ul className="mt-3 max-h-64 space-y-2 overflow-auto">{history.items.map(item => <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-white/5 p-3"><span className="text-xs text-slate-400">{item.sourceLabel} · {item.count} · {t(item.confirmationPending ? "pendingHistory" : item.applied ? "applied" : "proposed")}<time className="mt-1 block text-slate-500" dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time></span><button className={button} disabled={busy || !!pending || modified || reviewing || props.blocked} onClick={() => choose(item.id)}>{t("open")}</button></li>)}</ul> : <p className="mt-3 text-sm text-slate-500">{t(history ? "noHistory" : "loading")}</p>}</details>
      {readError && <p role="alert" className="mt-4 text-sm text-amber-200">{problemText(readError, t)}</p>}
      {saved.planId && !plan && !readError && <p role="status" className="mt-4 text-sm text-slate-400">{t("loading")}</p>}
      {plan && <CandidateEditor key={plan.id} plan={plan} api={api} candidates={candidates} included={included} busy={busy} blocked={props.blocked || storageFailed} modified={modified}
        canManage={props.canManage && plan.canManage && history?.canManage === true} pending={!!pending} outdated={plan.episodeRevision !== props.episodes.revision && !plan.result && !plan.confirmationPending}
        onReviewChange={setReviewing} onChange={update} onConfirm={items => { if (validCandidates(plan, items)) void execute({ kind: "confirm", planId: plan.id, key: crypto.randomUUID(), payload: { candidates: items.map(candidateInput) } }); }}
        onReset={() => persist({ planId: plan.id, candidates: plan.proposal.candidates.map(candidateInput), included: plan.proposal.candidates.map(item => item.id) })}
        onClose={() => { persist({}); setPlan(undefined); }} onNavigate={props.onNavigate} onDenied={deny} />}
    </>}
  </section>;
}
