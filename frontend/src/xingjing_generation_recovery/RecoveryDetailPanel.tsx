import { useEffect, useRef, useState } from "react";
import { RecoveryApiError, type Receipt, type RecoveryApi, type RecoveryDetail, type RecoveryPreview } from "./api";
import { button, card, date, field, money, useRecoveryTranslation } from "./i18n";
import { type PendingResolution } from "./pending";

export function errorCode(error: unknown): string {
  if (error instanceof RecoveryApiError) return error.code;
  if (error instanceof Error && "code" in error && typeof error.code === "string" && error.code.startsWith("AUTH_")) return "RECOVERY_PERMISSION_DENIED";
  return "network";
}
export function denied(error: unknown): boolean {
  return error instanceof RecoveryApiError && [401, 403].includes(error.status) || error instanceof Error && "code" in error && typeof error.code === "string" && error.code.startsWith("AUTH_");
}

function OutputPreview({ api, detail, receipt, index, onDenied }: { api: RecoveryApi; detail: RecoveryDetail; receipt: Receipt; index: number; onDenied: (error: unknown) => void }) {
  const { t } = useRecoveryTranslation();
  const [requested, setRequested] = useState(false);
  const [url, setUrl] = useState<string>();
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    if (!requested) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    api.artifact(detail.task.projectId, detail.task.id, receipt.id, index, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch(error => { if (!controller.signal.aborted) { setError(error); if (denied(error)) onDenied(error); } });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [api, requested, detail.task.projectId, detail.task.id, receipt.id, index, onDenied]);
  const asset = receipt.artifacts[index];
  return <div className="mt-3 rounded-xl border border-white/10 p-3">
    <p className="break-all text-xs text-slate-400">{t(asset.media_type)} · {(asset.size_bytes / 1024).toFixed(1)} KB</p>
    <p className="mt-2 break-all font-mono text-[11px] text-slate-500">SHA-256 {asset.content_sha256}</p>
    {!requested ? <button className={button + " mt-3"} onClick={() => setRequested(true)}>{t("mediaPreview")}</button>
      : error ? <p role="alert" className="mt-3 text-sm text-amber-200">{t(errorCode(error), { defaultValue: t("network") })}</p>
      : !url ? <p role="status" className="mt-3 text-sm text-slate-400">{t("mediaLoading")}</p>
      : asset.media_type === "image" ? <img src={url} alt={t("mediaAlt")} className="mt-3 max-h-80 w-full rounded-lg object-contain" />
      : <video src={url} aria-label={t("mediaAlt")} className="mt-3 max-h-80 w-full rounded-lg" controls preload="metadata"><track kind="captions" /></video>}
  </div>;
}

export function RecoveryDetailPanel({ detail, api, pending, busy, unavailable, onResolve, onDenied }: {
  detail: RecoveryDetail; api: RecoveryApi; pending?: PendingResolution; busy: boolean; unavailable: boolean;
  onResolve: (operation: PendingResolution) => Promise<void>; onDenied: (error: unknown) => void;
}) {
  const { t, i18n } = useRecoveryTranslation();
  const [receiptId, setReceiptId] = useState("");
  const [preview, setPreview] = useState<RecoveryPreview>();
  const [reason, setReason] = useState(pending?.payload.reason ?? "");
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<unknown>();
  const [clock, setClock] = useState(Date.now);
  const flight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (!preview) return; const timer = window.setInterval(() => setClock(Date.now()), 1000); return () => window.clearInterval(timer); }, [preview]);
  const selected = detail.receipts.find(item => item.id === receiptId) ?? (detail.receipts.length === 1 ? detail.receipts[0] : undefined);
  const validPreview = preview && preview.receiptId === selected?.id && preview.taskVersion === detail.task.version && Date.parse(preview.expiresAt) > clock && !detail.blockedCode;
  const canPreview = detail.canManage && !detail.blockedCode && !!selected && !pending && !busy && !previewing && !unavailable;
  async function loadPreview() {
    if (!canPreview || !selected || flight.current) return;
    flight.current = true; setPreviewing(true); setError(undefined); setPreview(undefined);
    try {
      const value = await api.preview(detail.task.projectId, detail.task.id, selected.id, detail.task.version);
      if (mounted.current) { setPreview(value); setClock(Date.now()); }
    } catch (error) { if (mounted.current) { setError(error); if (denied(error)) onDenied(error); } }
    finally { flight.current = false; if (mounted.current) setPreviewing(false); }
  }
  const format = (value: number, currency: string) => money(value, currency, i18n.language);
  return <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.85fr)]">
    <div className="min-w-0 space-y-5">
      <section className={card}>
        <p className="text-xs text-violet-300">{detail.task.projectName}</p><h2 className="mt-2 break-all text-xl font-semibold">{t("task")} · {detail.task.id}</h2>
        <div className="mt-4 flex flex-wrap gap-2 text-xs"><span className="rounded-full bg-white/5 px-3 py-1.5">{t(detail.task.status, { defaultValue: detail.task.status })}</span><span className="rounded-full bg-white/5 px-3 py-1.5">{t("version")} {detail.task.version}</span><span className="rounded-full bg-white/5 px-3 py-1.5">{t("attempt")} {detail.task.attempt}</span></div>
        <dl className="mt-5 grid gap-4 text-xs sm:grid-cols-2">{[
          ["provider", `${detail.task.providerId ?? "—"} / ${detail.task.modelId ?? "—"}`], ["execution", detail.execution.id],
          ["created", date(detail.task.createdAt, i18n.language)], ["submitted", date(detail.execution.submittedAt, i18n.language)],
          ["requestDigest", detail.task.requestFingerprint], ["upstream", detail.execution.upstreamRequestId],
        ].map(([key, value]) => <div className="min-w-0" key={key}><dt className="text-slate-500">{t(key!)}</dt><dd className="mt-1.5 break-all text-slate-300">{value ?? "—"}</dd></div>)}</dl>
        <h3 className="mt-6 border-t border-white/10 pt-5 font-medium">{t("original")}</h3><p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7 text-slate-300">{detail.task.prompt}</p>
      </section>
      <section className={card}><h3 className="font-semibold">{t("inputs")}</h3>
        {detail.task.inputs.length ? <ul className="mt-3 space-y-3">{detail.task.inputs.map((item, index) => <li className="rounded-xl border border-white/10 p-3 text-xs" key={`${item.assetId}:${index}`}><p className="break-all text-slate-300">{t("asset")}: {item.assetId}</p><p className="mt-2 break-all text-slate-400">{t("inputVersion")}: {item.versionId ?? "—"}</p><p className="mt-2 break-all font-mono text-slate-500">SHA-256 {item.sha256}</p></li>)}</ul> : <p className="mt-3 text-sm text-slate-400">{t("noInputs")}</p>}
      </section>
      <section className={card}><h3 className="font-semibold">{t("billing")}</h3>{detail.billing ? <><dl className="mt-4 grid grid-cols-2 gap-4">{[["quoted", detail.billing.estimatedMinor], ["held", detail.billing.heldMinor], ["charged", detail.billing.chargedMinor], ["released", detail.billing.releasedMinor]].map(([key, value]) => <div key={key}><dt className="text-xs text-slate-400">{t(String(key))}</dt><dd className="mt-2 font-mono text-sm">{format(Number(value), detail.billing!.currency)}</dd></div>)}</dl><p className="mt-5 break-all text-xs text-slate-500">{t("price")} · {detail.billing.pricingVersion}</p></> : <p className="mt-3 text-sm text-amber-200">{t("noBilling")}</p>}</section>
      <section className={card}><h3 className="font-semibold">{t("history")}</h3>{detail.history.length ? <ol className="mt-4 space-y-4">{detail.history.map(item => <li className="rounded-xl border border-white/10 p-4 text-sm" key={item.id}><p className="text-violet-200">{t(item.result.status)} · {date(item.createdAt, i18n.language)}</p><p className="mt-2 break-all text-xs text-slate-500">{t("actor")}: {item.actorId}</p><p className="mt-3 whitespace-pre-wrap break-words text-slate-300">{item.reason}</p><p className="mt-3 text-xs text-slate-400">{t("charged")} {format(item.result.chargedMinor, item.result.currency)} · {t("released")} {format(item.result.releasedMinor, item.result.currency)}</p></li>)}</ol> : <p className="mt-3 text-sm text-slate-400">{t("noHistory")}</p>}</section>
    </div>
    <div className="min-w-0 space-y-5">
      <section className={card}><h3 className="font-semibold">{t("receipts")}</h3><p className="mt-2 text-xs leading-6 text-slate-400">{t("sourceHelp")}</p>
        {detail.blockedCode && <p role="status" className="mt-4 rounded-xl border border-amber-300/20 bg-amber-300/5 p-4 text-sm leading-6 text-amber-200">{t(detail.blockedCode, { defaultValue: detail.blockedCode })}</p>}
        {!!detail.receipts.length && <label className="mt-5 block text-sm text-slate-300">{t("select")}<select className={field + " mt-2"} value={selected?.id ?? ""} disabled={busy || !!pending || previewing} onChange={event => { setReceiptId(event.target.value); setPreview(undefined); setError(undefined); }}><option value="" disabled>{t("select")}</option>{detail.receipts.map(item => <option key={item.id} value={item.id}>{t(item.status)} · {date(item.completedAt, i18n.language)} · {item.id}</option>)}</select></label>}
        {selected && <div className="mt-5"><p className="text-sm text-violet-200">{t(selected.evidenceKind === "provider_not_accepted" ? "notAccepted" : "terminal")}</p><p className="mt-2 text-xs text-slate-500">{t("source")}</p><dl className="mt-4 space-y-3 text-xs">{[["receipt", selected.id], ["upstream", selected.upstreamRequestId], ["receiptRef", selected.providerReceiptRef], ["completed", date(selected.completedAt, i18n.language)], ["verified", date(selected.verifiedAt, i18n.language)], ["charged", format(selected.actualMinor, selected.currency)]].map(([key, value]) => <div key={key}><dt className="text-slate-500">{t(key)}</dt><dd className="mt-1 break-all text-slate-300">{value}</dd></div>)}</dl>{selected.artifacts.map((item, index) => <OutputPreview key={item.asset_id} api={api} detail={detail} receipt={selected} index={index} onDenied={onDenied} />)}</div>}
        {!detail.canManage && <p className="mt-5 text-sm text-amber-200">{t("readOnly")}</p>}
        {detail.canManage && <button className={button + " mt-5 w-full"} disabled={!canPreview} onClick={() => void loadPreview()}>{t(previewing ? "working" : "preview")}</button>}
        {error !== undefined && <p role="alert" className="mt-4 text-sm text-amber-200">{t(errorCode(error), { defaultValue: t("network") })}</p>}
      </section>
      {preview && <section className={card + " border-violet-300/30"}><h3 className="text-lg font-semibold">{t("previewTitle")}</h3><p className="mt-3 text-violet-200">{t(preview.action)}</p><p className="mt-2 text-xs leading-6 text-slate-400">{t("previewHelp")}</p><dl className="mt-5 space-y-3">{[["held", preview.heldMinor], ["charged", preview.chargedMinor], ["released", preview.releasedMinor]].map(([key, value]) => <div className="flex justify-between gap-3 text-sm" key={key}><dt className="text-slate-400">{t(String(key))}</dt><dd className="font-mono">{format(Number(value), preview.currency)}</dd></div>)}</dl><p className="mt-4 text-xs text-slate-400">{t("outputCount", { count: preview.artifactCount })}</p><p className="mt-2 text-xs text-slate-500">{t("expires", { time: date(preview.expiresAt, i18n.language) })}</p>
        {!validPreview && <p role="status" className="mt-3 text-sm text-amber-200">{t(preview.taskVersion !== detail.task.version ? "RECOVERY_VERSION_CHANGED" : "RECOVERY_PREVIEW_EXPIRED")}</p>}
        <label className="mt-5 block text-sm">{t("reason")}<textarea className={field + " mt-2 min-h-28"} maxLength={1000} value={reason} disabled={busy || !!pending} onChange={event => setReason(event.target.value)} aria-describedby="recovery-reason-help" /></label><p id="recovery-reason-help" className="mt-2 text-xs leading-5 text-slate-500">{t("reasonHelp")}</p>
        <button className={button + " mt-5 w-full border-violet-300/40 bg-violet-500/15 text-violet-100"} disabled={!validPreview || !detail.canManage || reason.trim().length < 5 || busy || !!pending || unavailable} onClick={() => { if (validPreview && reason.trim().length >= 5) void onResolve({ projectId: detail.task.projectId, taskId: detail.task.id, key: crypto.randomUUID(), payload: { previewId: preview.previewId, reason: reason.trim() } }); }}>{t(busy ? "working" : "confirm")}</button>
      </section>}
    </div>
  </div>;
}
