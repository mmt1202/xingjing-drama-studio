import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { RefreshCw, Wallet, CreditCard, ReceiptText } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceNavigationGuard } from "@/xingjing_creator_home/WorkspaceFrame";
import { createPaymentsApi, PaymentApiError } from "./api";
import type { BillingWorkspace, PaymentCatalog, PaymentDetail, PaymentOrder, PaymentPage, PaymentRefund, PaymentsApi, PaymentTab } from "./contracts";
import { readPaymentDraft, savePaymentDraft, type PaymentDraft } from "./draft-state";
import { PaymentForm } from "./PaymentForm";
import { BillingAside, BillingMetrics, BillingTrend, dateLabel, OrderInspector, PaymentError, PaymentHistory, PaymentStatus, RefundInspector } from "./PaymentViews";
import { minorDecimal, money, parseMinor } from "./money";
import { paymentButton, paymentField, paymentPanel, usePaymentTranslation } from "./i18n";
import "./payments.css";

type ReadState<T> = { key: string; revision: number; value?: T; error?: unknown };
type ReadValues = { catalog: PaymentCatalog; billing: BillingWorkspace; orders: PaymentPage<PaymentOrder>; refunds: PaymentPage<PaymentRefund>; detail: PaymentDetail };
function usePaymentRead<K extends keyof ReadValues>(resource: K, api: PaymentsApi, key: string, revision: number, enabled = true) {
  const [state, setState] = useState<ReadState<ReadValues[K]>>();
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    const filters = new URLSearchParams(key);
    const pageQuery = { q: filters.get("q") ?? "", status: filters.get("status") ?? "", offset: Number(filters.get("offset") ?? 0), limit: 20 };
    const request = resource === "catalog" ? api.catalog(abort.signal) : resource === "detail" ? api.detail(key, abort.signal) : resource === "orders" ? api.orders(pageQuery, abort.signal) : resource === "refunds" ? api.refunds(pageQuery, abort.signal) : api.billing({ ...Object.fromEntries(filters), pageSize: 25 }, abort.signal);
    void (request as Promise<ReadValues[K]>).then((value) => { if (!abort.signal.aborted) setState({ key, revision, value }); }).catch((error: unknown) => {
      if (!abort.signal.aborted) setState((previous) => ({ key, revision, error, value: previous?.key === key && !(error instanceof PaymentApiError && [401, 403, 404].includes(error.status)) ? previous.value : undefined }));
    });
    return () => abort.abort();
  }, [api, key, revision, resource, enabled]);
  const current = state?.key === key ? state : undefined;
  return { value: current?.value, error: current?.error, loading: enabled && (!current || current.revision !== revision), fresh: Boolean(enabled && current?.value && !current.error && current.revision === revision) };
}
const emptyDraft = (): PaymentDraft => ({ idempotencyKey: crypto.randomUUID(), attempted: false, kind: "create", targetId: "", orderId: "", expectedVersion: 0, offerId: "", channel: "", amount: "", currency: "", reason: "", ticketId: "" });
const orderStates = ["creating", "pending", "processing", "unknown", "paid", "closed", "failed", "refunded"];
const refundStates = ["pending_approval", "processing", "unknown", "succeeded", "failed", "rejected"];
const ledgerActions = ["grant", "freeze", "settle", "release", "refund", "compensation", "payment_grant", "refund_reserve", "refund_release", "refund_settle"];
const offsetValue = (value: string | null) => /^\d+$/.test(value ?? "") && Number.isSafeInteger(Number(value)) ? Number(value) : 0;

export function PaymentsWorkspace({ workspaceId, initialTab = "ledger", mode = "creator" }: { workspaceId: string; initialTab?: PaymentTab; mode?: "creator" | "admin" }) {
  const token = useAuthStore((state) => state.token);
  return <PaymentsContent key={JSON.stringify([workspaceId, token, mode, initialTab])} workspaceId={workspaceId} token={token} initialTab={initialTab} mode={mode} />;
}

function PaymentsContent({ workspaceId, token, initialTab, mode }: { workspaceId: string; token: string | null; initialTab: PaymentTab; mode: "creator" | "admin" }) {
  const { t, i18n } = usePaymentTranslation();
  const locale = i18n.resolvedLanguage ?? "zh";
  const api = useMemo(() => createPaymentsApi(workspaceId, token), [workspaceId, token]);
  const [location, navigate] = useLocation();
  const search = useSearch();
  const params = new URLSearchParams(search);
  const tab: PaymentTab = ["ledger", "orders", "refunds"].includes(params.get("tab") ?? "") ? params.get("tab") as PaymentTab : initialTab;
  const [revision, setRevision] = useState(0);
  const [editor, setEditor] = useState<PaymentDraft>();
  const [draftActorId, setDraftActorId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState<unknown>();
  const [saved, setSaved] = useState(false);
  const [, bumpDraft] = useState(0);
  const commandLock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useWorkspaceNavigationGuard(Boolean(editor) || busy);
  const refresh = () => { if (!editor && !commandLock.current) { setWriteError(undefined); setRevision((value) => value + 1); } };
  const catalogRead = usePaymentRead("catalog", api, "catalog", revision);
  const catalog = catalogRead.value;
  const actorId = catalog?.scope.actorId ?? draftActorId;
  const savedDraft = actorId ? readPaymentDraft(actorId, workspaceId) : undefined;
  const ledgerKey = new URLSearchParams({ query: params.get("ledgerQ") ?? "", status: params.get("ledgerStatus") ?? "", source: params.get("ledgerSource") ?? "", projectId: params.get("projectId") ?? "", dateFrom: params.get("dateFrom") ?? "", dateTo: params.get("dateTo") ?? "", pageToken: params.get("pageToken") ?? "", pageSize: "25" }).toString();
  const billingRead = usePaymentRead("billing", api, ledgerKey, revision);
  const billing = billingRead.value;
  const listKey = new URLSearchParams({ q: params.get(tab === "refunds" ? "refundQ" : "orderQ") ?? "", status: params.get(tab === "refunds" ? "refundStatus" : "orderStatus") ?? "", offset: String(offsetValue(params.get(tab === "refunds" ? "refundOffset" : "orderOffset"))), limit: "20", tab }).toString();
  const listRead = usePaymentRead(tab === "refunds" ? "refunds" : "orders", api, listKey, revision, tab !== "ledger" && catalogRead.fresh && Boolean(catalog?.permissions.canView));
  const page = listRead.value;
  const orderId = params.get("orderId") ?? "";
  const refundId = tab === "refunds" ? params.get("refundId") ?? "" : "";
  const detailRead = usePaymentRead("detail", api, orderId, revision, Boolean(orderId) && catalogRead.fresh && Boolean(catalog?.permissions.canView));
  const detail = detailRead.value;
  const selectedRefund = detail?.refunds.find((item) => item.id === refundId);
  const selectionMissing = Boolean(refundId && (!orderId || (detailRead.fresh && !selectedRefund)));
  const readBusy = catalogRead.loading || billingRead.loading || listRead.loading || detailRead.loading;
  const disabled = busy || Boolean(editor);
  const canManage = catalogRead.fresh && Boolean(catalog?.permissions.canManage);
  const detailWritable = catalogRead.fresh && detailRead.fresh && Boolean(detail?.permissions.canManage);
  const editorEnabled = Boolean(editor && (editor.attempted ? canManage : editor.kind === "create" ? canManage && catalog?.configured : detailWritable && detail?.order.id === editor.orderId && (editor.kind === "cancel" ? detail.order.actions.canCancel : editor.kind === "requestRefund" ? detail.order.actions.canRequestRefund : (() => {
    const refund = detail.refunds.find((item) => item.id === editor.targetId);
    return editor.kind === "approve" ? refund?.actions.canApprove : editor.kind === "reject" ? refund?.actions.canReject : refund?.actions.canRefresh;
  })())));

  function updateUrl(values: Record<string, string | null>, force = false) {
    if (!force && (editor || commandLock.current)) return;
    const next = new URLSearchParams(search);
    for (const [key, value] of Object.entries(values)) { if (value === null || value === "") next.delete(key); else next.set(key, value); }
    navigate(`${location}${next.size ? `?${next}` : ""}`);
  }
  function startDraft(value: PaymentDraft) {
    if (!actorId || commandLock.current || editor) return;
    setWriteError(undefined); setSaved(false); setDraftActorId(actorId); setEditor(value); savePaymentDraft(actorId, workspaceId, value);
  }
  function changeDraft(value: PaymentDraft) { setEditor(value); if (actorId) savePaymentDraft(actorId, workspaceId, value); }
  function closeDraft() {
    if (commandLock.current) return;
    if (actorId && !editor?.attempted) savePaymentDraft(actorId, workspaceId);
    setEditor(undefined); setWriteError(undefined); bumpDraft((value) => value + 1);
  }
  function startTopUp(offerId = "") { if (!canManage || !catalog?.configured || savedDraft || !catalog.channels.length) return; startDraft({ ...emptyDraft(), offerId, currency: catalog.offers.find((item) => item.id === offerId)?.currency ?? "" }); }
  function showRefund(refund: PaymentRefund) { updateUrl({ tab: "refunds", refundId: refund.id, orderId: refund.orderId }); }
  function orderAction(kind: "refresh" | "cancel" | "requestRefund", order: PaymentOrder) {
    if (!detailWritable || !detail || disabled) return;
    if (kind === "refresh") { void perform(() => api.orderAction(order.id, { action: "refresh", expectedVersion: order.version })); return; }
    if (savedDraft) return;
    startDraft({ ...emptyDraft(), kind, targetId: order.id, orderId: order.id, expectedVersion: order.version, currency: order.currency, amount: kind === "requestRefund" ? minorDecimal(order.refundableMinor, order.currency) : "" });
  }
  function refundAction(kind: "approve" | "reject" | "refundRefresh", refund: PaymentRefund) {
    if (!detailWritable || disabled || savedDraft) return;
    startDraft({ ...emptyDraft(), kind, targetId: refund.id, orderId: refund.orderId, expectedVersion: refund.version, currency: refund.currency });
  }
  async function perform(command: () => ReturnType<PaymentsApi["create"]>, fromDraft = false) {
    if (commandLock.current) return;
    commandLock.current = true; setBusy(true); setWriteError(undefined); setSaved(false);
    try {
      const result = await command();
      if (!mounted.current) return;
      if (fromDraft && actorId) { savePaymentDraft(actorId, workspaceId); setEditor(undefined); }
      if (result.refund) updateUrl({ tab: "refunds", refundId: result.refund.id, orderId: result.refund.orderId }, true);
      else if (result.order) updateUrl({ tab: "orders", orderId: result.order.id, refundId: null }, true);
      setSaved(true); setRevision((value) => value + 1);
    } catch (error) { if (mounted.current) setWriteError(error); }
    finally { commandLock.current = false; if (mounted.current) setBusy(false); }
  }
  async function submitDraft() {
    if (!editor || !editorEnabled || !actorId) return;
    const draft = { ...editor, attempted: true }, reason = draft.reason.trim();
    changeDraft(draft);
    if (draft.kind === "create") await perform(() => api.create({ offerId: draft.offerId, channel: draft.channel, reason }, draft.idempotencyKey), true);
    else if (draft.kind === "cancel") await perform(() => api.orderAction(draft.orderId, { action: "cancel", expectedVersion: draft.expectedVersion, reason }, draft.idempotencyKey), true);
    else if (draft.kind === "requestRefund") {
      const amountMinor = parseMinor(draft.amount, draft.currency);
      if (!amountMinor || BigInt(amountMinor) <= 0n || (!editor.attempted && (!detail || BigInt(amountMinor) > BigInt(detail.order.refundableMinor)))) { changeDraft(editor); setWriteError(new PaymentApiError(400, "PAYMENT_AMOUNT_INVALID")); return; }
      await perform(() => api.orderAction(draft.orderId, { action: "requestRefund", expectedVersion: draft.expectedVersion, amountMinor, reason, ...(draft.ticketId.trim() ? { ticketId: draft.ticketId.trim() } : {}) }, draft.idempotencyKey), true);
    } else { const action = draft.kind === "refundRefresh" ? "refresh" : draft.kind; await perform(() => api.refundAction(draft.targetId, { action, expectedVersion: draft.expectedVersion, reason }, draft.idempotencyKey), true); }
  }
  function resumeDraft() {
    if (!savedDraft || !catalogRead.fresh || disabled) return;
    if (savedDraft.kind !== "create") updateUrl({ tab: ["approve", "reject", "refundRefresh"].includes(savedDraft.kind) ? "refunds" : "orders", orderId: savedDraft.orderId, refundId: ["approve", "reject", "refundRefresh"].includes(savedDraft.kind) ? savedDraft.targetId : null }, true);
    setDraftActorId(actorId); setEditor(savedDraft); setWriteError(undefined);
  }
  const polling = !editor && !busy && Boolean(detail && (["creating", "pending", "processing", "unknown"].includes(detail.order.status) || detail.refunds.some((item) => ["processing", "unknown"].includes(item.status))));
  useEffect(() => { if (!polling) return; const timer = window.setInterval(() => setRevision((value) => value + 1), 10000); return () => window.clearInterval(timer); }, [polling]);

  const ledgerTrail = useMemo(() => {
    try { const value: unknown = JSON.parse(new URLSearchParams(search).get("ledgerTrail") ?? "[]"); return Array.isArray(value) && value.length <= 100 && value.every((item) => typeof item === "string") ? value : []; } catch { return []; }
  }, [search]);
  function ledgerPage(next: boolean) {
    if (!billingRead.fresh || disabled) return;
    if (next && billing?.ledger.nextToken) updateUrl({ pageToken: billing.ledger.nextToken, ledgerTrail: JSON.stringify([...ledgerTrail, params.get("pageToken") ?? ""]) });
    else if (!next) updateUrl({ pageToken: ledgerTrail.at(-1) ?? null, ledgerTrail: ledgerTrail.length > 1 ? JSON.stringify(ledgerTrail.slice(0, -1)) : null });
  }
  function applyFilters(values: Record<string, string>) {
    const reset = tab === "ledger" ? { pageToken: null, ledgerTrail: null } : { [tab === "orders" ? "orderOffset" : "refundOffset"]: null };
    updateUrl({ ...values, ...reset });
  }
  const title = mode === "admin" ? initialTab === "refunds" ? "refundsTitle" : "ordersTitle" : "title";
  return <main className="xj-payments-page">
    <header className="xj-payments-heading"><div><h1>{t(title)}</h1><p className="xj-payments-muted">{t(mode === "admin" ? "adminSubtitle" : "subtitle")}</p></div><div className="xj-payments-actions"><button className={paymentButton} type="button" disabled={disabled || readBusy} onClick={refresh}><RefreshCw size={14} />{t("refresh")}</button><button className={paymentButton + " xj-payments-button--primary"} type="button" disabled={disabled || !canManage || !catalog?.configured || !catalog.offers.length || !catalog.channels.length || Boolean(savedDraft)} onClick={() => startTopUp()}><Wallet size={14} />{t("topUp")}</button></div></header>
    <BillingMetrics billing={billing} />
    {mode === "admin" && initialTab === "refunds" ? <div className="xj-payments-actions"><button type="button" className={paymentButton} disabled={disabled} onClick={() => updateUrl({ legacy: "compensation" })}>{t("compensationLink")}</button></div> : null}
    {catalogRead.error ? <PaymentError error={catalogRead.error} retry={disabled ? undefined : refresh} /> : null}
    {catalog && !catalog.configured && tab !== "ledger" ? <p className="xj-payments-notice">{t("paymentUnavailable")}{catalog.reasonCode ? <span className="xj-payments-code">{t(`reasonCode.${catalog.reasonCode}`, { defaultValue: catalog.reasonCode })}</span> : null}</p> : null}
    {billingRead.error ? <PaymentError error={billingRead.error} retry={disabled ? undefined : refresh} /> : null}
    {!editor && savedDraft ? <div className="xj-payments-notice"><p>{t("draftAvailable")}</p>{savedDraft.attempted ? <p>{t("pendingIntentHelp")}</p> : null}<div className="xj-payments-actions"><button type="button" className={paymentButton} disabled={busy || !catalogRead.fresh} onClick={resumeDraft}>{t("resumeDraft")}</button><button type="button" className={paymentButton} disabled={busy} onClick={() => { if (actorId) savePaymentDraft(actorId, workspaceId); bumpDraft((value) => value + 1); }}>{t("discardDraft")}</button></div></div> : null}
    {writeError ? <PaymentError error={writeError} retry={editor ? undefined : refresh} /> : null}
    {saved ? <p className="xj-payments-notice xj-payments-notice--success" role="status">{t("saved")}</p> : null}
    {editor ? <PaymentForm draft={editor} catalog={catalog} busy={busy} enabled={editorEnabled} onChange={changeDraft} onSave={submitDraft} onCancel={closeDraft} /> : null}
    <nav className="xj-payments-tabs" aria-label={t("details")}>{(["ledger", "orders", "refunds"] as const).map((value) => <button type="button" key={value} className="xj-payments-tab" aria-current={tab === value ? "page" : undefined} disabled={disabled} onClick={() => updateUrl({ tab: value })}>{value === "ledger" ? <ReceiptText size={14} aria-hidden="true" /> : value === "orders" ? <CreditCard size={14} aria-hidden="true" /> : null}{t(`${value}Tab`)}</button>)}</nav>
    <div className="xj-payments-layout"><div className="xj-payments-main">
      {tab === "ledger" ? <BillingTrend billing={billing} /> : null}
      <section className={paymentPanel}><div className="xj-payments-toolbar"><h2 className="xj-payments-section-title">{t(`${tab}Tab`)}</h2><span className="xj-payments-muted">{t("utc")}</span></div>
        <PaymentFilters key={`${tab}:${tab === "ledger" ? ledgerKey : listKey}`} tab={tab} search={search} disabled={disabled} onApply={applyFilters} />
        {tab === "ledger" ? <><LedgerTable billing={billing} loading={billingRead.loading} locale={locale} />{billing ? <div className="xj-payments-pagination"><span>{t("ledgerPage", { count: billing.ledger.items.length })}</span><div className="xj-payments-actions"><button type="button" className={paymentButton} disabled={disabled || !billingRead.fresh || !params.get("pageToken")} onClick={() => ledgerPage(false)}>{t("previous")}</button><button type="button" className={paymentButton} disabled={disabled || !billingRead.fresh || !billing.ledger.nextToken || ledgerTrail.length >= 100} onClick={() => ledgerPage(true)}>{t("next")}</button></div></div> : null}</> : <>
          {listRead.error ? <PaymentError error={listRead.error} retry={disabled ? undefined : refresh} /> : null}
          <PaymentTable page={page} tab={tab} loading={listRead.loading} selected={tab === "orders" ? orderId : refundId} disabled={disabled} onSelect={(item) => { if (tab === "refunds") showRefund(item as PaymentRefund); else updateUrl({ orderId: item.id, refundId: null }); }} />
          {page ? <div className="xj-payments-pagination"><span>{t("page", { total: page.total, page: Math.floor(page.offset / page.limit) + 1 })}</span><div className="xj-payments-actions"><button type="button" className={paymentButton} disabled={disabled || !listRead.fresh || page.offset === 0} onClick={() => updateUrl({ [tab === "orders" ? "orderOffset" : "refundOffset"]: String(Math.max(0, page.offset - page.limit)) })}>{t("previous")}</button><button type="button" className={paymentButton} disabled={disabled || !listRead.fresh || page.offset + page.limit >= page.total} onClick={() => updateUrl({ [tab === "orders" ? "orderOffset" : "refundOffset"]: String(page.offset + page.limit) })}>{t("next")}</button></div></div> : null}</>}
      </section>
      {tab === "ledger" && billing?.coverage.notes.length ? <div className="xj-payments-notice">{billing.coverage.notes.map((code) => <p key={code}>{t(`coverage.${code}`, { defaultValue: code })}</p>)}</div> : null}
      {tab !== "ledger" && detail ? <PaymentHistory detail={detail} /> : null}
    </div><aside className="xj-payments-aside">
      {tab === "ledger" ? <BillingAside billing={billing} catalog={catalogRead.fresh ? catalog : undefined} disabled={disabled || !canManage || Boolean(savedDraft)} onTopUp={startTopUp} /> : <>
        {detailRead.error ? <PaymentError error={detailRead.error} retry={disabled ? undefined : refresh} /> : null}
        {detailRead.loading ? <p className="xj-payments-empty" role="status">{t("loading")}</p> : null}
        {selectionMissing ? <PaymentError error={new PaymentApiError(404, "PAYMENT_REFUND_NOT_FOUND")} /> : null}
        {selectedRefund && tab === "refunds" ? <RefundInspector refund={selectedRefund} disabled={disabled || !detailWritable || Boolean(savedDraft)} onAction={refundAction} /> : null}
        {detail ? <OrderInspector detail={detail} disabled={disabled || !detailWritable} mutationDisabled={Boolean(savedDraft)} onAction={orderAction} onRefund={showRefund} /> : !orderId ? <section className={paymentPanel}><p className="xj-payments-empty">{t(tab === "refunds" ? "selectRefund" : "selectOrder")}</p></section> : null}
      </>}
    </aside></div>
  </main>;
}

function LedgerTable({ billing, loading, locale }: { billing?: BillingWorkspace; loading: boolean; locale: string }) {
  const { t } = usePaymentTranslation();
  if (!billing) return <p className="xj-payments-empty" role="status">{t(loading ? "loading" : "emptyLedger")}</p>;
  if (!billing.ledger.items.length) return <p className="xj-payments-empty">{t("emptyLedger")}</p>;
  return <div className="xj-payments-table-wrap"><table className="xj-payments-table"><thead><tr>{["occurred", "type", "source", "project", "reference", "balanceDelta"].map((key) => <th key={key} scope="col">{t(key)}</th>)}</tr></thead><tbody>{billing.ledger.items.map((item) => <tr key={`${item.source}:${item.id}`}><td>{dateLabel(item.occurredAt, locale)}</td><td>{t(`actionName.${item.action}`, { defaultValue: item.action })}<span className="xj-payments-code">{item.id}</span></td><td>{t(`sourceName.${item.source}`, { defaultValue: item.source })}</td><td>{item.projectId ?? "—"}<span className="xj-payments-code">{item.taskId ? `${t("task")}: ${item.taskId}` : ""}</span></td><td>{item.reference || "—"}</td><td className={`xj-payments-amount ${item.availableDeltaMinor?.startsWith("-") ? "xj-payments-negative" : item.availableDeltaMinor && BigInt(item.availableDeltaMinor) > 0n ? "xj-payments-positive" : ""}`}>{money(item.availableDeltaMinor, item.currency, locale)}<span className="xj-payments-code">{t("ledgerAmount")}: {money(item.amountMinor, item.currency, locale)}</span>{item.availableDeltaMinor === null ? <span className="xj-payments-code">{t("unknownDirection")}</span> : null}</td></tr>)}</tbody></table></div>;
}
function PaymentTable({ page, tab, loading, selected, disabled, onSelect }: { page?: PaymentPage<PaymentOrder | PaymentRefund>; tab: "orders" | "refunds"; loading: boolean; selected: string; disabled: boolean; onSelect: (item: PaymentOrder | PaymentRefund) => void }) {
  const { t, i18n } = usePaymentTranslation();
  if (!page) return <p className="xj-payments-empty" role="status">{t(loading ? "loading" : tab === "orders" ? "emptyOrders" : "emptyRefunds")}</p>;
  if (!page.items.length) return <p className="xj-payments-empty">{t(tab === "orders" ? "emptyOrders" : "emptyRefunds")}</p>;
  return <div className="xj-payments-table-wrap"><table className="xj-payments-table"><thead><tr>{[tab === "orders" ? "orderId" : "refundId", "amount", tab === "orders" ? "channel" : "requestedBy", "status", "created", "details"].map((key) => <th key={key} scope="col">{t(key)}</th>)}</tr></thead><tbody>{page.items.map((item) => <tr key={item.id} aria-selected={selected === item.id}><td><button type="button" disabled={disabled} onClick={() => onSelect(item)}>{item.id}</button>{"orderId" in item ? <span className="xj-payments-code">{t("orderId")}: {item.orderId}</span> : <span className="xj-payments-code">{item.offerId}</span>}</td><td className="xj-payments-amount">{money(item.amountMinor, item.currency, i18n.resolvedLanguage ?? "zh")}</td><td>{"channel" in item ? item.channel : item.requestedBy}</td><td><PaymentStatus status={item.status} refund={tab === "refunds"} /></td><td>{dateLabel(item.createdAt, i18n.resolvedLanguage ?? "zh")}</td><td><button type="button" disabled={disabled} onClick={() => onSelect(item)}>{t("details")}</button></td></tr>)}</tbody></table></div>;
}

function PaymentFilters({ tab, search, disabled, onApply }: { tab: PaymentTab; search: string; disabled: boolean; onApply: (values: Record<string, string>) => void }) {
  const { t } = usePaymentTranslation();
  const params = new URLSearchParams(search), prefix = tab === "ledger" ? "ledger" : tab === "orders" ? "order" : "refund";
  const [q, setQ] = useState(params.get(`${prefix}Q`) ?? "");
  const [status, setStatus] = useState(params.get(`${prefix}Status`) ?? "");
  const [source, setSource] = useState(params.get("ledgerSource") ?? "");
  const [projectId, setProjectId] = useState(params.get("projectId") ?? "");
  const [from, setFrom] = useState((params.get("dateFrom") ?? "").replace(/Z$/, "").slice(0, 16));
  const [to, setTo] = useState((params.get("dateTo") ?? "").replace(/Z$/, "").slice(0, 16));
  const [invalid, setInvalid] = useState(false);
  function apply(event: FormEvent) { event.preventDefault(); if (disabled) return; if (tab === "ledger" && from && to && from >= to) { setInvalid(true); return; } setInvalid(false); onApply({ [`${prefix}Q`]: q.trim(), [`${prefix}Status`]: status, ...(tab === "ledger" ? { ledgerSource: source, projectId: projectId.trim(), dateFrom: from ? `${from}:00Z` : "", dateTo: to ? `${to}:00Z` : "" } : {}) }); }
  const states = tab === "ledger" ? ledgerActions : tab === "orders" ? orderStates : refundStates;
  return <form onSubmit={apply}><fieldset className="xj-payments-filters" disabled={disabled}><label className="xj-payments-label xj-payments-search">{t("search")}<input className={paymentField} value={q} maxLength={160} onChange={(event) => setQ(event.target.value)} placeholder={t("search")} /></label><label className="xj-payments-label">{t("status")}<select className={paymentField} value={status} onChange={(event) => setStatus(event.target.value)}><option value="">{t("all")}</option>{status && !states.includes(status) ? <option value={status}>{status}</option> : null}{states.map((state) => <option key={state} value={state}>{t(`${tab === "ledger" ? "actionName" : tab === "orders" ? "orderState" : "refundState"}.${state}`, { defaultValue: state })}</option>)}</select></label>
    {tab === "ledger" ? <><label className="xj-payments-label">{t("source")}<select className={paymentField} value={source} onChange={(event) => setSource(event.target.value)}><option value="">{t("all")}</option>{["generation", "audio", "editing", "payment", "refund"].map((value) => <option value={value} key={value}>{t(`sourceName.${value}`, { defaultValue: value })}</option>)}</select></label><label className="xj-payments-label">{t("project")}<input className={paymentField} value={projectId} maxLength={128} onChange={(event) => setProjectId(event.target.value)} /></label><label className="xj-payments-label">{t("dateFrom")} · UTC<input type="datetime-local" className={paymentField} value={from} onChange={(event) => setFrom(event.target.value)} /></label><label className="xj-payments-label">{t("dateTo")} · UTC<input type="datetime-local" className={paymentField} value={to} onChange={(event) => setTo(event.target.value)} /></label></> : null}
    <button type="submit" className={paymentButton}>{t("searchAction")}</button><button type="button" className={paymentButton} onClick={() => onApply({ [`${prefix}Q`]: "", [`${prefix}Status`]: "", ...(tab === "ledger" ? { ledgerSource: "", projectId: "", dateFrom: "", dateTo: "" } : {}) })}>{t("clear")}</button></fieldset>{invalid ? <p className="xj-payments-notice" role="alert">{t("invalidDate")}</p> : null}</form>;
}
