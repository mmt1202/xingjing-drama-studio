import { ArrowLeft, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { RecoveryApiError, type RecoveryApi, type RecoveryDetail, type RecoveryList } from "./api";
import { RecoveryDetailPanel, denied, errorCode } from "./RecoveryDetailPanel";
import { button, card, date, field, money, useRecoveryTranslation } from "./i18n";
import { pendingKey, readPending, savePending, type PendingResolution } from "./pending";

export function GenerationRecoveryPage({ workspaceId, api }: { workspaceId: string; api: RecoveryApi }) {
  const { t, i18n } = useRecoveryTranslation();
  const [location, navigate] = useLocation();
  const search = useSearch();
  const params = new URLSearchParams(search);
  const state = ["attention", "handled", "all"].includes(params.get("state") ?? "") ? params.get("state")! : "attention";
  const query = params.get("q") ?? "";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const projectId = params.get("projectId") ?? "", taskId = params.get("taskId") ?? "";
  const [listState, setListState] = useState<{ key: string; data?: RecoveryList; error?: unknown }>();
  const [detailState, setDetailState] = useState<{ key: string; data?: RecoveryDetail; error?: unknown }>();
  const [pending, setPending] = useState<PendingResolution>();
  const [reload, setReload] = useState(0);
  const [actionError, setActionError] = useState<unknown>();
  const [accessError, setAccessError] = useState<unknown>();
  const [busy, setBusy] = useState(false), [success, setSuccess] = useState(false);
  const flight = useRef(false), mounted = useRef(true), accessEpoch = useRef(0);
  const listKey = JSON.stringify([state, query, offset, reload]);
  const detailKey = JSON.stringify([projectId, taskId, reload]);
  const loading = listState?.key !== listKey, detailLoading = detailState?.key !== detailKey;
  const data = listState?.data, detail = detailState?.data;
  const error = !loading ? listState?.error : undefined, detailError = !detailLoading ? detailState?.error : undefined;
  const storage = data ? pendingKey(workspaceId, data.context.actorId) : undefined;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const deny = useCallback((error: unknown) => { accessEpoch.current += 1; setAccessError(error); setListState(undefined); setDetailState(undefined); }, [setAccessError, setListState, setDetailState]);
  useEffect(() => {
    const controller = new AbortController(), epoch = accessEpoch.current;
    api.list(new URLSearchParams({ state, q: query, offset: String(offset), limit: "20" }).toString(), controller.signal).then(value => {
      if (!controller.signal.aborted && epoch === accessEpoch.current) { setListState({ key: listKey, data: value }); setAccessError(undefined); setPending(readPending(pendingKey(workspaceId, value.context.actorId))); }
    }).catch(error => { if (!controller.signal.aborted && epoch === accessEpoch.current) { if (denied(error)) deny(error); else setListState(previous => ({ key: listKey, data: previous?.data, error })); } });
    return () => controller.abort();
  }, [api, workspaceId, state, query, offset, listKey, deny]);
  useEffect(() => {
    if (!projectId || !taskId) return;
    const controller = new AbortController(), epoch = accessEpoch.current;
    api.detail(projectId, taskId, controller.signal).then(value => { if (!controller.signal.aborted && epoch === accessEpoch.current) setDetailState({ key: detailKey, data: value }); })
      .catch(error => { if (!controller.signal.aborted && epoch === accessEpoch.current) { if (denied(error)) deny(error); else setDetailState(previous => ({ key: detailKey, data: previous?.data, error })); } });
    return () => controller.abort();
  }, [api, projectId, taskId, detailKey, deny]);
  const change = (values: Record<string, string>) => {
    const next = new URLSearchParams(search); for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    navigate(`${location}${next.size ? `?${next}` : ""}`); setSuccess(false); setActionError(undefined);
  };
  async function resolve(operation: PendingResolution) {
    if (flight.current || !storage || !data?.context.canManage) return;
    // Keep the original request through ambiguous responses and refreshes.
    const original = readPending(storage);
    if (original && JSON.stringify(original) !== JSON.stringify(operation)) { setPending(original); return; }
    if (!savePending(storage, operation)) { setActionError(new RecoveryApiError(0, "storage", "")); return; }
    flight.current = true; setPending(operation); setBusy(true); setActionError(undefined); setSuccess(false);
    try {
      await api.resolve(operation.projectId, operation.taskId, operation.payload, operation.key);
      savePending(storage);
      if (mounted.current) { setPending(undefined); setSuccess(true); setReload(value => value + 1); }
    } catch (error) {
      if (error instanceof RecoveryApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) savePending(storage);
      if (mounted.current) {
        setPending(readPending(storage)); setActionError(error);
        if (denied(error)) deny(error);
        else if (error instanceof RecoveryApiError && error.status === 409) setReload(value => value + 1);
      }
    } finally { flight.current = false; if (mounted.current) setBusy(false); }
  }
  const currentDetail = detail?.task.projectId === projectId && detail.task.id === taskId ? detail : undefined;
  const problem = accessError ?? (!data ? error : undefined);
  return <div className="min-h-screen bg-[#080a10] text-slate-100">
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 px-5 py-5 lg:px-8"><div className="flex items-center gap-3"><ShieldCheck className="text-violet-300" size={25} /><div><h1 className="text-xl font-semibold">{t("title")}</h1><p className="mt-1 text-xs text-slate-400">{t("subtitle")}</p></div></div><div className="flex gap-2"><Link className={button} href={`/app/workspaces/${encodeURIComponent(workspaceId)}/home`}>{t("home")}</Link><button className={button} disabled={busy || loading && accessError === undefined} onClick={() => setReload(value => value + 1)}><RefreshCw size={15} />{t("refresh")}</button></div></header>
    <main className="mx-auto max-w-[1600px] space-y-5 p-5 lg:p-8">
      <p className="break-all text-xs text-slate-500">{t("workspace")} · {workspaceId}</p>
      {problem !== undefined ? <div role="alert" className={card + " text-amber-200"}>{t(errorCode(problem), { defaultValue: t("network") })}</div> : !data ? <p role="status" className="py-12 text-slate-400">{t("loading")}</p> : <>
        {!data.receiptChannelConfigured && <p role="status" className="rounded-xl border border-amber-300/20 bg-amber-300/5 p-4 text-sm text-amber-200">{t("channelMissing")}</p>}
        {pending && <div role="status" className="rounded-xl border border-amber-300/30 bg-amber-300/5 p-4 text-amber-100"><p className="text-sm leading-6">{t("pending")}</p><p className="mt-2 break-all font-mono text-xs text-amber-200/70">{t("task")} · {pending.taskId}</p><div className="mt-3 flex flex-wrap gap-3">{pending.projectId !== projectId || pending.taskId !== taskId ? <button className={button} disabled={busy} onClick={() => change({ projectId: pending.projectId, taskId: pending.taskId })}>{t("resume")}</button> : null}<button className={button} disabled={busy || !data.context.canManage} onClick={() => void resolve(pending)}>{t(busy ? "working" : "retry")}</button></div></div>}
        {actionError !== undefined && <p role="alert" className="rounded-xl border border-amber-300/20 p-4 text-sm text-amber-200">{t(errorCode(actionError), { defaultValue: t("network") })}</p>}
        {success && <p role="status" className="rounded-xl border border-emerald-300/20 p-4 text-sm text-emerald-200">{t("done")}</p>}
        {projectId && taskId ? <><button className={button} disabled={busy} onClick={() => change({ projectId: "", taskId: "" })}><ArrowLeft size={15} />{t("close")}</button>{detailLoading && <p role="status" className="text-sm text-slate-400">{t("loading")}</p>}{detailError !== undefined && <p role="alert" className={card + " text-amber-200"}>{t(errorCode(detailError), { defaultValue: t("network") })}</p>}{currentDetail && <RecoveryDetailPanel key={`${projectId}:${taskId}`} detail={currentDetail} api={api} pending={pending} busy={busy} unavailable={detailLoading || detailError !== undefined} onResolve={resolve} onDenied={deny} />}</> : <>
          <nav className="flex flex-wrap gap-2" aria-label={t("status")}>{["attention", "handled", "all"].map(value => <button key={value} aria-pressed={state === value} className={button + (state === value ? " border-violet-300/40 bg-violet-500/15 text-violet-100" : "")} disabled={busy} onClick={() => change({ state: value, offset: "0" })}>{t(value)}</button>)}</nav>
          <form className="flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); const value = new FormData(event.currentTarget).get("q"); change({ q: typeof value === "string" ? value.trim() : "", offset: "0" }); }}><input key={query} name="q" defaultValue={query} placeholder={t("search")} aria-label={t("search")} maxLength={128} className={field + " sm:max-w-lg"} disabled={busy} /><button className={button} disabled={busy}>{t("searchAction")}</button></form>
          {error !== undefined && <p role="alert" className="text-sm text-amber-200">{t(errorCode(error), { defaultValue: t("network") })}</p>}
          {loading ? <p role="status" className="py-8 text-slate-400">{t("loading")}</p> : error !== undefined ? null : data.items.length ? <div className="grid gap-4 lg:grid-cols-2">{data.items.map(item => <article className={card} key={`${item.projectId}:${item.id}`}><div className="flex flex-wrap items-start justify-between gap-3"><h2 className="text-lg font-semibold">{item.projectName}</h2><span className={`rounded-full border px-3 py-1 text-xs ${item.attention ? "border-amber-300/20 text-amber-200" : "border-white/10 text-slate-400"}`}>{t(item.conflict ? "conflictLabel" : item.attention ? "attentionLabel" : item.status)}</span></div><p className="mt-3 break-all font-mono text-[11px] text-slate-500">{item.id}</p><p className="mt-3 line-clamp-2 min-h-10 text-sm leading-6 text-slate-300">{item.promptExcerpt}</p><dl className="mt-4 grid grid-cols-2 gap-3 border-t border-white/10 pt-4 text-xs"><div><dt className="text-slate-500">{t("held")}</dt><dd className="mt-1 text-slate-300">{item.currency ? money(item.heldMinor, item.currency, i18n.language) : "—"}</dd></div><div><dt className="text-slate-500">{t("receipts")}</dt><dd className="mt-1 text-slate-300">{t("receiptCount", { count: item.receiptCount })}</dd></div></dl><div className="mt-5 flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-slate-500">{date(item.updatedAt, i18n.language)}</p><button className={button} onClick={() => change({ projectId: item.projectId, taskId: item.id })}>{t("open")}</button></div></article>)}</div> : <div className="rounded-2xl border border-dashed border-white/15 p-10 text-center"><h2 className="text-lg text-slate-300">{t("empty")}</h2><p className="mt-3 text-sm text-slate-500">{t("emptyHelp")}</p></div>}
          {error === undefined && <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>{t("count", { total: data.total, page: Math.floor(data.offset / data.limit) + 1 })}</span><div className="flex gap-2"><button className={button} disabled={loading || data.offset === 0} onClick={() => change({ offset: String(Math.max(0, data.offset - data.limit)) })}>{t("previous")}</button><button className={button} disabled={loading || data.offset + data.limit >= data.total} onClick={() => change({ offset: String(data.offset + data.limit) })}>{t("next")}</button></div></div>}
        </>}
      </>}
    </main>
  </div>;
}
