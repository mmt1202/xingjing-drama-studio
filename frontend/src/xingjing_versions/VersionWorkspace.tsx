import { getTokenForSession } from "@/utils/auth";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Camera, ChevronRight, FileClock, GitCompareArrows, LockKeyhole, RefreshCw, RotateCcw } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { useAuthStore } from "@/stores/auth-store";
import { useProjectFlow, useProjectNavigationBlocker } from "@/xingjing_creator_projects/ProjectFlowShell";
import { ComparisonPanel } from "./ComparisonPanel";
import { createVersionApi, VersionApiError } from "./api";
import { sameVersionObject, versionRefKey, type CheckpointPage, type VersionComparison, type VersionDetail, type VersionKind, type VersionNode, type VersionPage, type VersionRef } from "./contracts";
import { useVersionTranslation, versionButton, versionField, versionPanel } from "./i18n";
import "./versions.css";
import { readVersionReturn, rememberVersionReturn, restoreVersionScroll } from "./return-state";
import { readVersionDraft, saveVersionDraft } from "./draft-state";

const kinds: readonly VersionKind[] = ["script", "asset", "shot", "storyboard", "final"];
type Editor = { action: "snapshot" | "lock" | "restore"; node: VersionNode; label: string; note: string; actorId?: string };
const clearCachedValue = (error: unknown) => error instanceof VersionApiError && [401, 403, 404].includes(error.status);
type ReadState<T> = { key: string; revision: number; value?: T; error?: unknown };

function VersionError({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useVersionTranslation();
  const problem = error instanceof VersionApiError ? error : undefined;
  const key = problem?.status === 403 ? "forbidden" : problem?.status === 404 ? "missing" : problem?.status === 409 ? "conflict" : problem?.status === 503 ? "serviceUnavailable" : "failed";
  return <div role="alert" className="rounded-xl border border-amber-300/20 bg-amber-300/[.04] p-4 text-sm leading-6 text-amber-100">
    <p>{problem ? t(`error.${problem.code}`, { defaultValue: t(key) }) : t(key)}</p>{problem ? <p className="mt-2 break-all text-[11px] text-slate-400">{problem.code}{problem.requestId ? ` · ${t("requestId")}: ${problem.requestId}` : ""}</p> : null}
    {retry ? <button type="button" className={versionButton + " mt-3"} onClick={retry}>{t("retry")}</button> : null}
  </div>;
}

function downloadReport(report: unknown, id: string) {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `version-comparison-${id.replace(/[^a-zA-Z0-9_-]/g, "_")}.json`;
  document.body.append(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function VersionWorkspace({ workspaceId, projectId }: { workspaceId: string; projectId: string }) {
  const { t, i18n } = useVersionTranslation();
  const { go } = useProjectFlow();
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const api = useMemo(() => createVersionApi(workspaceId, projectId, getTokenForSession(sessionEpoch)), [workspaceId, projectId, sessionEpoch]);
  const [location, navigate] = useLocation();
  const search = useSearch();
  const params = new URLSearchParams(search);
  const filterKey = new URLSearchParams({ kind: params.get("kind") ?? "", q: params.get("q") ?? "", episodeId: params.get("episodeId") ?? "", objectId: params.get("objectId") ?? "", shotId: params.get("shotId") ?? "", state: params.get("state") ?? "", offset: params.get("offset") ?? "0", limit: "20" }).toString();
  const [reload, setReload] = useState(0);
  const [pageState, setPage] = useState<ReadState<VersionPage>>();
  const [detailState, setDetail] = useState<ReadState<VersionDetail>>();
  const [checkpointState, setCheckpoints] = useState<ReadState<CheckpointPage>>();
  const [comparisonState, setComparison] = useState<{ key: string; value?: VersionComparison; error?: unknown }>();
  const [comparing, setComparing] = useState(false);
  const [editor, setEditor] = useState<Editor>();
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState<unknown>();
  const [saved, setSaved] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [, refreshSavedDraft] = useState(0);
  const locked = useRef(false);
  const comparisonRequest = useRef<AbortController | null>(null);
  const restoredScroll = useRef(false);
  const dirty = Boolean(editor && (editor.label.trim() || editor.note.trim()));
  useProjectNavigationBlocker(dirty || busy);
  useEffect(() => {
    if (!dirty && !busy) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, busy]);
  useEffect(() => () => comparisonRequest.current?.abort(), []);
  useEffect(() => {
    const abort = new AbortController();
    const filters = new URLSearchParams(filterKey);
    void api.list({ kind: filters.get("kind") ?? "", q: filters.get("q") ?? "", episodeId: filters.get("episodeId") ?? "", objectId: filters.get("objectId") ?? "", shotId: filters.get("shotId") ?? "", state: filters.get("state") ?? "", offset: Number(filters.get("offset")), limit: 20 }, abort.signal)
      .then((value) => { if (!abort.signal.aborted) setPage({ key: filterKey, revision: reload, value }); })
      .catch((error: unknown) => { if (!abort.signal.aborted) setPage((previous) => ({ key: filterKey, revision: reload, value: previous?.key === filterKey && !clearCachedValue(error) ? previous.value : undefined, error })); });
    return () => abort.abort();
  }, [api, filterKey, reload]);
  const page = pageState?.key === filterKey ? pageState.value : undefined;
  const actorId = page?.scope.actorId ?? editor?.actorId;
  const savedDraft = actorId ? readVersionDraft(actorId, workspaceId, projectId) : undefined;
  useEffect(() => {
    if (!actorId) return;
    if (!restoredScroll.current) {
      restoredScroll.current = true;
      const previous = readVersionReturn(actorId, workspaceId, projectId);
      if (previous.present && previous.query === new URLSearchParams(search).toString()) {
        const stop = restoreVersionScroll(previous.scroll);
        return () => { stop(); rememberVersionReturn(actorId, workspaceId, projectId, search); };
      }
    }
    return () => rememberVersionReturn(actorId, workspaceId, projectId, search);
  }, [actorId, workspaceId, projectId, search]);
  const selectedKind = params.get("kind");
  const selectionInvalid = params.has("versionId") && (!params.get("versionId")?.trim() || !params.get("objectId")?.trim()
    || !kinds.includes(selectedKind as VersionKind) || ((selectedKind === "shot") !== Boolean(params.get("shotId"))));
  const explicitRef: VersionRef | undefined = kinds.includes(selectedKind as VersionKind) && params.get("objectId") && params.get("versionId")
    ? { kind: selectedKind as VersionKind, objectId: params.get("objectId")!, versionId: params.get("versionId")!, ...(params.get("shotId") ? { shotId: params.get("shotId")! } : {}) } : undefined;
  const selectedRef = selectionInvalid ? undefined : explicitRef ?? page?.items[0]?.ref;
  const selectedKey = selectedRef ? versionRefKey(selectedRef) : "";
  const checkpointOffset = Math.max(0, Number(params.get("checkpointOffset") ?? "0") || 0);
  const checkpointKey = String(checkpointOffset);
  useEffect(() => {
    if (!selectedKey) return;
    const [kind, objectId, shotId, versionId] = JSON.parse(selectedKey) as [VersionKind, string, string, string];
    const abort = new AbortController();
    void api.detail({ kind, objectId, versionId, ...(shotId ? { shotId } : {}) }, abort.signal)
      .then((value) => { if (!abort.signal.aborted) setDetail({ key: selectedKey, revision: reload, value }); })
      .catch((error: unknown) => { if (!abort.signal.aborted) setDetail((previous) => ({ key: selectedKey, revision: reload, value: previous?.key === selectedKey && !clearCachedValue(error) ? previous.value : undefined, error })); });
    return () => abort.abort();
  }, [api, selectedKey, reload]);
  useEffect(() => {
    const abort = new AbortController();
    void api.checkpoints(checkpointOffset, abort.signal)
      .then((value) => { if (!abort.signal.aborted) setCheckpoints({ key: checkpointKey, revision: reload, value }); })
      .catch((error: unknown) => { if (!abort.signal.aborted) setCheckpoints((previous) => ({ key: checkpointKey, revision: reload, value: previous?.key === checkpointKey && !clearCachedValue(error) ? previous.value : undefined, error })); });
    return () => abort.abort();
  }, [api, checkpointKey, checkpointOffset, reload]);
  const detail = detailState?.key === selectedKey ? detailState.value : undefined;
  const node = detail?.node;
  const checkpoints = checkpointState?.key === checkpointKey ? checkpointState.value : undefined;
  const reading = pageState?.key !== filterKey || pageState.revision !== reload || Boolean(selectedKey && (detailState?.key !== selectedKey || detailState.revision !== reload));
  const waiting = busy || Boolean(editor) || reading;
  const stale = Boolean((pageState?.key === filterKey && pageState.error) || (detailState?.key === selectedKey && detailState.error));
  const conflict = writeError instanceof VersionApiError && writeError.status === 409;
  const versions = detail?.versions ?? [];
  const baselineId = params.get("baselineVersionId"), candidateId = params.get("candidateVersionId");
  const baseline = baselineId ? versions.find((item) => item.ref.versionId === baselineId) : versions.find((item) => item.ref.versionId !== node?.ref.versionId);
  const candidate = candidateId ? versions.find((item) => item.ref.versionId === candidateId) : node;
  const pairKey = baseline && candidate ? `${versionRefKey(baseline.ref)}:${versionRefKey(candidate.ref)}` : "";
  const comparison = comparisonState?.key === pairKey ? comparisonState.value : undefined;
  const comparisonError = comparisonState?.key === pairKey ? comparisonState.error : undefined;
  const canCompare = Boolean(baseline && candidate && sameVersionObject(baseline.ref, candidate.ref) && baseline.ref.versionId !== candidate.ref.versionId);
  const date = (value: string | null) => value ? new Date(value).toLocaleString(i18n.resolvedLanguage) : t("unknownTime");
  const refresh = () => setReload((value) => value + 1);
  function query(values: Record<string, string>) {
    if (busy || editor) return;
    comparisonRequest.current?.abort(); setComparing(false);
    const next = new URLSearchParams(search);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    navigate(`${location}?${next}`);
  }
  function choose(item: VersionNode) {
    query({ kind: item.ref.kind, objectId: item.ref.objectId, shotId: item.ref.shotId ?? "", versionId: item.ref.versionId, baselineVersionId: "", candidateVersionId: "", offset: "0" });
  }
  function filter(values: Record<string, string>) { query({ ...values, objectId: "", shotId: "", versionId: "", baselineVersionId: "", candidateVersionId: "", offset: "0" }); }
  function searchVersions(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const value = new FormData(event.currentTarget).get("q"); filter({ q: typeof value === "string" ? value.trim() : "" }); }
  function begin(action: Editor["action"], target: VersionNode) {
    if (waiting || comparing || selectionInvalid || stale || savedDraft) return;
    setEditor({ action, node: target, label: "", note: "", actorId }); setInvalid(false); setWriteError(undefined); setSaved(false);
  }
  function edit(next: Editor) {
    setEditor(next);
    if (actorId) saveVersionDraft(actorId, workspaceId, projectId, { action: next.action, ref: next.node.ref, expectedRevision: next.node.headRevision, sourceDigest: next.node.contentDigest, label: next.label, note: next.note });
  }
  function clearDraft() {
    if (actorId) saveVersionDraft(actorId, workspaceId, projectId);
    refreshSavedDraft((value) => value + 1);
  }
  function resumeDraft() {
    if (!savedDraft || waiting || stale) return;
    if (node && versionRefKey(node.ref) === versionRefKey(savedDraft.ref)) {
      setEditor({ action: savedDraft.action, node: { ...node, headRevision: savedDraft.expectedRevision, contentDigest: savedDraft.sourceDigest }, label: savedDraft.label, note: savedDraft.note, actorId });
      setWriteError(undefined); setSaved(false); return;
    }
    query({ kind: savedDraft.ref.kind, objectId: savedDraft.ref.objectId, shotId: savedDraft.ref.shotId ?? "", versionId: savedDraft.ref.versionId, q: "", state: "", episodeId: "", offset: "0", baselineVersionId: "", candidateVersionId: "" });
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!editor || locked.current || conflict || stale) return;
    if (!editor.note.trim() || (editor.action === "snapshot" && !editor.label.trim())) { setInvalid(true); return; }
    locked.current = true; setBusy(true); setWriteError(undefined); setInvalid(false);
    try {
      const result = await api.command({ action: editor.action, ref: editor.node.ref, expectedRevision: editor.node.headRevision, sourceDigest: editor.node.contentDigest, note: editor.note.trim(), ...(editor.action === "snapshot" ? { label: editor.label.trim() } : {}) });
      clearDraft();
      setEditor(undefined); setSaved(true); refresh();
      if (result.node) {
        const next = new URLSearchParams(search), ref = result.node.ref;
        next.set("kind", ref.kind); next.set("objectId", ref.objectId); next.set("versionId", ref.versionId);
        if (ref.shotId) next.set("shotId", ref.shotId); else next.delete("shotId");
        next.delete("baselineVersionId"); next.delete("candidateVersionId"); next.delete("offset");
        navigate(`${location}?${next}`);
      }
    } catch (error) { setWriteError(error); }
    finally { locked.current = false; setBusy(false); }
  }
  async function compare() {
    if (!baseline || !candidate || !canCompare || busy || editor) return;
    comparisonRequest.current?.abort(); const abort = new AbortController(); comparisonRequest.current = abort;
    setComparing(true);
    try { const value = await api.compare(baseline.ref, candidate.ref, abort.signal); if (!abort.signal.aborted) setComparison({ key: pairKey, value }); }
    catch (error) { if (!abort.signal.aborted) setComparison({ key: pairKey, error }); }
    finally { if (!abort.signal.aborted) setComparing(false); }
  }
  async function report() {
    if (!baseline || !candidate || !comparison || locked.current || editor) return;
    locked.current = true; setBusy(true); setWriteError(undefined);
    try { const value = await api.report(baseline.ref, candidate.ref); downloadReport(value, value.reportId); setSaved(true); }
    catch (error) { setWriteError(error); }
    finally { locked.current = false; setBusy(false); }
  }
  function source(target: VersionNode) {
    if (actorId) rememberVersionReturn(actorId, workspaceId, projectId, search);
    const pageTarget = target.ref.kind === "asset" ? `asset-editor/${encodeURIComponent(target.ref.objectId)}` : target.ref.kind === "script" ? "versions" : target.ref.kind === "final" ? "final-version-compare" : target.ref.kind === "storyboard" ? "storyboard" : "shots";
    go(pageTarget, target.episodeId && /^\d+$/.test(target.episodeId) ? Number(target.episodeId) : null);
  }
  const sectionError = pageState?.key === filterKey ? pageState.error : undefined;
  return <main className="xj-versions-page">
    <header className="xj-versions-heading"><div><p className="text-xs text-violet-300">{t("projectHistory")}</p><h1 className="xj-versions-title">{t("title")}</h1><p className="xj-versions-subtitle">{t("subtitle")}</p></div><button type="button" className={versionButton} disabled={waiting || comparing} onClick={refresh}><RefreshCw size={15} />{t("refresh")}</button></header>
    <div className="xj-versions-metrics">{[["totalVersions", page?.summary.totalVersions, "authorizedScope"], ["todayAdded", page?.summary.todayAdded, "utcToday"], ["restorableVersions", page?.summary.restorable, "restoreMetricHint"], ["conflictVersions", page?.summary.conflicts, "conflictUnknown"]].map(([label, value, hint]) => <section className="xj-versions-metric" key={String(label)}><p className="xj-versions-metric-label">{t(String(label))}</p><strong className="xj-versions-metric-value">{typeof value === "number" ? value : "—"}</strong><p className="xj-versions-metric-hint">{t(String(hint))}</p></section>)}</div>
    {page?.summary.unknownTime ? <p className="mt-3 text-xs leading-6 text-slate-500">{t("missingTimestamps", { count: page.summary.unknownTime })}</p> : null}
    {selectionInvalid ? <div role="alert" className="my-4 rounded-xl border border-amber-300/20 p-4 text-sm text-amber-200"><p>{t("invalidSelection")}</p><button type="button" className={versionButton + " mt-3"} disabled={waiting} onClick={() => filter({ kind: "" })}>{t("clearSelection")}</button></div> : null}
    {savedDraft && !editor ? <section className="my-4 rounded-xl border border-violet-300/20 bg-violet-400/[.04] p-4 text-sm"><p>{t("savedDraftAvailable")}</p><p className="mt-2 break-all text-xs text-slate-500">{savedDraft.ref.objectId} · {savedDraft.ref.versionId}</p><div className="mt-3 flex gap-2"><button type="button" className={versionButton} disabled={waiting || stale} onClick={resumeDraft}>{t(node && versionRefKey(node.ref) === versionRefKey(savedDraft.ref) ? "resumeDraft" : "openDraftVersion")}</button><button type="button" className={versionButton} disabled={waiting} onClick={clearDraft}>{t("discardDraft")}</button></div></section> : null}
    {writeError ? <div className="my-4"><VersionError error={writeError} retry={!editor ? () => { setWriteError(undefined); refresh(); } : undefined} />{conflict && editor ? <button type="button" className={versionButton + " mt-3"} disabled={busy} onClick={() => { clearDraft(); setEditor(undefined); setWriteError(undefined); refresh(); }}>{t("discardReload")}</button> : null}</div> : saved ? <p role="status" className="my-4 text-sm text-emerald-300">{t("saved")}</p> : null}
    {editor ? <form className={versionPanel + " my-5 border-violet-300/30"} onSubmit={(event) => void submit(event)}>
      <h2 className="xj-versions-section-title">{t(editor.action === "snapshot" ? "createSnapshot" : editor.action === "lock" ? "freeze" : editor.node.ref.kind === "final" ? "selectFinal" : "rollback")}</h2>
      <p className="mt-3 text-sm text-slate-300">{editor.node.label} · {t("versionNumber", { number: editor.node.versionNumber })}</p>
      <p className="my-3 break-all font-mono text-[11px] text-slate-500">{editor.node.ref.objectId} · {editor.node.ref.versionId}</p>
      <p className="mb-4 text-xs leading-6 text-slate-400">{t(editor.action === "snapshot" ? "snapshotHelp" : editor.action === "lock" ? "freezeHelp" : editor.node.ref.kind === "final" ? "selectFinalHelp" : "restoreHelp")}</p>
      <fieldset disabled={busy || conflict} className="grid gap-4 border-0 p-0">
        {editor.action === "snapshot" ? <label className="xj-versions-label">{t("snapshotName")}<input className={versionField} required maxLength={160} value={editor.label} onChange={(event) => edit({ ...editor, label: event.target.value })} /></label> : null}
        <label className="xj-versions-label">{t("note")}<textarea className={versionField + " min-h-24"} required maxLength={1000} value={editor.note} onChange={(event) => edit({ ...editor, note: event.target.value })} /></label>
      </fieldset>
      {invalid ? <p role="alert" className="mt-3 text-xs text-amber-200">{t("requiredNotes")}</p> : null}
      <div className="mt-4 flex gap-2"><button type="submit" className={versionButton + " xj-versions-button--primary"} disabled={busy || conflict || stale}>{t(busy ? "saving" : "confirm")}</button><button type="button" className={versionButton} disabled={busy} onClick={() => { clearDraft(); setEditor(undefined); setWriteError(undefined); }}>{t("cancel")}</button></div>
    </form> : null}
    <div className="xj-versions-layout"><div className="xj-versions-main">
      <section className={versionPanel} aria-busy={reading}>
        <div className="xj-versions-toolbar"><h2 className="xj-versions-section-title">{t("history")}</h2><button type="button" className={versionButton} disabled={waiting || stale || Boolean(savedDraft) || !node?.actions.canSnapshot} onClick={() => node && begin("snapshot", node)}><Camera size={15} />{t("createSnapshot")}</button></div>
        <form className="xj-versions-filters" onSubmit={searchVersions}>
          <input key={params.get("q")} name="q" defaultValue={params.get("q") ?? ""} aria-label={t("search")} placeholder={t("search")} maxLength={255} className={versionField + " min-w-0 flex-1"} disabled={waiting} />
          <button className={versionButton} disabled={waiting}>{t("searchAction")}</button>
          <select aria-label={t("domain")} className={versionField} value={params.get("kind") ?? ""} disabled={waiting} onChange={(event) => filter({ kind: event.target.value })}><option value="">{t("all")}</option>{(page?.kinds ?? kinds).map((kind) => <option key={kind} value={kind}>{t(`kind.${kind}`)}</option>)}</select>
          <select aria-label={t("state")} className={versionField} value={params.get("state") ?? ""} disabled={waiting} onChange={(event) => filter({ state: event.target.value })}><option value="">{t("all")}</option><option value="current">{t("current")}</option><option value="locked">{t("frozen")}</option><option value="restorable">{t("restorableVersions")}</option></select>
        </form>
        {params.get("objectId") ? <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-white/[.03] p-3 text-xs"><span className="max-w-full break-all text-slate-400">{t("object")}: {params.get("shotId") ?? params.get("objectId")}</span><button type="button" className="text-violet-200 disabled:opacity-40" disabled={waiting} onClick={() => filter({})}>{t("allObjects")}</button></div> : null}
        {sectionError ? <VersionError error={sectionError} retry={editor ? undefined : refresh} /> : !page ? <p role="status" className="py-10 text-sm text-slate-400">{t("loading")}</p> : null}
        <ol className="xj-versions-history">{page?.items.map((item) => <li key={versionRefKey(item.ref)} className="xj-versions-history-card" aria-current={versionRefKey(item.ref) === selectedKey ? "true" : undefined}>
          <div className="xj-versions-toolbar"><div className="min-w-0 flex-1"><h3 className="xj-versions-history-title">{item.label} · {t("versionNumber", { number: item.versionNumber })}</h3><p className="xj-versions-history-summary">{item.summary || t("noSummary")}</p></div><span className="xj-versions-tag">{t(`kind.${item.ref.kind}`)}</span></div>
          <div className="my-3 flex flex-wrap gap-2 text-[11px] text-slate-500"><span>{date(item.createdAt)}</span>{item.actorId ? <span>· {item.actorId}</span> : null}{item.current ? <span className="text-emerald-300">{t("current")}</span> : null}{item.locked ? <span className="text-amber-200">{t("frozen")}</span> : item.immutable ? <span>{t("immutable")}</span> : null}</div>
          <p className="mb-4 break-all font-mono text-[10px] text-slate-500">{item.ref.shotId ?? item.ref.objectId} · {item.ref.versionId}</p>
          <div className="flex flex-wrap gap-2"><button type="button" className={versionButton} disabled={waiting || comparing} onClick={() => choose(item)}><GitCompareArrows size={14} />{t("inspectCompare")}</button><button type="button" className={versionButton} disabled={waiting || comparing || stale || selectionInvalid || Boolean(savedDraft) || !item.actions.canRestore} onClick={() => begin("restore", item)}><RotateCcw size={14} />{t(item.ref.kind === "final" ? "selectFinal" : "rollback")}</button>{item.actions.canLock ? <button type="button" className={versionButton} disabled={waiting || comparing || stale || selectionInvalid || Boolean(savedDraft)} onClick={() => begin("lock", item)}><LockKeyhole size={13} />{t("freeze")}</button> : null}</div>
        </li>)}</ol>
        {page && !page.items.length ? <div className="py-10 text-sm text-slate-400"><p>{t("empty")}</p><p className="mt-2 text-xs leading-6 text-slate-500">{t("emptyHint")}</p></div> : null}
        {page ? <div className="mt-5 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500"><span>{t("page", { total: page.total, page: Math.floor(page.offset / page.limit) + 1 })}</span><div className="flex gap-2"><button className={versionButton} disabled={waiting || page.offset === 0} onClick={() => query({ offset: String(Math.max(0, page.offset - page.limit)) })}>{t("previous")}</button><button className={versionButton} disabled={waiting || page.offset + page.limit >= page.total} onClick={() => query({ offset: String(page.offset + page.limit) })}>{t("next")}</button></div></div> : null}
      </section>
      {detailState?.key === selectedKey && detailState.error ? <VersionError error={detailState.error} retry={editor ? undefined : refresh} /> : null}
      {node ? <section className={versionPanel}><div className="xj-versions-toolbar"><h2 className="xj-versions-section-title">{t("source")}</h2><button className={versionButton} disabled={waiting} onClick={() => source(node)}>{t("openProduction")}<ChevronRight size={14} /></button></div><dl className="mt-5 grid gap-4 text-xs">{[["object", node.ref.shotId ?? node.ref.objectId], ["versionId", node.ref.versionId], ["sourceModule", t(`kind.${node.ref.kind}`)], ["sourceId", node.source.sourceId], ["parentVersion", node.source.parentVersionId ?? "—"], ["sourceDigest", node.contentDigest], ["headRevision", String(node.headRevision)]].map(([label, value]) => <div key={label} className="flex flex-wrap justify-between gap-3 border-b border-white/[.06] pb-3"><dt className="text-slate-500">{t(label)}</dt><dd className="max-w-full break-all text-slate-300">{value}</dd></div>)}</dl>{node.actions.reason ? <p className="mt-4 text-xs leading-6 text-amber-200">{t(`reason.${node.actions.reason}`, { defaultValue: t("capabilityHint") })}</p> : null}</section> : null}
      <section className={versionPanel}><div className="xj-versions-toolbar"><h2 className="xj-versions-section-title">{t("checkpoints")}</h2><FileClock size={17} aria-hidden="true" /></div><p className="mt-3 text-xs leading-6 text-slate-500">{t("checkpointHint")}</p>
        {checkpointState?.key === checkpointKey && checkpointState.error ? <VersionError error={checkpointState.error} retry={editor ? undefined : refresh} /> : !checkpoints ? <p className="py-5 text-sm text-slate-400">{t("loading")}</p> : <><ul className="mt-4 space-y-4">{checkpoints.items.map((item) => <li key={item.id} className="border-l-2 border-violet-400/30 pl-3 text-xs"><strong className="break-words text-slate-300">{item.label}</strong><p className="my-2 break-words leading-6 text-slate-400">{item.note}</p><p className="text-slate-500">{date(item.createdAt)} · {item.createdBy}</p><p className="mt-2 break-all font-mono text-[10px] text-slate-500">{t(`kind.${item.ref.kind}`)} · {item.ref.versionId} · {item.sourceDigest}</p></li>)}</ul>{!checkpoints.items.length ? <p className="py-5 text-xs text-slate-500">{t("emptyCheckpoints")}</p> : null}<div className="mt-4 flex justify-end gap-2"><button className={versionButton} disabled={waiting || checkpoints.offset === 0} onClick={() => query({ checkpointOffset: String(Math.max(0, checkpoints.offset - checkpoints.limit)) })}>{t("previous")}</button><button className={versionButton} disabled={waiting || checkpoints.offset + checkpoints.limit >= checkpoints.total} onClick={() => query({ checkpointOffset: String(checkpoints.offset + checkpoints.limit) })}>{t("next")}</button></div></>}
      </section>
    </div><ComparisonPanel selectors={<div className="grid gap-4">{(["baseline", "candidate"] as const).map((side) => <label className="xj-versions-label" key={side}>{t(side)}<select className={versionField} disabled={waiting || comparing || !versions.length} value={(side === "baseline" ? baselineId ?? baseline?.ref.versionId : candidateId ?? candidate?.ref.versionId) ?? ""} onChange={(event) => query({ [side === "baseline" ? "baselineVersionId" : "candidateVersionId"]: event.target.value })}><option value="">{t(side === "baseline" ? "chooseBaseline" : "chooseCandidate")}</option>{(side === "baseline" ? baselineId && !baseline : candidateId && !candidate) ? <option value={(side === "baseline" ? baselineId : candidateId) ?? ""} disabled>{t("unknownVersion")} · {side === "baseline" ? baselineId : candidateId}</option> : null}{versions.map((item) => <option key={versionRefKey(item.ref)} value={item.ref.versionId}>{t("versionNumber", { number: item.versionNumber })} · {item.ref.versionId}</option>)}</select></label>)}</div>} baseline={baseline ? { ...baseline, versionId: baseline.ref.versionId } : undefined} candidate={candidate ? { ...candidate, versionId: candidate.ref.versionId } : undefined} changes={comparison?.changes} busy={comparing || busy} error={comparisonError ? <VersionError error={comparisonError} /> : undefined} onCompare={() => void compare()} onDownloadReport={() => void report()} canCompare={canCompare && !waiting && !stale} canDownload={Boolean(comparison && page?.permissions.canReport && !waiting && !stale)} />
    </div>
  </main>;
}
